import type { CodeEnv } from './codeEnvs';
import type { Cluster } from './k8s';
import type { Project, User } from './projects';

// =============================================================================
// COMPARISON TYPES
// =============================================================================

export type ComparisonViewMode = 'delta' | 'side-by-side' | 'tabbed';
export type DeltaDirection = 'improvement' | 'regression' | 'neutral';
export type DeltaSeverity = 'critical' | 'warning' | 'info';
export type ChangeType = 'added' | 'removed' | 'modified' | 'unchanged';

// Delta for a single field comparison
export interface FieldDelta {
  field: string;
  label: string;
  category: string;
  before: unknown;
  after: unknown;
  changeType: ChangeType;
  direction: DeltaDirection;
  severity: DeltaSeverity;
  numericDelta?: number;
  percentChange?: number;
}

// Delta for a collection (arrays of objects)
export interface CollectionDelta<T> {
  added: T[];
  removed: T[];
  modified: Array<{
    before: T;
    after: T;
    changes: string[];
  }>;
  unchanged: number;
}

// Health score delta
export interface HealthDelta {
  before: number;
  after: number;
  change: number;
  direction: DeltaDirection;
}

// A section of deltas grouped by category
export interface DeltaSection {
  id: string;
  label: string;
  icon: string;
  deltas: FieldDelta[];
  changeCount: number;
}

// Full comparison result
export interface ComparisonResult {
  computedAt: Date;
  summary: {
    totalChanges: number;
    improvements: number;
    regressions: number;
    neutral: number;
    critical: number;
    improvementDeltas: FieldDelta[];
    regressionDeltas: FieldDelta[];
  };
  health: HealthDelta;
  sections: {
    critical: DeltaSection;
    system: DeltaSection;
    versions: DeltaSection;
    config: DeltaSection;
    scale: DeltaSection;
    infrastructure: DeltaSection;
  };
  collections: {
    users: CollectionDelta<User>;
    projects: CollectionDelta<Project>;
    clusters: CollectionDelta<Cluster>;
    codeEnvs: CollectionDelta<CodeEnv>;
    plugins: CollectionDelta<string>;
  };
}
