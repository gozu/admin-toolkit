// Disabled feature info
export interface DisabledFeature {
  status: string;
  description: string;
  url: string;
}

// License properties
export type LicenseProperties = Record<
  string,
  string | { value: string; truncate: boolean; maxLength: number }
>;

// Settings types
export type EnabledSettings = Record<string, boolean>;
export type SparkSettings = Record<string, string | number | boolean>;
export type AuthSettings = Record<
  string,
  string | { value: string; truncate: boolean; maxLength: number }
>;
export type ContainerSettings = Record<string, string | number>;
export type IntegrationSettings = Record<string, string>;
export type ResourceLimits = Record<string, string | number>;
export type CgroupSettings = Record<string, string | number>;
export type ProxySettings = Record<string, string | number | boolean | string[]>;
export type MaxRunningActivities = Record<string, number | string>;
export type JavaMemorySettings = Record<string, string>;

// Instance-default container execution modes (project-standards.json).
// CONTAINER collapses any concrete execution-config name.
export type ContainerExecMode = 'NONE' | 'INHERIT' | 'CONTAINER';

export interface ContainerExecDefaults {
  executionConfigsCount: number;
  userCodeMode: ContainerExecMode;
  visualRecipesMode: ContainerExecMode;
}
export interface JekSettings {
  maxRunningJobs?: number;
  numberOfReadyJEKs?: number;
}

// Instance info from install.ini
export interface InstanceInfo {
  nodeId?: string;
  installId?: string;
  instanceUrl?: string;
  https?: boolean;
  port?: string;
}

// Security & defaults settings rendered as a flat key/value table
export type SecurityDefaultsSettings = Record<string, string | boolean>;
