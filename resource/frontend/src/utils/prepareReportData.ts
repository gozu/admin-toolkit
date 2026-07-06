import type { FilesystemInfo, ParsedData } from '../types';

export interface ReportSlideData {
  // Every slide may carry an optional editorial `headline` (short, punchy,
  // specific to this instance's data). Older saved reports don't have it —
  // the renderer falls back to the slide's canonical title.
  executive_summary: { findings: string[]; overall_status: string; headline?: string };
  instance_overview: { narrative: string; headline?: string };
  projects: { narrative: string; highlights: string[]; headline?: string };
  project_footprint: { narrative: string; risks: string[]; headline?: string };
  code_envs: { narrative: string; headline?: string };
  code_env_health: { narrative: string; upgrade_paths: string[]; headline?: string };
  filesystem: { narrative: string; warnings: string[]; headline?: string };
  memory: { narrative: string; tuning_recs: string[]; headline?: string };
  connections: { narrative: string; headline?: string };
  issues: { narrative: string; risk_level: string; headline?: string };
  users: { narrative: string; headline?: string };
  /** Only present when the Cost/CRU module has data for this instance. */
  compute_cost?: { narrative: string; drivers: string[]; headline?: string };
  logs: { narrative: string; patterns: string[]; headline?: string };
  rec_critical: { items: ReportRecItem[] };
  rec_important: { items: ReportRecItem[] };
  rec_nice_to_have: { items: ReportRecItem[] };
  action_plan: { priorities: ReportActionItem[]; headline?: string };
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
 * Keep only real disk mounts: drop pseudo-filesystems (tmpfs, devtmpfs,
 * efivarfs…) and kernel mount points so the deck and the LLM see /data and /
 * instead of /dev/shm noise.
 */
export function filterRealMounts(mounts: FilesystemInfo[] | undefined): FilesystemInfo[] {
  const junkFs = /^(tmpfs|devtmpfs|efivarfs|overlay|squashfs|shm|none)$/i;
  const junkMount = /^\/(dev|sys|proc|run)(\/|$)/;
  return (mounts || []).filter(
    f => !junkFs.test(f.Filesystem || '') && !junkMount.test(f['Mounted on'] || ''),
  );
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

  // Filesystem — real disk mounts only (no tmpfs/devtmpfs noise)
  const realMounts = filterRealMounts(parsedData.filesystemInfo);
  if (realMounts.length) {
    data.filesystem = realMounts.map(f => ({
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

  // Compute & cost — CRU parsed from the instance audit-log window (Cost module)
  const cost = parsedData.projectCostData;
  if (cost?.totals) {
    data.computeCost = {
      auditWindow: cost.span ? { firstTs: cost.span.firstTs, lastTs: cost.span.lastTs } : undefined,
      totals: cost.totals,
      topProjects: cost.projects
        ? [...cost.projects]
            .sort((a, b) => b.memGBh - a.memGBh)
            .slice(0, 10)
            .map(p => ({
              key: p.projectKey,
              memGBh: +p.memGBh.toFixed(1),
              cpuH: +p.cpuH.toFixed(1),
              llmUSD: +p.llmUSD.toFixed(2),
            }))
        : undefined,
      topUsers: cost.users
        ? [...cost.users]
            .sort((a, b) => b.memGBh - a.memGBh)
            .slice(0, 8)
            .map(u => ({
              user: u.authIdentifier,
              memGBh: +u.memGBh.toFixed(1),
              cpuH: +u.cpuH.toFixed(1),
              llmUSD: +u.llmUSD.toFixed(2),
            }))
        : undefined,
      contextTypes: cost.contextTypes,
      idleResourceCount: cost.idleResources?.length,
    };
  }

  // LLM Mesh audit — obsolete / mispriced model findings
  if (parsedData.llmAudit?.summary) {
    data.llmMesh = {
      summary: parsedData.llmAudit.summary,
      flagged: parsedData.llmAudit.rows
        ?.filter(r => r.status === 'obsolete' || r.status === 'ripoff')
        .slice(0, 10)
        .map(r => ({ llmId: r.llmId, status: r.status, model: r.effectiveModel })),
    };
  }

  // K8s clusters
  if (parsedData.clusters?.length) {
    data.k8sClusters = parsedData.clusters
      .slice(0, 10)
      .map(c => ({ name: c.name, status: c.status, version: c.version }));
  }

  // Connection health probe results
  if (parsedData.connectionHealth?.length) {
    const failing = parsedData.connectionHealth.filter(c => c.status === 'fail');
    data.connectionHealth = {
      tested: parsedData.connectionHealth.length,
      failCount: failing.length,
      failing: failing.slice(0, 8).map(c => ({
        name: c.name, type: c.type, error: c.error?.slice(0, 120),
      })),
    };
  }

  // Instance sanity check
  if (parsedData.sanityCheck?.length) {
    const counts: Record<string, number> = {};
    parsedData.sanityCheck.forEach(m => { counts[m.severity] = (counts[m.severity] || 0) + 1; });
    data.sanityCheck = {
      counts,
      maxSeverity: parsedData.sanityCheckMaxSeverity,
      topIssues: parsedData.sanityCheck
        .filter(m => m.severity === 'ERROR' || m.severity === 'WARNING')
        .slice(0, 8)
        .map(m => ({ severity: m.severity, title: m.title })),
    };
  }

  return data;
}
