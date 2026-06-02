import type { ParsedData } from '../types';

export interface ReportSlideData {
  executive_summary: { findings: string[]; overall_status: string };
  instance_overview: { narrative: string };
  projects: { narrative: string; highlights: string[] };
  project_footprint: { narrative: string; risks: string[] };
  code_envs: { narrative: string };
  code_env_health: { narrative: string; upgrade_paths: string[] };
  filesystem: { narrative: string; warnings: string[] };
  memory: { narrative: string; tuning_recs: string[] };
  connections: { narrative: string };
  issues: { narrative: string; risk_level: string };
  users: { narrative: string };
  logs: { narrative: string; patterns: string[] };
  rec_critical: { items: ReportRecItem[] };
  rec_important: { items: ReportRecItem[] };
  rec_nice_to_have: { items: ReportRecItem[] };
  action_plan: { priorities: ReportActionItem[] };
}

export interface ReportRecItem {
  title: string;
  description: string;
  impact: string;
}

export interface ReportActionItem {
  action: string;
  timeline: string;
  effort: 'low' | 'medium' | 'high';
}

export interface ReportData {
  slides: ReportSlideData;
}

/**
 * Summarize parsedData into a compact payload for the LLM prompt.
 * Keeps total size under ~25K chars to fit most model context windows.
 */
export function prepareReportData(parsedData: ParsedData): Record<string, unknown> {
  const data: Record<string, unknown> = {};

  // Instance info
  if (parsedData.dssVersion || parsedData.osInfo) {
    data.instance = {
      dssVersion: parsedData.dssVersion,
      osInfo: parsedData.osInfo,
      cpuCores: parsedData.cpuCores,
      pythonVersion: parsedData.pythonVersion,
      lastRestartTime: parsedData.lastRestartTime,
    };
  }

  // License
  if (parsedData.licenseInfo) {
    const li = parsedData.licenseInfo as Record<string, unknown>;
    data.license = {
      licenseType: li.licenseType,
      expiresOn: li.expiresOn,
      maxUsers: li.maxUsers,
      hasExpired: li.hasExpired,
    };
  }

  // Settings (key-value summaries only)
  const settingsSummary: Record<string, unknown> = {};
  if (parsedData.authSettings) settingsSummary.auth = parsedData.authSettings;
  if (parsedData.sparkSettings) settingsSummary.spark = parsedData.sparkSettings;
  if (parsedData.resourceLimits) settingsSummary.resourceLimits = parsedData.resourceLimits;
  if (parsedData.cgroupSettings) settingsSummary.cgroups = parsedData.cgroupSettings;
  if (parsedData.enabledSettings) settingsSummary.enabled = parsedData.enabledSettings;
  if (Object.keys(settingsSummary).length > 0) data.settings = settingsSummary;

  // Projects — top 20 by versionNumber descending
  if (parsedData.projects?.length) {
    const sorted = [...parsedData.projects]
      .sort((a, b) => b.versionNumber - a.versionNumber)
      .slice(0, 20);
    data.projects = {
      totalCount: parsedData.projects.length,
      top20: sorted.map(p => ({ key: p.key, name: p.name, owner: p.owner, versionNumber: p.versionNumber })),
    };
  }

  // Project footprint — summary + top 20 by totalBytes
  if (parsedData.projectFootprint?.length) {
    const sorted = [...parsedData.projectFootprint]
      .sort((a, b) => b.totalBytes - a.totalBytes)
      .slice(0, 20);
    data.projectFootprint = {
      summary: parsedData.projectFootprintSummary ? {
        projectCount: parsedData.projectFootprintSummary.projectCount,
        instanceAvgProjectGB: parsedData.projectFootprintSummary.instanceAvgProjectGB,
        instanceProjectRiskAvg: parsedData.projectFootprintSummary.instanceProjectRiskAvg,
      } : undefined,
      top20: sorted.map(p => ({
        key: p.projectKey, name: p.name, totalGB: p.totalGB,
        managedDatasetsBytes: p.managedDatasetsBytes,
        managedFoldersBytes: p.managedFoldersBytes,
        bundleBytes: p.bundleBytes,
        projectSizeHealth: p.projectSizeHealth,
      })),
    };
  }

  // Code Environments
  if (parsedData.codeEnvs?.length) {
    data.codeEnvs = {
      totalCount: parsedData.codeEnvs.length,
      pythonVersionCounts: parsedData.pythonVersionCounts,
      rVersionCounts: parsedData.rVersionCounts,
      envs: parsedData.codeEnvs.map(e => ({
        name: e.name, version: e.version, language: e.language,
        owner: e.owner, sizeBytes: e.sizeBytes,
        usageCount: e.usageCount, projectCount: e.projectCount,
      })),
    };
  }

  // Filesystem
  if (parsedData.filesystemInfo?.length) {
    data.filesystem = parsedData.filesystemInfo.map(f => ({
      filesystem: f.Filesystem, size: f.Size, used: f.Used,
      available: f.Available, usePct: f['Use%'], mountedOn: f['Mounted on'],
    }));
  }

  // Memory / JVM
  if (parsedData.memoryInfo || parsedData.javaMemorySettings) {
    data.memory = {
      systemMemory: parsedData.memoryInfo,
      javaMemorySettings: parsedData.javaMemorySettings,
      javaMemoryLimits: parsedData.javaMemoryLimits,
    };
  }

  // Connections
  if (parsedData.connectionCounts || parsedData.connectionDetails?.length) {
    data.connections = {
      typeCounts: parsedData.connectionCounts,
      details: parsedData.connectionDetails?.map(c => ({ name: c.name, type: c.type })),
    };
  }

  // Users
  if (parsedData.users?.length) {
    data.users = {
      totalCount: parsedData.users.length,
      stats: parsedData.userStats,
      byProjects: parsedData.usersByProjects,
    };
  }

  // Plugins
  if (parsedData.pluginDetails?.length) {
    data.plugins = parsedData.pluginDetails.map(p => ({
      id: p.id, label: p.label, version: p.installedVersion, isDev: p.isDev,
    }));
  } else if (parsedData.plugins?.length) {
    data.plugins = parsedData.plugins;
  }

  // Disabled features
  if (parsedData.disabledFeatures) {
    data.disabledFeatures = Object.entries(parsedData.disabledFeatures).map(([key, f]) => ({
      feature: key, status: f.status, description: f.description,
    }));
  }

  // Log errors
  if (parsedData.logStats || parsedData.formattedLogErrors) {
    data.logs = {
      stats: parsedData.logStats,
      // Take first 3K chars of formatted errors to stay within budget
      errorSample: parsedData.formattedLogErrors?.slice(0, 3000),
    };
  }

  return data;
}
