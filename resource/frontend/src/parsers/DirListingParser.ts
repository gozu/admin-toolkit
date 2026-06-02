import { BaseTextParser } from './BaseParser';
import type { DirEntry, DirTreeData } from '../types';

/**
 * Parser for datadir_listing.txt (output of find -ls)
 *
 * Format:
 * 69711934      8 drwx--x--x  70  dataiku  dataiku      4096 May  5 09:05 /data/dataiku/dss_data/
 * inode    blocks permissions links owner   group       size date         path
 */
export class DirListingParser extends BaseTextParser<DirTreeData> {
  processContent(content: string): DirTreeData {
    const lines = content.trim().split('\n').filter(line => line.trim());

    console.log(`[DirListingParser] Processing ${lines.length} lines`);

    if (lines.length === 0) {
      console.log('[DirListingParser] No lines to process');
      return { root: null, totalSize: 0, totalFiles: 0, rootPath: '' };
    }

    // Parse all entries
    const entries: Array<{
      path: string;
      size: number;
      isDirectory: boolean;
    }> = [];

    let parsedCount = 0;
    let failedCount = 0;

    for (const line of lines) {
      const parsed = this.parseLine(line);
      if (parsed) {
        entries.push(parsed);
        parsedCount++;
      } else {
        failedCount++;
        if (failedCount <= 3) {
          console.log(`[DirListingParser] Failed to parse line: "${line.substring(0, 100)}"`);
        }
      }
    }

    console.log(`[DirListingParser] Parsed ${parsedCount} entries, failed ${failedCount}`);

    if (entries.length === 0) {
      console.log('[DirListingParser] No entries parsed, returning null');
      return { root: null, totalSize: 0, totalFiles: 0, rootPath: '' };
    }

    // Find root path (shortest path that's a directory)
    const rootPath = this.findRootPath(entries);
    console.log(`[DirListingParser] Root path: "${rootPath}"`);

    // Build tree structure
    const root = this.buildTree(entries, rootPath);
    console.log(`[DirListingParser] Tree built, root children: ${root.children.length}`);

    // Calculate cumulative sizes
    this.calculateSizes(root);
    console.log(`[DirListingParser] Final: size=${root.size}, fileCount=${root.fileCount}`);

    return {
      root,
      totalSize: root.size,
      totalFiles: root.fileCount,
      rootPath,
    };
  }

  private parseLine(line: string): { path: string; size: number; isDirectory: boolean } | null {
    // Match the find -ls output format
    // inode blocks permissions links owner group size month day time/year path
    const match = line.match(
      /^\s*\d+\s+\d+\s+([d-])[rwx-]{9}\s+\d+\s+\S+\s+\S+\s+(\d+)\s+\w+\s+\d+\s+[\d:]+\s+(.+)$/
    );

    if (!match) {
      // Try alternative format without full permissions
      const altMatch = line.match(/^\s*\d+\s+\d+\s+([d-])\S+\s+\d+\s+\S+\s+\S+\s+(\d+)\s+.+?\s+(.+)$/);
      if (!altMatch) return null;

      return {
        path: altMatch[3].trim(),
        size: parseInt(altMatch[2], 10),
        isDirectory: altMatch[1] === 'd',
      };
    }

    return {
      path: match[3].trim(),
      size: parseInt(match[2], 10),
      isDirectory: match[1] === 'd',
    };
  }

  private findRootPath(entries: Array<{ path: string }>): string {
    const paths = entries.map(e => e.path).sort((a, b) => a.length - b.length);
    return paths[0] || '';
  }

  private buildTree(
    entries: Array<{ path: string; size: number; isDirectory: boolean }>,
    rootPath: string
  ): DirEntry {
    // Create a map for quick lookup
    const nodeMap = new Map<string, DirEntry>();

    // Sort entries by path to process parents before children
    const sortedEntries = [...entries].sort((a, b) => a.path.localeCompare(b.path));

    // Create root node
    const rootEntry = sortedEntries.find(e => e.path === rootPath);
    const rootSize = rootEntry?.size || 0;
    const root: DirEntry = {
      name: this.getBaseName(rootPath) || rootPath,
      path: rootPath,
      size: rootSize,
      ownSize: rootSize,
      isDirectory: true,
      children: [],
      fileCount: 0,
      depth: 0,
      hasHiddenChildren: false,
    };
    nodeMap.set(rootPath, root);

    // Process all entries
    for (const entry of sortedEntries) {
      if (entry.path === rootPath) continue;

      const node: DirEntry = {
        name: this.getBaseName(entry.path),
        path: entry.path,
        size: entry.size,
        ownSize: entry.size,
        isDirectory: entry.isDirectory,
        children: [],
        fileCount: entry.isDirectory ? 0 : 1,
        depth: this.getDepth(entry.path, rootPath),
        hasHiddenChildren: false,
      };

      nodeMap.set(entry.path, node);

      // Find parent and add as child
      const parentPath = this.getParentPath(entry.path);
      const parent = nodeMap.get(parentPath);
      if (parent) {
        parent.children.push(node);
      }
    }

    return root;
  }

  private calculateSizes(node: DirEntry): { size: number; fileCount: number } {
    if (!node.isDirectory || node.children.length === 0) {
      return { size: node.size, fileCount: node.isDirectory ? 0 : 1 };
    }

    let totalSize = node.size; // Directory's own size (usually 4096)
    let totalFiles = 0;

    for (const child of node.children) {
      const { size, fileCount } = this.calculateSizes(child);
      totalSize += size;
      totalFiles += fileCount;
    }

    node.size = totalSize;
    node.fileCount = totalFiles;

    // Sort children by size descending
    node.children.sort((a, b) => b.size - a.size);

    return { size: totalSize, fileCount: totalFiles };
  }

  private getBaseName(path: string): string {
    const parts = path.replace(/\/$/, '').split('/');
    return parts[parts.length - 1] || path;
  }

  private getParentPath(path: string): string {
    const normalized = path.replace(/\/$/, '');
    const lastSlash = normalized.lastIndexOf('/');
    if (lastSlash <= 0) return '/';
    return normalized.substring(0, lastSlash);
  }

  private getDepth(path: string, rootPath: string): number {
    const rootDepth = rootPath.split('/').filter(Boolean).length;
    const pathDepth = path.replace(/\/$/, '').split('/').filter(Boolean).length;
    return pathDepth - rootDepth;
  }
}
