export interface PluginUsageObject {
  elementKind: string;
  elementType: string;
  objectType: string;
  objectId: string;
}

export interface PluginProjectUsage {
  projectKey: string;
  elementKinds: Record<string, number>;
  objects: PluginUsageObject[];
}

export interface PluginMissingType {
  missingType: string;
  objectType: string;
  projectKey: string;
  objectId: string;
}

export interface PluginInfo {
  id: string;
  label?: string;
  installedVersion?: string;
  /** Latest version published on the Dataiku plugin store (currency check). */
  latestVersion?: string;
  isDev?: boolean;
  projectsUsingCount?: number | null;
  projectsUsing?: PluginProjectUsage[];
  missingTypes?: PluginMissingType[];
  usagesError?: string;
}

export interface PluginCompareRow {
  id: string;
  label: string;
  localVersion: string | null;
  remoteVersion: string | null;
  isDev: boolean;
}
