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
}
