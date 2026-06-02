import { useEffect, useCallback, useState, useRef } from 'react';
import { motion } from 'framer-motion';
import {
  BlobReader,
  ZipReader,
  type Entry,
} from '@zip.js/zip.js';
import { useDirTreeLoader } from '../hooks/useDirTreeLoader';
import { DirTreemap } from './DirTreemap';
import { DirTreeTable } from './DirTreeTable';
import type { DirEntry } from '../types';

interface DirTreeSectionProps {
  file: File;
}

// Delay after mount before starting to extract and parse (lets main UI render first)
const LOAD_DELAY_MS = 1000;

function formatSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

export function DirTreeSection({ file }: DirTreeSectionProps) {
  const { state, loadFromStream, expandDirectory } = useDirTreeLoader();
  const [expandedNodes, setExpandedNodes] = useState<Map<string, DirEntry>>(new Map());
  const [ready, setReady] = useState(false);
  const [started, setStarted] = useState(false);
  const [entrySize, setEntrySize] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const loadStartedRef = useRef(false);

  // Wait 1 second after mount before starting
  useEffect(() => {
    const timer = setTimeout(() => setReady(true), LOAD_DELAY_MS);
    return () => clearTimeout(timer);
  }, []);

  // Stream datadir_listing.txt directly from zip to parser (no blob in memory)
  useEffect(() => {
    if (!ready || started || loadError || loadStartedRef.current) return;

    const streamFromZip = async () => {
      loadStartedRef.current = true;
      setStarted(true);

      try {
        const reader = new BlobReader(file);
        const zipReader = new ZipReader(reader);
        const entries = await zipReader.getEntries();

        const dirListingEntry = entries.find(
          (e) => e.filename === 'datadir_listing.txt' || e.filename.endsWith('/datadir_listing.txt')
        );

        if (!dirListingEntry) {
          setLoadError('datadir_listing.txt not found in archive');
          await zipReader.close();
          return;
        }

        const totalSize = dirListingEntry.uncompressedSize;
        setEntrySize(totalSize);
        console.log(`[DirTreeSection] Streaming datadir_listing.txt: ${formatSize(totalSize)}`);

        // Get readable stream from entry - streams data without holding in memory
        const readable = (dirListingEntry as Entry & { readable: ReadableStream<Uint8Array> }).readable;

        if (readable) {
          // Use native readable stream
          await loadFromStream(readable, totalSize, file);
        } else {
          // Fallback: create a readable stream using getData with a TransformStream
          const { readable: streamReadable, writable } = new TransformStream<Uint8Array>();

          // Start extraction in background, piping to our transform stream
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const extractPromise = (dirListingEntry as any).getData(writable);

          // Process the readable side
          await loadFromStream(streamReadable, totalSize, file);
          await extractPromise;
        }

        await zipReader.close();
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Unknown error';
        setLoadError(msg);
        console.error('[DirTreeSection] Failed to stream:', err);
      }
    };

    streamFromZip();
  }, [ready, file, started, loadError, loadFromStream]);

  // Handle expand request from child components
  const handleExpand = useCallback(async (dirPath: string): Promise<DirEntry | null> => {
    const cached = expandedNodes.get(dirPath);
    if (cached) return cached;

    const expanded = await expandDirectory(dirPath);
    if (expanded) {
      setExpandedNodes(prev => {
        const next = new Map(prev);
        next.set(dirPath, expanded);
        return next;
      });
    }
    return expanded;
  }, [expandDirectory, expandedNodes]);

  // Placeholder while waiting or loading
  if (!ready || (state.isLoading && !state.tree) || (started && !state.tree && !loadError)) {
    return (
      <div className="col-span-full">
        <motion.div
          className="glass-card p-6"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
        >
          <h3 className="text-lg font-semibold text-neon-subtle mb-4">
            Directory Space Analysis
          </h3>

          {/* Progress bar */}
          <div className="mb-4">
            <div className="flex justify-between text-sm text-[var(--text-muted)] mb-2">
              <span>
                {!ready ? 'Waiting...' : 'Streaming directory listing...'}
              </span>
              <span>{started ? `${state.progress}%` : ''}</span>
            </div>
            <div className="w-full h-3 bg-[var(--bg-glass)] rounded-full overflow-hidden">
              {started ? (
                <motion.div
                  className="h-full bg-gradient-to-r from-[var(--neon-cyan)] to-[var(--neon-green)] rounded-full"
                  initial={{ width: 0 }}
                  animate={{ width: `${state.progress}%` }}
                  transition={{ duration: 0.3, ease: 'easeOut' }}
                />
              ) : (
                <div className="h-full w-full bg-[var(--bg-glass-hover)] rounded-full opacity-50" />
              )}
            </div>
          </div>

          <p className="text-sm text-[var(--text-muted)]">
            {!ready ? 'Loading main content first...' : (state.progressText || 'Initializing stream...')}
          </p>

          {/* File size info */}
          <p className="text-xs text-[var(--text-muted)] mt-2">
            {entrySize > 0 ? formatSize(entrySize) + ' uncompressed' : formatSize(file.size) + ' archive'}
          </p>
        </motion.div>
      </div>
    );
  }

  // Error state
  if (loadError || state.error) {
    return (
      <div className="col-span-full">
        <motion.div
          className="glass-card p-6 border-l-4 border-[var(--neon-red)]"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <h3 className="text-lg font-semibold text-[var(--neon-red)] mb-2">
            Failed to Load Directory Analysis
          </h3>
          <p className="text-sm text-[var(--text-muted)]">
            {loadError || state.error}
          </p>
          <button
            onClick={() => {
              setLoadError(null);
              setStarted(false);
              loadStartedRef.current = false;
            }}
            className="mt-4 px-4 py-2 text-sm rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-[var(--text-secondary)] transition-colors"
          >
            Retry
          </button>
        </motion.div>
      </div>
    );
  }

  // No data after loading
  if (!state.tree?.root) {
    return null;
  }

  // Show treemap and table side by side
  return (
    <div className="col-span-full grid grid-cols-1 lg:grid-cols-2 gap-4">
      <DirTreemap
        data={state.tree}
        onExpand={handleExpand}
        expandedNodes={expandedNodes}
        isExpanding={state.isLoading}
      />
      <DirTreeTable
        data={state.tree}
        onExpand={handleExpand}
        expandedNodes={expandedNodes}
        isExpanding={state.isLoading}
      />
    </div>
  );
}
