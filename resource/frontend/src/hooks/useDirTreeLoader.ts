import { useState, useCallback, useRef } from 'react';
import {
  BlobReader,
  BlobWriter,
  ZipReader,
} from '@zip.js/zip.js';
import type { DirEntry, DirTreeData, DirIndex, DirTreeLoaderState } from '../types';

const MAX_DEPTH = 3; // Default depth limit
const CHUNK_SIZE = 10 * 1024 * 1024; // 10MB chunks

interface ParsedLine {
  path: string;
  size: number;
  isDirectory: boolean;
}

/**
 * Parse a single line from `find -ls` output
 * Format: inode blocks permissions links owner group size month day time/year path
 */
function parseLine(line: string): ParsedLine | null {
  // Match the find -ls output format
  const match = line.match(
    /^\s*\d+\s+\d+\s+([d-])[rwx-]{9}\s+\d+\s+\S+\s+\S+\s+(\d+)\s+\w+\s+\d+\s+[\d:]+\s+(.+)$/
  );

  if (match) {
    return {
      path: match[3].trim(),
      size: parseInt(match[2], 10),
      isDirectory: match[1] === 'd',
    };
  }

  // Try alternative format
  const altMatch = line.match(/^\s*\d+\s+\d+\s+([d-])\S+\s+\d+\s+\S+\s+\S+\s+(\d+)\s+.+?\s+(.+)$/);
  if (altMatch) {
    return {
      path: altMatch[3].trim(),
      size: parseInt(altMatch[2], 10),
      isDirectory: altMatch[1] === 'd',
    };
  }

  return null;
}

/**
 * Normalize path by removing trailing slash (except for root "/")
 */
function normalizePath(path: string): string {
  if (path === '/') return path;
  return path.replace(/\/+$/, '');
}

function getBaseName(path: string): string {
  const normalized = normalizePath(path);
  const parts = normalized.split('/');
  return parts[parts.length - 1] || path;
}

function getParentPath(path: string): string {
  const normalized = normalizePath(path);
  const lastSlash = normalized.lastIndexOf('/');
  if (lastSlash <= 0) return '/';
  return normalized.substring(0, lastSlash);
}

function getDepth(path: string, rootPath: string): number {
  const normalizedPath = normalizePath(path);
  const normalizedRoot = normalizePath(rootPath);
  const rootDepth = normalizedRoot.split('/').filter(Boolean).length;
  const pathDepth = normalizedPath.split('/').filter(Boolean).length;
  return pathDepth - rootDepth;
}

function getAncestorAtDepth(path: string, rootPath: string, targetDepth: number): string {
  const normalizedPath = normalizePath(path);
  const normalizedRoot = normalizePath(rootPath);
  const parts = normalizedPath.split('/');
  const rootParts = normalizedRoot.split('/').filter(Boolean).length;
  // +1 because absolute paths have empty string at index 0 after split
  const targetParts = rootParts + targetDepth + 1;
  return parts.slice(0, targetParts).join('/');
}

export interface UseDirTreeLoaderReturn {
  state: DirTreeLoaderState;
  loadFromBlob: (blob: Blob) => Promise<DirTreeData | null>;
  loadFromStream: (stream: ReadableStream<Uint8Array>, totalSize: number, file: File) => Promise<DirTreeData | null>;
  expandDirectory: (dirPath: string, newMaxDepth?: number) => Promise<DirEntry | null>;
  reset: () => void;
}

export function useDirTreeLoader(): UseDirTreeLoaderReturn {
  const [state, setState] = useState<DirTreeLoaderState>({
    isLoading: false,
    progress: 0,
    progressText: '',
    error: null,
    tree: null,
    index: new Map(),
  });

  // Store references for drill-down
  const blobRef = useRef<Blob | null>(null);
  const fileRef = useRef<File | null>(null);
  const rootPathRef = useRef<string>('');

  const reset = useCallback(() => {
    blobRef.current = null;
    fileRef.current = null;
    rootPathRef.current = '';
    setState({
      isLoading: false,
      progress: 0,
      progressText: '',
      error: null,
      tree: null,
      index: new Map(),
    });
  }, []);

  /**
   * Load directory tree from a blob with depth-limited parsing
   */
  const loadFromBlob = useCallback(async (blob: Blob): Promise<DirTreeData | null> => {
    blobRef.current = blob;
    const totalBytes = blob.size;

    setState(prev => ({
      ...prev,
      isLoading: true,
      progress: 0,
      progressText: 'Starting parsing...',
      error: null,
    }));

    try {
      // Node map for building tree (only stores nodes at depth <= MAX_DEPTH)
      const nodeMap = new Map<string, DirEntry>();
      // Byte-offset index for directories at depth == MAX_DEPTH
      const dirIndex = new Map<string, DirIndex>();

      // Track current byte position and current directory being indexed
      let currentByte = 0;
      let buffer = '';
      let rootPath = '';
      let linesProcessed = 0;
      const startTime = performance.now();

      // Track the current MAX_DEPTH directory for byte indexing
      // (only one at a time since find output is depth-first)
      let activeIndexDir: { path: string; startByte: number; size: number; fileCount: number } | null = null;

      // Process blob in chunks
      let offset = 0;
      while (offset < totalBytes) {
        const chunkEnd = Math.min(offset + CHUNK_SIZE, totalBytes);
        const chunk = blob.slice(offset, chunkEnd);
        const text = await chunk.text();
        buffer += text;

        // Process complete lines
        const lines = buffer.split('\n');
        buffer = lines.pop() || ''; // Keep incomplete line in buffer

        for (const line of lines) {
          // Use string length as byte approximation (accurate for ASCII, close enough for UTF-8 paths)
          const lineBytes = line.length + 1; // +1 for newline

          if (!line.trim()) {
            currentByte += lineBytes;
            continue;
          }

          const parsed = parseLine(line);
          if (!parsed) {
            currentByte += lineBytes;
            continue;
          }

          linesProcessed++;
          const { path: rawPath, size, isDirectory } = parsed;
          const path = normalizePath(rawPath);

          // Detect root path from first entry
          if (!rootPath) {
            rootPath = path;
            rootPathRef.current = rootPath;

            // Create root node
            const rootNode: DirEntry = {
              name: getBaseName(rootPath) || rootPath,
              path: rootPath,
              size: size,
              ownSize: size,
              isDirectory: true,
              children: [],
              fileCount: 0,
              depth: 0,
              hasHiddenChildren: false,
            };
            nodeMap.set(rootPath, rootNode);
            currentByte += lineBytes;
            continue;
          }

          const depth = getDepth(path, rootPath);

          // Close active index dir if we've left its subtree
          if (activeIndexDir && !path.startsWith(activeIndexDir.path + '/') && path !== activeIndexDir.path) {
            dirIndex.set(activeIndexDir.path, {
              path: activeIndexDir.path,
              startByte: activeIndexDir.startByte,
              endByte: currentByte,
              totalSize: activeIndexDir.size,
              fileCount: activeIndexDir.fileCount,
              depth: MAX_DEPTH,
            });
            activeIndexDir = null;
          }

          // Aggregate into active index dir if we're deeper than MAX_DEPTH
          if (depth > MAX_DEPTH && activeIndexDir) {
            activeIndexDir.size += size;
            if (!isDirectory) activeIndexDir.fileCount++;
          }

          if (depth <= MAX_DEPTH) {
            // Create actual node
            const node: DirEntry = {
              name: getBaseName(path),
              path: path,
              size: size,
              ownSize: size,
              isDirectory: isDirectory,
              children: [],
              fileCount: isDirectory ? 0 : 1,
              depth: depth,
              hasHiddenChildren: false,
            };
            nodeMap.set(path, node);

            // Find parent and add as child
            const parentPath = getParentPath(path);
            const parent = nodeMap.get(parentPath);
            if (parent) {
              parent.children.push(node);
            }

            // Start tracking byte index for directories at MAX_DEPTH
            if (isDirectory && depth === MAX_DEPTH) {
              activeIndexDir = {
                path: path,
                startByte: currentByte,
                size: size,
                fileCount: 0,
              };
              node.hasHiddenChildren = true; // Will have hidden children
            }
          } else {
            // depth > MAX_DEPTH: Aggregate into nearest visible ancestor
            const ancestorPath = getAncestorAtDepth(path, rootPath, MAX_DEPTH);
            const ancestor = nodeMap.get(ancestorPath);
            if (ancestor) {
              ancestor.size += size;
              if (!isDirectory) {
                ancestor.fileCount++;
              }
            }
          }

          currentByte += lineBytes;
        }

        offset = chunkEnd;
        const progress = Math.round((offset / totalBytes) * 100);
        const elapsedSec = (performance.now() - startTime) / 1000;
        const entriesPerSec = elapsedSec > 0 ? Math.round(linesProcessed / elapsedSec) : 0;
        setState(prev => ({
          ...prev,
          progress,
          progressText: `Processing... ${progress}% (${linesProcessed.toLocaleString()} entries, ${entriesPerSec.toLocaleString()}/sec)`,
        }));

        // Yield to UI every chunk
        await new Promise(resolve => setTimeout(resolve, 0));
      }

      // Process remaining buffer
      if (buffer.trim()) {
        const parsed = parseLine(buffer);
        if (parsed) {
          const { path: rawPath, size, isDirectory } = parsed;
          const path = normalizePath(rawPath);
          const depth = getDepth(path, rootPath);

          if (depth <= MAX_DEPTH && path !== rootPath) {
            const node: DirEntry = {
              name: getBaseName(path),
              path: path,
              size: size,
              ownSize: size,
              isDirectory: isDirectory,
              children: [],
              fileCount: isDirectory ? 0 : 1,
              depth: depth,
              hasHiddenChildren: false,
            };
            nodeMap.set(path, node);

            const parentPath = getParentPath(path);
            const parent = nodeMap.get(parentPath);
            if (parent) {
              parent.children.push(node);
            }
          }
        }
      }

      // Close remaining active index directory
      if (activeIndexDir) {
        dirIndex.set(activeIndexDir.path, {
          path: activeIndexDir.path,
          startByte: activeIndexDir.startByte,
          endByte: currentByte,
          totalSize: activeIndexDir.size,
          fileCount: activeIndexDir.fileCount,
          depth: MAX_DEPTH,
        });
      }

      // Calculate cumulative sizes (bottom-up)
      const root = nodeMap.get(rootPath);
      if (root) {
        calculateCumulativeSizes(root);

        const treeData: DirTreeData = {
          root,
          totalSize: root.size,
          totalFiles: root.fileCount,
          rootPath,
        };

        setState({
          isLoading: false,
          progress: 100,
          progressText: `Loaded ${linesProcessed.toLocaleString()} entries`,
          error: null,
          tree: treeData,
          index: dirIndex,
        });

        return treeData;
      }

      setState(prev => ({
        ...prev,
        isLoading: false,
        error: 'Failed to build directory tree',
      }));
      return null;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Unknown error';
      setState(prev => ({
        ...prev,
        isLoading: false,
        error: errorMessage,
      }));
      return null;
    }
  }, []);

  /**
   * Load directory tree from a ReadableStream (memory efficient for large files)
   */
  const loadFromStream = useCallback(async (
    stream: ReadableStream<Uint8Array>,
    totalSize: number,
    file: File
  ): Promise<DirTreeData | null> => {
    fileRef.current = file;

    setState(prev => ({
      ...prev,
      isLoading: true,
      progress: 0,
      progressText: 'Starting streaming parse...',
      error: null,
    }));

    try {
      const nodeMap = new Map<string, DirEntry>();
      const dirIndex = new Map<string, DirIndex>();

      let currentByte = 0;
      let buffer = '';
      let rootPath = '';
      let linesProcessed = 0;
      const startTime = performance.now();

      let activeIndexDir: { path: string; startByte: number; size: number; fileCount: number } | null = null;

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const textStream = (stream as any).pipeThrough(new TextDecoderStream());
      const reader = textStream.getReader() as ReadableStreamDefaultReader<string>;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += value;

        // Process complete lines
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const lineBytes = line.length + 1;

          if (!line.trim()) {
            currentByte += lineBytes;
            continue;
          }

          const parsed = parseLine(line);
          if (!parsed) {
            currentByte += lineBytes;
            continue;
          }

          linesProcessed++;
          const { path: rawPath, size, isDirectory } = parsed;
          const path = normalizePath(rawPath);

          if (!rootPath) {
            rootPath = path;
            rootPathRef.current = rootPath;

            const rootNode: DirEntry = {
              name: getBaseName(rootPath) || rootPath,
              path: rootPath,
              size: size,
              ownSize: size,
              isDirectory: true,
              children: [],
              fileCount: 0,
              depth: 0,
              hasHiddenChildren: false,
            };
            nodeMap.set(rootPath, rootNode);
            currentByte += lineBytes;
            continue;
          }

          const depth = getDepth(path, rootPath);

          if (activeIndexDir && !path.startsWith(activeIndexDir.path + '/') && path !== activeIndexDir.path) {
            dirIndex.set(activeIndexDir.path, {
              path: activeIndexDir.path,
              startByte: activeIndexDir.startByte,
              endByte: currentByte,
              totalSize: activeIndexDir.size,
              fileCount: activeIndexDir.fileCount,
              depth: MAX_DEPTH,
            });
            activeIndexDir = null;
          }

          if (depth > MAX_DEPTH && activeIndexDir) {
            activeIndexDir.size += size;
            if (!isDirectory) activeIndexDir.fileCount++;
          }

          if (depth <= MAX_DEPTH) {
            const node: DirEntry = {
              name: getBaseName(path),
              path: path,
              size: size,
              ownSize: size,
              isDirectory: isDirectory,
              children: [],
              fileCount: isDirectory ? 0 : 1,
              depth: depth,
              hasHiddenChildren: false,
            };
            nodeMap.set(path, node);

            const parentPath = getParentPath(path);
            const parent = nodeMap.get(parentPath);
            if (parent) {
              parent.children.push(node);
            }

            if (isDirectory && depth === MAX_DEPTH) {
              activeIndexDir = {
                path: path,
                startByte: currentByte,
                size: size,
                fileCount: 0,
              };
              node.hasHiddenChildren = true;
            }
          } else {
            const ancestorPath = getAncestorAtDepth(path, rootPath, MAX_DEPTH);
            const ancestor = nodeMap.get(ancestorPath);
            if (ancestor) {
              ancestor.size += size;
              if (!isDirectory) {
                ancestor.fileCount++;
              }
            }
          }

          currentByte += lineBytes;
        }

        // Update progress
        const progress = Math.round((currentByte / totalSize) * 100);
        const elapsedSec = (performance.now() - startTime) / 1000;
        const entriesPerSec = elapsedSec > 0 ? Math.round(linesProcessed / elapsedSec) : 0;
        setState(prev => ({
          ...prev,
          progress,
          progressText: `Processing... ${progress}% (${linesProcessed.toLocaleString()} entries, ${entriesPerSec.toLocaleString()}/sec)`,
        }));

        // Yield to UI
        await new Promise(resolve => setTimeout(resolve, 0));
      }

      // Process remaining buffer
      if (buffer.trim()) {
        const parsed = parseLine(buffer);
        if (parsed) {
          const { path: rawPath, size, isDirectory } = parsed;
          const path = normalizePath(rawPath);
          const depth = getDepth(path, rootPath);

          if (depth <= MAX_DEPTH && path !== rootPath) {
            const node: DirEntry = {
              name: getBaseName(path),
              path: path,
              size: size,
              ownSize: size,
              isDirectory: isDirectory,
              children: [],
              fileCount: isDirectory ? 0 : 1,
              depth: depth,
              hasHiddenChildren: false,
            };
            nodeMap.set(path, node);

            const parentPath = getParentPath(path);
            const parent = nodeMap.get(parentPath);
            if (parent) {
              parent.children.push(node);
            }
          }
        }
      }

      if (activeIndexDir) {
        dirIndex.set(activeIndexDir.path, {
          path: activeIndexDir.path,
          startByte: activeIndexDir.startByte,
          endByte: currentByte,
          totalSize: activeIndexDir.size,
          fileCount: activeIndexDir.fileCount,
          depth: MAX_DEPTH,
        });
      }

      const root = nodeMap.get(rootPath);
      if (root) {
        calculateCumulativeSizes(root);

        const treeData: DirTreeData = {
          root,
          totalSize: root.size,
          totalFiles: root.fileCount,
          rootPath,
        };

        setState({
          isLoading: false,
          progress: 100,
          progressText: `Loaded ${linesProcessed.toLocaleString()} entries`,
          error: null,
          tree: treeData,
          index: dirIndex,
        });

        return treeData;
      }

      setState(prev => ({
        ...prev,
        isLoading: false,
        error: 'Failed to build directory tree',
      }));
      return null;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Unknown error';
      setState(prev => ({
        ...prev,
        isLoading: false,
        error: errorMessage,
      }));
      return null;
    }
  }, []);

  /**
   * Expand a directory by re-parsing its byte range with a deeper depth limit
   */
  const expandDirectory = useCallback(async (
    rawDirPath: string,
    newMaxDepth: number = MAX_DEPTH + 2
  ): Promise<DirEntry | null> => {
    const dirPath = normalizePath(rawDirPath);
    const blob = blobRef.current;
    const file = fileRef.current;
    const index = state.index.get(dirPath);
    const rootPath = rootPathRef.current;

    if ((!blob && !file) || !index || !rootPath) {
      return null;
    }

    setState(prev => ({
      ...prev,
      isLoading: true,
      progressText: `Expanding ${getBaseName(dirPath)}...`,
    }));

    try {
      let content: string;

      if (blob) {
        // Fast path: we have the blob in memory, just slice it
        const slice = blob.slice(index.startByte, index.endByte);
        content = await slice.text();
      } else if (file) {
        // Streaming mode: re-extract from zip and get the byte range
        const zipReader = new ZipReader(new BlobReader(file));
        const entries = await zipReader.getEntries();
        const dirListingEntry = entries.find(
          (e) => e.filename === 'datadir_listing.txt' || e.filename.endsWith('/datadir_listing.txt')
        );

        if (!dirListingEntry) {
          await zipReader.close();
          throw new Error('datadir_listing.txt not found');
        }

        // Extract just the needed byte range by streaming and collecting only that portion
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const fullBlob = await (dirListingEntry as any).getData(new BlobWriter('text/plain'));
        const slice = fullBlob.slice(index.startByte, index.endByte);
        content = await slice.text();

        await zipReader.close();
      } else {
        throw new Error('No data source available');
      }
      const lines = content.trim().split('\n');

      // Build subtree with new depth limit (relative to this dir)
      const dirDepth = getDepth(dirPath, rootPath);
      const effectiveMaxDepth = dirDepth + newMaxDepth;

      const nodeMap = new Map<string, DirEntry>();

      // Create the root of this subtree (the dir we're expanding)
      const rootNode: DirEntry = {
        name: getBaseName(dirPath),
        path: dirPath,
        size: 0,
        ownSize: 0,
        isDirectory: true,
        children: [],
        fileCount: 0,
        depth: dirDepth,
        hasHiddenChildren: false,
      };
      nodeMap.set(dirPath, rootNode);

      for (const line of lines) {
        if (!line.trim()) continue;

        const parsed = parseLine(line);
        if (!parsed) continue;

        const { path: rawPath, size, isDirectory } = parsed;
        const path = normalizePath(rawPath);

        // Skip entries not under this directory
        if (!path.startsWith(dirPath + '/') && path !== dirPath) continue;

        const depth = getDepth(path, rootPath);

        if (path === dirPath) {
          rootNode.ownSize = size;
          continue;
        }

        if (depth <= effectiveMaxDepth) {
          const node: DirEntry = {
            name: getBaseName(path),
            path: path,
            size: size,
            ownSize: size,
            isDirectory: isDirectory,
            children: [],
            fileCount: isDirectory ? 0 : 1,
            depth: depth,
            hasHiddenChildren: depth === effectiveMaxDepth && isDirectory,
          };
          nodeMap.set(path, node);

          // Find parent and add as child
          const parentPath = getParentPath(path);
          const parent = nodeMap.get(parentPath);
          if (parent) {
            parent.children.push(node);
          }
        } else {
          // Aggregate into nearest visible ancestor
          const ancestorPath = getAncestorAtDepth(path, rootPath, effectiveMaxDepth);
          const ancestor = nodeMap.get(ancestorPath);
          if (ancestor) {
            ancestor.size += size;
            if (!isDirectory) {
              ancestor.fileCount++;
            }
          }
        }
      }

      // Calculate cumulative sizes
      calculateCumulativeSizes(rootNode);

      setState(prev => ({
        ...prev,
        isLoading: false,
        progressText: '',
      }));

      return rootNode;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Unknown error';
      setState(prev => ({
        ...prev,
        isLoading: false,
        error: errorMessage,
      }));
      return null;
    }
  }, [state.index]);

  return {
    state,
    loadFromBlob,
    loadFromStream,
    expandDirectory,
    reset,
  };
}

/**
 * Calculate cumulative sizes bottom-up and sort children by size
 *
 * For nodes with hasHiddenChildren=true:
 * - node.size already contains: ownSize + aggregated hidden children sizes
 * - node.fileCount already contains: aggregated hidden children file counts
 * - We just need to recurse into visible children (to calculate their sizes) and sort
 *
 * For nodes without hidden children:
 * - We calculate: ownSize + sum of all children sizes
 */
function calculateCumulativeSizes(node: DirEntry): { size: number; fileCount: number } {
  // Base case: files or empty directories
  if (!node.isDirectory) {
    return { size: node.size, fileCount: 1 };
  }

  if (node.children.length === 0) {
    // Directory with no visible children
    // If hasHiddenChildren, size and fileCount already have aggregated values
    return { size: node.size, fileCount: node.fileCount };
  }

  // Recurse into visible children first (this also sorts them)
  for (const child of node.children) {
    calculateCumulativeSizes(child);
  }

  // Sort children by size descending
  node.children.sort((a, b) => b.size - a.size);

  if (node.hasHiddenChildren) {
    // node.size and node.fileCount already include hidden children's contributions
    // (aggregated during parsing). Just return what we have.
    return { size: node.size, fileCount: node.fileCount };
  }

  // No hidden children - calculate from visible children
  let totalSize = node.ownSize;
  let totalFiles = 0;

  for (const child of node.children) {
    totalSize += child.size;
    totalFiles += child.fileCount;
  }

  node.size = totalSize;
  node.fileCount = totalFiles;

  return { size: node.size, fileCount: node.fileCount };
}
