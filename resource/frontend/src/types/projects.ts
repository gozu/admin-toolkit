import type { CodeEnvUsageRef } from './codeEnvs';

// Project types
export interface Permission {
  type: 'Group' | 'User';
  name: string;
  permissions: Record<string, boolean>;
}

export interface Project {
  key: string;
  name: string;
  owner: string;
  permissions: Permission[];
  versionNumber: number;
}

export type ProjectFootprintHealth = 'green' | 'yellow' | 'orange' | 'red' | 'angry-red';

export interface ProjectSavedModelRef {
  id: string;
  name: string;
  type: 'PREDICTION' | 'CLUSTERING' | 'UNKNOWN' | string;
  savedModelType?: string;
  backendType?: string;
  predictionType?: string;
  versionsCount?: number;
  activeVersionId?: string;
}

export interface CodeStudioRef {
  id: string;
  name: string;
}

export interface FootprintBucketRef {
  name: string;
  label: string;
  bytes: number;
  location?: string;
}

export interface FootprintBreakdown {
  buckets: FootprintBucketRef[];
  otherCount: number;
  otherBytes: number;
}

export interface ProjectFootprintRow {
  projectKey: string;
  name: string;
  owner: string;
  codeEnvCount: number;
  codeEnvBytes?: number;
  managedDatasetsBytes: number;
  managedFoldersBytes: number;
  bundleBytes: number;
  bundleCount?: number;
  footprintBreakdown?: FootprintBreakdown;
  totalBytes: number;
  totalGB: number;
  instanceAvgProjectGB: number;
  projectSizeIndex: number;
  projectSizeHealth: ProjectFootprintHealth;
  codeStudioCount?: number;
  codeStudios?: CodeStudioRef[];
  codeEnvHealth: ProjectFootprintHealth;
  codeEnvRisk?: number;
  projectRisk?: number;
  usageBreakdown?: Record<string, number>;
  savedModelCount?: number;
  savedModels?: ProjectSavedModelRef[];
  savedModelTypeCounts?: Record<string, number>;
  savedModelSummary?: string;
  usageDetails?: CodeEnvUsageRef[];
  codeEnvKeys?: string[];
}

export interface SqlPushdownRecipeFinding {
  recipeName: string;
  recipeType: string;
  connection: string;
  inputs: string[];
  outputs: string[];
}

export interface SqlPushdownProjectFinding {
  projectKey: string;
  projectName: string;
  recipes: SqlPushdownRecipeFinding[];
}

export interface SqlPushdownOwnerGroup {
  ownerLogin: string;
  ownerDisplayName: string;
  ownerEmail: string | null;
  totalRecipes: number;
  projects: SqlPushdownProjectFinding[];
  scanErrors?: { projectKey: string; area: string; error: string }[];
  failedProjectCount?: number;
  scannedProjectCount?: number;
}

export interface ProjectFootprintSummary {
  instanceProjectRiskAvg: number;
  instanceAvgProjectGB: number;
  projectCount: number;
  scanErrors?: { projectKey: string; area: string; error: string }[];
  failedProjectCount?: number;
  scannedProjectCount?: number;
  benchmark?: {
    enabled?: boolean;
    projectLimit?: number;
    projectSelection?: string;
    timeoutMs?: number;
    timedOut?: boolean;
    timeoutAtStep?: string | null;
    totalElapsedMs?: number;
    remainingMs?: number;
    totalProjectCount?: number;
    selectedProjectCount?: number;
    steps?: Array<{
      name: string;
      calls: number;
      elapsedMs: number;
      avgMs: number;
      qps: number;
    }>;
    apiCalls?: Array<{
      operation: string;
      calls: number;
      elapsedMs: number;
      avgMs: number;
      qps: number;
    }>;
    events?: Array<{
      tMs?: number;
      level?: 'info' | 'warn' | 'error';
      step?: string;
      projectKey?: string;
      message?: string;
      elapsedMs?: number;
    }>;
  };
}

// User types
export interface User {
  login: string;
  email?: string;
  enabled?: boolean;
  userProfile?: string;
}

// User stats
export type UserStats = Record<string, string | number>;
