// Filesystem info
export interface FilesystemInfo {
  Filesystem: string;
  Size: string;
  Used: string;
  Available: string;
  'Use%': string;
  'Mounted on': string;
}

// Memory info
export type MemoryInfo = Record<string, string>;

// System limits
export type SystemLimits = Record<string, string>;

/** One row of the per-PID CPU/memory snapshot (see python-runnables/process-metrics). */
export interface ProcessMetric {
  pid: number;
  user: string;
  cpuPercent: number;
  memPercent: number;
  rssKb: number;
  vszKb: number;
  command: string;
}

// Directory tree types for datadir_listing.txt visualization
export interface DirEntry {
  name: string;
  path: string;
  size: number; // Size in bytes (cumulative for dirs - includes hidden children)
  ownSize: number; // Directory's own size (usually 4096) or file size
  isDirectory: boolean;
  children: DirEntry[];
  fileCount: number; // Number of files (recursive for dirs - includes hidden)
  depth: number;
  hasHiddenChildren: boolean; // True if children were aggregated due to depth limit
}

export interface DirTreeData {
  root: DirEntry | null;
  totalSize: number;
  totalFiles: number;
  rootPath: string;
  scope?: 'all' | 'global' | 'unknown' | 'project';
  projectKey?: string | null;
}

// Byte-offset index for fast drill-down into large directory listings
export interface DirIndex {
  path: string;
  startByte: number; // Where this dir's entries begin in the file
  endByte: number; // Where they end (exclusive)
  totalSize: number; // Pre-computed cumulative size
  fileCount: number; // Pre-computed file count
  depth: number; // Depth at which this was indexed
}

// State for the async directory tree loader
export interface DirTreeLoaderState {
  isLoading: boolean;
  progress: number; // 0-100 percentage
  progressText: string; // Human-readable progress
  error: string | null;
  tree: DirTreeData | null;
  index: Map<string, DirIndex>;
}
