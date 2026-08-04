export interface CodeEnvUsageRef {
  projectKey: string;
  projectName?: string;
  usageType: string;
  objectType?: string;
  objectId?: string;
  objectName?: string;
  codeEnvKey?: string;
  codeEnvName?: string;
  codeEnvLanguage?: string;
  codeEnvOwner?: string;
}

// Code Environment types
export interface CodeEnv {
  name: string;
  version: string;
  language: 'python' | 'r';
  owner?: string;
  ownerEmail?: string;
  sizeBytes?: number;
  usageCount?: number;
  usageSummary?: Record<string, number>;
  projectCount?: number;
  projectKeys?: string[];
  usageDetails?: CodeEnvUsageRef[];
}

export interface CodeEnvReplaceResult {
  dryRun: boolean;
  sourceEnvName: string;
  sourceLanguage: 'python' | 'r';
  targetEnvName: string;
  matchedRows: number;
  updatedRows: number;
  skippedRows: number;
  failedRows: number;
  results: Array<{
    rowId?: string;
    projectKey?: string;
    objectType?: string;
    objectId?: string;
    objectName?: string;
    from?: string;
    to?: string;
    status: 'planned' | 'updated' | 'skipped' | 'failed';
    error?: string;
  }>;
}

// Provisional rows built from streaming usage-check events before full env details land.
export interface ProvisionalCodeEnv {
  name: string;
  usageCount: number;
  statusLabel: string;
  isSkipped?: boolean;
  scanIndex?: number;
  scanTotal?: number;
  updatedAt: string;
}

// Code Environment Comparison types
export interface CodeEnvCompareGreen {
  envNames: string[];
  packageCount: number;
  pythonVersion: string;
}

export interface CodeEnvComparePurple {
  envNames: string[];
  packageCount: number;
  pythonVersions: Record<string, string>;
}

export interface CodeEnvCompareBlue {
  envNames: string[];
  packageCount: number;
  diffCount: number;
  diffs: Record<string, Record<string, string>>;
}

export interface CodeEnvCompareYellow {
  envA: string;
  envB: string;
  onlyInA: string[];
  onlyInB: string[];
  versionDiffs: Array<{ package: string; versionA: string; versionB: string }>;
}

export interface CodeEnvCompareResult {
  green: CodeEnvCompareGreen[];
  purple: CodeEnvComparePurple[];
  blue: CodeEnvCompareBlue[];
  yellow: CodeEnvCompareYellow[];
  analyzedCount: number;
}

// Code Environment build-failure types (Code Envs → Broken). Dates are
// log-derived epoch-ms (DSS exposes no creation/update date on a code env) and
// are null when the build log listing carries no usable mtime.
export interface BrokenEnvUsage {
  projectKey?: string;
  projectName?: string;
  usageType?: string;
  objectType?: string;
  objectId?: string;
  objectName?: string;
}

export interface BrokenEnvRow {
  name: string;
  lang: string;
  deploymentMode: string;
  pythonVersion: string;
  status: 'OK' | 'FAILED' | 'LOG_UNAVAILABLE' | 'NO_BUILD_LOG';
  failureClass: string | null;
  failureLabel: string | null;
  logName: string | null;
  errorExcerpt: string;
  createdOn: number | null;
  lastBuildOn: number | null;
  sizeBytes: number | null;
  usageCount: number | null;
  usages: BrokenEnvUsage[];
  usagesTruncated: boolean;
}

export interface CodeEnvBrokenResult {
  rows: BrokenEnvRow[];
  indeterminate: BrokenEnvRow[];
  okCount: number;
  total: number;
  sizesAvailable: boolean;
}
