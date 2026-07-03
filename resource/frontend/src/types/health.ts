// Health Score types
export type HealthSeverity = 'critical' | 'warning' | 'info' | 'good';

export type HealthCategory =
  | 'code_envs'
  | 'project_footprint'
  | 'system_capacity'
  | 'security_isolation'
  | 'version_currency'
  | 'runtime_config'
  | 'version'
  | 'license'
  | 'system'
  | 'errors'
  | 'config'
  | 'security'
  | 'connections';

export interface HealthIssue {
  id: string;
  category: HealthCategory;
  severity: HealthSeverity;
  title: string;
  description: string;
  recommendation?: string;
  docUrl?: string;
  value?: string | number;
  threshold?: string | number;
  /** Whitelist rule this issue's items fall under (false-positive doctrine) —
   *  present only on issues whose members can be whitelisted per item. */
  whitelistRule?: string;
  /** The concrete member items (project keys, env names, mounts). */
  whitelistItems?: string[];
}

export interface HealthCategoryScore {
  category: HealthCategory;
  label: string;
  score: number;
  weight: number;
  issues: HealthIssue[];
}

export interface HealthScore {
  overall: number;
  status: 'healthy' | 'warning' | 'critical';
  categories: HealthCategoryScore[];
  issues: HealthIssue[];
  criticalCount: number;
  warningCount: number;
  infoCount: number;
  /** True when a critical cap rule (issue id `cap-*`) clamped the overall
   *  score into the critical band. */
  capped?: boolean;
  /** Number of distinct (rule, item) pairs the admin whitelist suppressed
   *  during this computation — agents report this count, never the items. */
  whitelistSuppressed?: number;
}
