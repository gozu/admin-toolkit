import { useMemo } from 'react';
import type {
  ParsedData,
  HealthScore,
  HealthCategoryScore,
  HealthIssue,
  HealthCategory,
  HealthSeverity,
} from '../types';
import { useThresholds, type ThresholdSettings } from './useThresholds';

/**
 * Category weights for overall score calculation
 * Excludes license and log errors - focuses on system health only
 */
const CATEGORY_WEIGHTS: Record<HealthCategory, number> = {
  code_envs: 0.35,          // Highest priority
  project_footprint: 0.30,  // Second highest priority
  system_capacity: 0.15,
  security_isolation: 0.10,
  version_currency: 0.05,
  runtime_config: 0.05,
  // Legacy categories kept for compatibility with older snapshots
  version: 0,
  system: 0,
  config: 0,
  security: 0,
  connections: 0,  // Surfaced via issue banner, not weighted
  license: 0,      // Not used
  errors: 0,       // Not used
};

export const HEALTH_FACTOR_CONTROLS = [
  { key: 'python_versions', label: 'Python Versions' },
  { key: 'spark_version', label: 'Spark Version' },
  { key: 'memory_availability', label: 'Memory Availability' },
  { key: 'filesystem_capacity', label: 'Filesystem Capacity' },
  { key: 'open_files_limit', label: 'Open Files Limit' },
  { key: 'user_isolation', label: 'User Isolation' },
  { key: 'cgroups_enabled', label: 'CGroups Enabled' },
  { key: 'cgroups_empty_targets', label: 'CGroups Empty Targets' },
  { key: 'code_envs_per_project', label: 'Code Envs per Project' },
  { key: 'project_size_pressure', label: 'Project Size' },
  { key: 'disabled_features', label: 'Disabled Features' },
  { key: 'java_memory_limits', label: 'Java Memory Limits' },
  { key: 'runtime_database', label: 'Runtime Database' },
] as const;

export type HealthFactorKey = (typeof HEALTH_FACTOR_CONTROLS)[number]['key'];

export type HealthFactorToggles = Record<HealthFactorKey, boolean>;

export const DEFAULT_HEALTH_FACTOR_TOGGLES: HealthFactorToggles = Object.fromEntries(
  HEALTH_FACTOR_CONTROLS.map((c) => [c.key, true]),
) as HealthFactorToggles;

interface WeightedScoreComponent {
  enabled: boolean;
  score: number;
  weight: number;
}

function combineEnabledScores(components: WeightedScoreComponent[], defaultScore = 100): number {
  const active = components.filter((entry) => entry.enabled && Number.isFinite(entry.score) && entry.weight > 0);
  if (active.length === 0) return defaultScore;
  const weightSum = active.reduce((sum, entry) => sum + entry.weight, 0);
  if (weightSum <= 0) return defaultScore;
  const weighted = active.reduce((sum, entry) => sum + (entry.score * entry.weight), 0) / weightSum;
  return Math.max(0, Math.min(100, weighted));
}

/**
 * Parse memory string like "4g", "2048m", "512m" to MB
 */
function parseMemoryToMB(value: string | undefined): number {
  if (!value) return 0;
  const match = value.toLowerCase().match(/^(\d+)([gmk]?)$/);
  if (!match) return 0;

  const num = parseInt(match[1], 10);
  const unit = match[2];

  switch (unit) {
    case 'g':
      return num * 1024;
    case 'k':
      return num / 1024;
    case 'm':
    default:
      return num;
  }
}

/**
 * Parse filesystem percentage like "85%" to number
 */
function parsePercentage(value: string | undefined): number {
  if (!value) return 0;
  return parseInt(value.replace('%', ''), 10) || 0;
}

/**
 * Parse memory size string like "16000064 kB" to GB
 */
function parseMemorySizeToGB(value: string | undefined): number {
  if (!value) return 0;
  const match = value.match(/^(\d+)\s*(kB|MB|GB|B)?$/i);
  if (!match) return 0;

  const num = parseInt(match[1], 10);
  const unit = (match[2] || 'kB').toLowerCase();

  switch (unit) {
    case 'gb':
      return num;
    case 'mb':
      return num / 1024;
    case 'kb':
      return num / (1024 * 1024);
    case 'b':
      return num / (1024 * 1024 * 1024);
    default:
      return num / (1024 * 1024); // Assume kB
  }
}

/**
 * Parse a Python version string and return major.minor as numbers
 */
function parsePythonVersion(version: string): { major: number; minor: number } | null {
  const match = version.match(/(\d+)\.(\d+)/);
  if (!match) return null;
  return {
    major: parseInt(match[1], 10),
    minor: parseInt(match[2], 10),
  };
}

/**
 * Check if a Python version is supported (>= 3.10)
 */
function isPythonVersionSupported(version: string): boolean {
  const parsed = parsePythonVersion(version);
  if (!parsed) return false;
  return parsed.major >= 3 && parsed.minor >= 10;
}

/**
 * Calculate Python version score based on percentage of code environments
 * with supported Python versions (>= 3.10)
 *
 * Scoring:
 * - 90%+ supported: 100
 * - 70-89% supported: 80
 * - 50-69% supported: 60
 * - 30-49% supported: 40
 * - <30% supported: 20
 */
function scorePythonVersion(codeEnvs: ParsedData['codeEnvs']): { score: number; issue?: HealthIssue } {
  if (!codeEnvs || codeEnvs.length === 0) {
    return { score: 75 }; // No code envs = neutral score
  }

  const total = codeEnvs.length;
  const supported = codeEnvs.filter(env => isPythonVersionSupported(env.version)).length;
  const unsupported = total - supported;
  const supportedPercent = (supported / total) * 100;

  if (supportedPercent >= 90) {
    return { score: 100 };
  }

  if (supportedPercent >= 70) {
    return {
      score: 80,
      issue: {
        id: 'python-versions-aging',
        category: 'version_currency',
        severity: 'info',
        title: `${unsupported} of ${total} code envs on older Python`,
        description: `${supportedPercent.toFixed(0)}% of code environments use Python 3.10+. ${unsupported} environment${unsupported > 1 ? 's' : ''} use older versions.`,
        recommendation: 'Consider upgrading older code environments to Python 3.10 or later.',
        value: `${supportedPercent.toFixed(0)}%`,
        threshold: '≥90%',
      },
    };
  }

  if (supportedPercent >= 50) {
    return {
      score: 60,
      issue: {
        id: 'python-versions-old',
        category: 'version_currency',
        severity: 'warning',
        title: `${unsupported} of ${total} code envs on older Python`,
        description: `Only ${supportedPercent.toFixed(0)}% of code environments use Python 3.10+. ${unsupported} environment${unsupported > 1 ? 's' : ''} use older, potentially unsupported versions.`,
        recommendation: 'Upgrade code environments to Python 3.10 or later.',
        value: `${supportedPercent.toFixed(0)}%`,
        threshold: '≥90%',
      },
    };
  }

  if (supportedPercent >= 30) {
    return {
      score: 40,
      issue: {
        id: 'python-versions-critical',
        category: 'version_currency',
        severity: 'warning',
        title: `${unsupported} of ${total} code envs on older Python`,
        description: `Only ${supportedPercent.toFixed(0)}% of code environments use Python 3.10+. Most environments use older, potentially unsupported versions.`,
        recommendation: 'Prioritize upgrading code environments to Python 3.10 or later.',
        value: `${supportedPercent.toFixed(0)}%`,
        threshold: '≥90%',
      },
    };
  }

  return {
    score: 20,
    issue: {
      id: 'python-versions-critical',
      category: 'version_currency',
      severity: 'critical',
      title: `${unsupported} of ${total} code envs on unsupported Python`,
      description: `Only ${supportedPercent.toFixed(0)}% of code environments use Python 3.10+. Most environments use older, unsupported Python versions that no longer receive security updates.`,
      recommendation: 'Upgrade code environments to Python 3.10 or later ASAP.',
      value: `${supportedPercent.toFixed(0)}%`,
      threshold: '≥90%',
    },
  };
}

/**
 * Calculate Spark version score
 * 3.x: 100, 2.x: 50, unknown: 75
 */
function scoreSparkVersion(sparkSettings: ParsedData['sparkSettings']): { score: number; issue?: HealthIssue } {
  if (!sparkSettings) return { score: 75 }; // No Spark = neutral

  const version = sparkSettings['Spark Version'];
  if (!version || typeof version !== 'string') return { score: 75 };

  const match = version.match(/^(\d+)/);
  if (!match) return { score: 75 };

  const major = parseInt(match[1], 10);

  if (major < 3) {
    return {
      score: 50,
      issue: {
        id: 'spark-version-old',
        category: 'version_currency',
        severity: 'warning',
        title: `Spark ${version} is outdated`,
        description: `Spark 2.x is deprecated and lacks many performance improvements.`,
        recommendation: 'Upgrade to Spark 3.x for better performance and features.',
        value: version,
        threshold: '3.0+',
      },
    };
  }

  return { score: 100 };
}

/**
 * Calculate memory availability score
 * >30% available: 100, 10-30%: 70, <10%: 30
 */
function scoreMemoryAvailability(memoryInfo: ParsedData['memoryInfo']): { score: number; issue?: HealthIssue } {
  if (!memoryInfo) return { score: 75 };

  const totalStr = memoryInfo['MemTotal'] || memoryInfo['total'];
  const availableStr = memoryInfo['MemAvailable'] || memoryInfo['available'];

  if (!totalStr || !availableStr) return { score: 75 };

  const totalGB = parseMemorySizeToGB(totalStr);
  const availableGB = parseMemorySizeToGB(availableStr);

  if (totalGB <= 0) return { score: 75 };

  const availablePercent = (availableGB / totalGB) * 100;

  if (availablePercent < 10) {
    return {
      score: 30,
      issue: {
        id: 'memory-critical',
        category: 'system_capacity',
        severity: 'critical',
        title: `Memory critically low (${availablePercent.toFixed(0)}% available)`,
        description: `Only ${availableGB.toFixed(1)}GB of ${totalGB.toFixed(1)}GB memory is available.`,
        recommendation: 'Investigate memory usage, consider adding more RAM or reducing load.',
        value: `${availablePercent.toFixed(0)}%`,
        threshold: '>30%',
      },
    };
  }

  if (availablePercent < 30) {
    return {
      score: 70,
      issue: {
        id: 'memory-low',
        category: 'system_capacity',
        severity: 'warning',
        title: `Memory running low (${availablePercent.toFixed(0)}% available)`,
        description: `${availableGB.toFixed(1)}GB of ${totalGB.toFixed(1)}GB memory is available.`,
        recommendation: 'Monitor memory usage and consider scaling resources.',
        value: `${availablePercent.toFixed(0)}%`,
        threshold: '>30%',
      },
    };
  }

  return { score: 100 };
}

/**
 * Calculate filesystem score based on worst mount point
 * >20% available: 100, 10-20%: 70, <10%: 30
 */
function scoreFilesystem(filesystemInfo: ParsedData['filesystemInfo']): { score: number; issues: HealthIssue[] } {
  if (!filesystemInfo || filesystemInfo.length === 0) return { score: 75, issues: [] };

  let worstScore = 100;
  const issues: HealthIssue[] = [];

  for (const fs of filesystemInfo) {
    const usage = parsePercentage(fs['Use%']);
    const mountPoint = fs['Mounted on'] || fs.Filesystem;

    // Skip invalid entries
    if (usage > 100 || usage <= 0) continue;

    const available = 100 - usage;

    if (available < 10) {
      worstScore = Math.min(worstScore, 30);
      issues.push({
        id: `disk-critical-${mountPoint}`,
        category: 'system_capacity',
        severity: 'critical',
        title: `Disk ${usage}% full on ${mountPoint}`,
        description: `Only ${available}% disk space remaining on ${mountPoint}.`,
        recommendation: 'Free up disk space or expand storage immediately.',
        value: `${usage}%`,
        threshold: '<80%',
      });
    } else if (available < 20) {
      worstScore = Math.min(worstScore, 70);
      issues.push({
        id: `disk-warning-${mountPoint}`,
        category: 'system_capacity',
        severity: 'warning',
        title: `Disk ${usage}% used on ${mountPoint}`,
        description: `${available}% disk space remaining on ${mountPoint}.`,
        recommendation: 'Monitor disk usage and plan for cleanup or expansion.',
        value: `${usage}%`,
        threshold: '<80%',
      });
    }
  }

  return { score: worstScore, issues };
}

/**
 * Calculate disabled features score
 * 0: 100, 1-2: 80, 3-5: 60, >5: 40
 */
function scoreDisabledFeatures(disabledFeatures: ParsedData['disabledFeatures']): { score: number; issue?: HealthIssue } {
  if (!disabledFeatures) return { score: 100 };

  const count = Object.keys(disabledFeatures).length;

  if (count === 0) return { score: 100 };

  if (count <= 2) {
    return {
      score: 80,
      issue: {
        id: 'features-disabled-few',
        category: 'runtime_config',
        severity: 'info',
        title: `${count} feature${count > 1 ? 's' : ''} disabled`,
        description: `Some features are disabled which may limit functionality.`,
        recommendation: 'Review disabled features to ensure they are intentionally disabled.',
        value: count,
        threshold: 0,
      },
    };
  }

  if (count <= 5) {
    return {
      score: 60,
      issue: {
        id: 'features-disabled-several',
        category: 'runtime_config',
        severity: 'warning',
        title: `${count} features disabled`,
        description: `Multiple features are disabled which may significantly limit functionality.`,
        recommendation: 'Review disabled features and enable those needed for your use case.',
        value: count,
        threshold: '0-2',
      },
    };
  }

  return {
    score: 40,
    issue: {
      id: 'features-disabled-many',
      category: 'runtime_config',
      severity: 'warning',
      title: `${count} features disabled`,
      description: `Many features are disabled. This may indicate licensing limitations or configuration issues.`,
      recommendation: 'Review disabled features list and discuss with your admin or Dataiku support.',
      value: count,
      threshold: '0-2',
    },
  };
}

/**
 * Calculate security settings score (impersonation, cgroups)
 */
function scoreSecuritySettings(parsedData: ParsedData): { score: number; issues: HealthIssue[] } {
  const issues: HealthIssue[] = [];
  let totalScore = 100;
  let checksPerformed = 0;

  // Check impersonation setting
  if (parsedData.enabledSettings) {
    const impersonation = parsedData.enabledSettings['User Isolation'];
    if (impersonation !== undefined) {
      checksPerformed++;
      if (!impersonation) {
        totalScore -= 25;
        issues.push({
          id: 'impersonation-disabled',
          category: 'security_isolation',
          severity: 'warning',
          title: 'User isolation disabled',
          description: 'User isolation (impersonation) is not enabled.',
          recommendation: 'Consider enabling user isolation for better security in multi-user environments.',
        });
      }
    }
  }

  // Check cgroups setting
  if (parsedData.cgroupSettings) {
    const cgroupsEnabled = parsedData.cgroupSettings['Enabled'];
    checksPerformed++;
    if (!cgroupsEnabled) {
      totalScore -= 15;
      issues.push({
        id: 'cgroups-disabled',
        category: 'security_isolation',
        severity: 'info',
        title: 'CGroups not enabled',
        description: 'CGroups resource limits are not configured.',
        recommendation: 'Consider enabling CGroups for better resource isolation.',
      });
    }

    // Check for empty target types
    const emptyTargets = parsedData.cgroupSettings['Empty Target Types'];
    if (emptyTargets && String(emptyTargets).trim() !== '') {
      totalScore -= 20;
      issues.push({
        id: 'cgroups-empty-targets',
        category: 'security_isolation',
        severity: 'warning',
        title: 'CGroups empty target types',
        description: `Some target types have empty cgroup configurations: ${emptyTargets}`,
        recommendation: 'Configure cgroup settings for all target types.',
      });
    }
  }

  // Check open files limit
  if (parsedData.systemLimits) {
    const maxOpenFiles = parsedData.systemLimits['Max open files'];
    if (maxOpenFiles) {
      checksPerformed++;
      const limit = parseInt(String(maxOpenFiles), 10);
      if (limit < 65535) {
        totalScore -= 20;
        issues.push({
          id: 'open-files-low',
          category: 'system_capacity',
          severity: 'critical',
          title: `Open files limit too low (${limit})`,
          description: `Max open files is ${limit}, should be at least 65535.`,
          recommendation: 'Increase the open files limit in system configuration.',
          value: limit,
          threshold: '>=65535',
        });
      }
    }
  }

  // If no checks were performed, return neutral score
  if (checksPerformed === 0) return { score: 75, issues: [] };

  return { score: Math.max(0, totalScore), issues };
}

/**
 * Calculate runtime database score.
 * Production DSS instances should use PostgreSQL for the runtime database.
 * PostgreSQL: 100, other: 40 (warning), unknown: 75 (neutral).
 */
function scoreRuntimeDatabase(
  generalSettings: ParsedData['generalSettings'],
): { score: number; issue?: HealthIssue } {
  const internalDb = (
    generalSettings as { internalDatabase?: { connection?: { type?: string } } } | undefined
  )?.internalDatabase;
  const type = internalDb?.connection?.type;
  if (!type) return { score: 75 };
  if (type === 'PostgreSQL') return { score: 100 };
  return {
    score: 40,
    issue: {
      id: 'runtime-db-not-postgres',
      category: 'runtime_config',
      severity: 'warning',
      title: `Runtime database is ${type}, not PostgreSQL`,
      description: `DSS runtime database connection type is '${type}'. Production DSS instances should use PostgreSQL for the runtime database.`,
      recommendation: 'Migrate the DSS runtime database to PostgreSQL.',
      value: type,
      threshold: 'PostgreSQL',
    },
  };
}

/**
 * Calculate Java memory settings score
 */
function scoreJavaMemory(javaMemorySettings: ParsedData['javaMemorySettings']): { score: number; issues: HealthIssue[] } {
  if (!javaMemorySettings) return { score: 75, issues: [] };

  const issues: HealthIssue[] = [];
  let totalScore = 100;
  let checksPerformed = 0;

  const components: Array<{ key: string; name: string }> = [
    { key: 'BACKEND', name: 'Backend' },
    { key: 'JEK', name: 'JEK' },
    { key: 'FEK', name: 'FEK' },
  ];

  for (const { key, name } of components) {
    const value = javaMemorySettings[key];
    if (value) {
      checksPerformed++;
      const memoryMB = parseMemoryToMB(value);
      if (memoryMB > 0 && memoryMB < 2048) {
        totalScore -= 15;
        issues.push({
          id: `java-memory-${key.toLowerCase()}`,
          category: 'runtime_config',
          severity: 'warning',
          title: `${name} heap < 2GB (${value})`,
          description: `${name} heap is configured to ${value}, recommended minimum is 2GB.`,
          recommendation: `Increase ${name} heap size in install.ini or environment settings.`,
          value: value,
          threshold: '>=2GB',
        });
      }
    }
  }

  if (checksPerformed === 0) return { score: 75, issues: [] };

  return { score: Math.max(0, totalScore), issues };
}

function normalizeCodeEnvRisk(codeEnvCount: number): number {
  if (codeEnvCount <= 1) return 0;
  if (codeEnvCount === 2) return 0.45;
  if (codeEnvCount === 3) return 0.75;
  return 1.0;
}

function normalizeProjectSizeIndex(totalGb: number, avgGb: number): number {
  if (totalGb >= 40) return 1;
  const absNorm = Math.log1p(Math.min(Math.max(totalGb, 0), 40)) / Math.log1p(40);
  const ratio = totalGb / Math.max(avgGb, 0.1);
  const relNorm = Math.log1p(Math.min(Math.max(ratio, 0), 4)) / Math.log1p(4);
  return Math.max(0, Math.min(1, (0.6 * absNorm) + (0.4 * relNorm)));
}

function scoreCodeEnvComplexity(
  projectFootprint: ParsedData['projectFootprint']
): { score: number; issues: HealthIssue[] } {
  if (!projectFootprint || projectFootprint.length === 0) {
    return { score: 75, issues: [] };
  }

  const risks: number[] = [];
  const issues: HealthIssue[] = [];

  const criticalProjects: string[] = [];
  const warningProjects: string[] = [];
  const infoProjects: string[] = [];

  for (const row of projectFootprint) {
    const count = row.codeEnvCount || 0;
    const risk = normalizeCodeEnvRisk(count);
    risks.push(risk);

    if (count >= 4) {
      criticalProjects.push(`${row.projectKey} (${count})`);
    } else if (count === 3) {
      warningProjects.push(row.projectKey);
    } else if (count === 2) {
      infoProjects.push(row.projectKey);
    }
  }

  if (criticalProjects.length > 0) {
    const preview = criticalProjects.slice(0, 5).join(', ');
    const more = criticalProjects.length > 5 ? ` and ${criticalProjects.length - 5} more` : '';
    issues.push({
      id: 'project-codenv-critical-group',
      category: 'code_envs',
      severity: 'critical',
      title: `${criticalProjects.length} project${criticalProjects.length > 1 ? 's' : ''} have 4+ code envs`,
      description: `${preview}${more}. Each extra code environment multiplies size, fragility, deployment time, and failure surface.`,
      recommendation: 'Consolidate toward a single code environment per project.',
    });
  }

  if (warningProjects.length > 0) {
    const preview = warningProjects.slice(0, 5).join(', ');
    const more = warningProjects.length > 5 ? ` and ${warningProjects.length - 5} more` : '';
    issues.push({
      id: 'project-codenv-warning-group',
      category: 'code_envs',
      severity: 'warning',
      title: `${warningProjects.length} project${warningProjects.length > 1 ? 's' : ''} have 3 code envs`,
      description: `${preview}${more}. Multiple code environments increase maintenance overhead and drift risk.`,
      recommendation: 'Reduce project code environments to 1-2, ideally 1.',
    });
  }

  if (infoProjects.length > 0) {
    const preview = infoProjects.slice(0, 5).join(', ');
    const more = infoProjects.length > 5 ? ` and ${infoProjects.length - 5} more` : '';
    issues.push({
      id: 'project-codenv-info-group',
      category: 'code_envs',
      severity: 'info',
      title: `${infoProjects.length} project${infoProjects.length > 1 ? 's' : ''} have 2 code envs`,
      description: `${preview}${more}. Two code environments already increase rebuild and deployment complexity.`,
      recommendation: 'Consolidate to a single environment when possible.',
    });
  }

  const avgRisk = risks.length > 0 ? risks.reduce((sum, v) => sum + v, 0) / risks.length : 0;
  const score = Math.max(0, Math.min(100, 100 * (1 - avgRisk)));
  return { score, issues };
}

function scoreProjectSizePressure(
  projectFootprint: ParsedData['projectFootprint'],
  summary: ParsedData['projectFootprintSummary']
): { score: number; issues: HealthIssue[] } {
  if (!projectFootprint || projectFootprint.length === 0) {
    return { score: 75, issues: [] };
  }

  const avgProjectGb =
    summary?.instanceAvgProjectGB ??
    (projectFootprint.reduce((sum, row) => sum + ((row.totalBytes || 0) / (1024 * 1024 * 1024)), 0) / projectFootprint.length);

  const risks: number[] = [];
  const issues: HealthIssue[] = [];

  const hugeProjects: string[] = [];
  const criticalSizeProjects: string[] = [];
  const highSizeProjects: string[] = [];

  for (const row of projectFootprint) {
    const totalGb = row.totalGB ?? ((row.totalBytes || 0) / (1024 * 1024 * 1024));
    const sizeRisk = typeof row.projectSizeIndex === 'number'
      ? row.projectSizeIndex
      : normalizeProjectSizeIndex(totalGb, avgProjectGb);
    risks.push(sizeRisk);

    if (totalGb >= 40) {
      hugeProjects.push(`${row.projectKey} (${totalGb.toFixed(1)}GB)`);
      continue;
    }

    const sizeHealth = row.projectSizeHealth;
    if (sizeHealth === 'angry-red') {
      criticalSizeProjects.push(row.projectKey);
    } else if (sizeHealth === 'red') {
      highSizeProjects.push(row.projectKey);
    }
  }

  if (hugeProjects.length > 0) {
    const preview = hugeProjects.slice(0, 5).join(', ');
    const more = hugeProjects.length > 5 ? ` and ${hugeProjects.length - 5} more` : '';
    issues.push({
      id: 'project-size-huge-group',
      category: 'project_footprint',
      severity: 'critical',
      title: `${hugeProjects.length} project${hugeProjects.length > 1 ? 's' : ''} exceed 40GB`,
      description: `${preview}${more}. Project size above 40GB is a severe storage and operational risk.`,
      recommendation: 'Prioritize cleanup or archival for these projects.',
    });
  }

  if (criticalSizeProjects.length > 0) {
    const preview = criticalSizeProjects.slice(0, 5).join(', ');
    const more = criticalSizeProjects.length > 5 ? ` and ${criticalSizeProjects.length - 5} more` : '';
    issues.push({
      id: 'project-size-critical-group',
      category: 'project_footprint',
      severity: 'critical',
      title: `${criticalSizeProjects.length} project${criticalSizeProjects.length > 1 ? 's' : ''} have critical relative size`,
      description: `${preview}${more}. These projects are significantly larger than peers on this instance.`,
      recommendation: 'Review managed data/folders and archive or purge stale assets.',
    });
  }

  if (highSizeProjects.length > 0) {
    const preview = highSizeProjects.slice(0, 5).join(', ');
    const more = highSizeProjects.length > 5 ? ` and ${highSizeProjects.length - 5} more` : '';
    issues.push({
      id: 'project-size-high-group',
      category: 'project_footprint',
      severity: 'warning',
      title: `${highSizeProjects.length} project${highSizeProjects.length > 1 ? 's' : ''} have high project size`,
      description: `${preview}${more}. These projects are above instance norm and add storage pressure.`,
      recommendation: 'Review large managed datasets/folders for cleanup.',
    });
  }

  const avgRisk = risks.length > 0 ? risks.reduce((sum, v) => sum + v, 0) / risks.length : 0;
  const score = Math.max(0, Math.min(100, 100 * (1 - avgRisk)));
  return { score, issues };
}

/**
 * Standalone function to calculate health score from parsed data
 * Use this when you need to calculate outside of React component context
 */
export function calculateHealthScore(
  parsedData: ParsedData,
  factorToggles: Partial<HealthFactorToggles> = DEFAULT_HEALTH_FACTOR_TOGGLES,
  thresholdOverrides?: Partial<ThresholdSettings>
): HealthScore {
    const toggles: HealthFactorToggles = {
      ...DEFAULT_HEALTH_FACTOR_TOGGLES,
      ...factorToggles,
    };
    const t: ThresholdSettings = thresholdOverrides as ThresholdSettings;
    const categoryWeights: Record<HealthCategory, number> = t ? {
      ...CATEGORY_WEIGHTS,
      code_envs: t.weightCodeEnvs,
      project_footprint: t.weightProjectFootprint,
      system_capacity: t.weightSystemCapacity,
      security_isolation: t.weightSecurityIsolation,
      version_currency: t.weightVersionCurrency,
      runtime_config: t.weightRuntimeConfig,
    } : CATEGORY_WEIGHTS;
    const categoryScores: HealthCategoryScore[] = [];
    const allIssues: HealthIssue[] = [];

    // ============================================
    // VERSION CURRENCY (5%)
    // ============================================
    const pythonResult = scorePythonVersion(parsedData.codeEnvs);
    const sparkResult = scoreSparkVersion(parsedData.sparkSettings);

    const versionCurrencyScore = combineEnabledScores([
      { enabled: toggles.python_versions, score: pythonResult.score, weight: 0.7 },
      { enabled: toggles.spark_version, score: sparkResult.score, weight: 0.3 },
    ]);
    const versionCurrencyIssues: HealthIssue[] = [];
    if (toggles.python_versions && pythonResult.issue) versionCurrencyIssues.push(pythonResult.issue);
    if (toggles.spark_version && sparkResult.issue) versionCurrencyIssues.push(sparkResult.issue);

    categoryScores.push({
      category: 'version_currency',
      label: 'Version Currency',
      score: versionCurrencyScore,
      weight: categoryWeights.version_currency,
      issues: versionCurrencyIssues,
    });
    allIssues.push(...versionCurrencyIssues);

    // ============================================
    // SYSTEM CAPACITY (15%)
    // ============================================
    const memoryResult = scoreMemoryAvailability(parsedData.memoryInfo);
    const filesystemResult = scoreFilesystem(parsedData.filesystemInfo);
    const securityResult = scoreSecuritySettings(parsedData);

    // Open files is capacity-related, so it contributes here.
    const openFilesIssue = securityResult.issues.find(i => i.id === 'open-files-low');
    const openFilesScore = openFilesIssue ? 30 : 100;
    const systemCapacityScore = combineEnabledScores([
      { enabled: toggles.memory_availability, score: memoryResult.score, weight: 0.4 },
      { enabled: toggles.filesystem_capacity, score: filesystemResult.score, weight: 0.4 },
      { enabled: toggles.open_files_limit, score: openFilesScore, weight: 0.2 },
    ]);

    const systemCapacityIssues: HealthIssue[] = [];
    if (toggles.filesystem_capacity) {
      systemCapacityIssues.push(...filesystemResult.issues);
    }
    if (toggles.memory_availability && memoryResult.issue) {
      systemCapacityIssues.push(memoryResult.issue);
    }
    if (toggles.open_files_limit && openFilesIssue) {
      systemCapacityIssues.push(openFilesIssue);
    }

    categoryScores.push({
      category: 'system_capacity',
      label: 'System Capacity',
      score: systemCapacityScore,
      weight: categoryWeights.system_capacity,
      issues: systemCapacityIssues,
    });
    allIssues.push(...systemCapacityIssues);

    // ============================================
    // SECURITY ISOLATION (10%)
    // ============================================
    const userIsolationIssue = securityResult.issues.find((i) => i.id === 'impersonation-disabled');
    const cgroupsDisabledIssue = securityResult.issues.find((i) => i.id === 'cgroups-disabled');
    const cgroupsEmptyIssue = securityResult.issues.find((i) => i.id === 'cgroups-empty-targets');
    const securityChecksEnabled =
      toggles.user_isolation || toggles.cgroups_enabled || toggles.cgroups_empty_targets;
    const securityIssues: HealthIssue[] = [];
    let securityIsolationScore = 100;

    if (securityChecksEnabled) {
      if (toggles.user_isolation && userIsolationIssue) {
        securityIsolationScore -= 25;
        securityIssues.push(userIsolationIssue);
      }
      if (toggles.cgroups_enabled && cgroupsDisabledIssue) {
        securityIsolationScore -= 15;
        securityIssues.push(cgroupsDisabledIssue);
      }
      if (toggles.cgroups_empty_targets && cgroupsEmptyIssue) {
        securityIsolationScore -= 20;
        securityIssues.push(cgroupsEmptyIssue);
      }
      securityIsolationScore = Math.max(0, securityIsolationScore);
    }

    categoryScores.push({
      category: 'security_isolation',
      label: 'Security Isolation',
      score: securityIsolationScore,
      weight: categoryWeights.security_isolation,
      issues: securityIssues,
    });
    allIssues.push(...securityIssues);

    // ============================================
    // CODE ENVIRONMENTS (35%)
    // ============================================
    const codeEnvResult = toggles.code_envs_per_project
      ? scoreCodeEnvComplexity(parsedData.projectFootprint)
      : { score: 100, issues: [] };
    categoryScores.push({
      category: 'code_envs',
      label: 'Code Envs',
      score: codeEnvResult.score,
      weight: categoryWeights.code_envs,
      issues: codeEnvResult.issues,
    });
    allIssues.push(...codeEnvResult.issues);

    // ============================================
    // PROJECT FOOTPRINT (30%)
    // ============================================
    const projectFootprintResult = toggles.project_size_pressure
      ? scoreProjectSizePressure(parsedData.projectFootprint, parsedData.projectFootprintSummary)
      : { score: 100, issues: [] };
    categoryScores.push({
      category: 'project_footprint',
      label: 'Project Footprint',
      score: projectFootprintResult.score,
      weight: categoryWeights.project_footprint,
      issues: projectFootprintResult.issues,
    });
    allIssues.push(...projectFootprintResult.issues);

    // ============================================
    // RUNTIME CONFIGURATION (5%)
    // ============================================
    const disabledResult = scoreDisabledFeatures(parsedData.disabledFeatures);
    const javaMemoryResult = scoreJavaMemory(parsedData.javaMemorySettings);
    const runtimeDbResult = scoreRuntimeDatabase(parsedData.generalSettings);

    const runtimeConfigScore = combineEnabledScores([
      { enabled: toggles.disabled_features, score: disabledResult.score, weight: 0.34 },
      { enabled: toggles.java_memory_limits, score: javaMemoryResult.score, weight: 0.33 },
      { enabled: toggles.runtime_database, score: runtimeDbResult.score, weight: 0.33 },
    ]);
    const runtimeConfigIssues: HealthIssue[] = [];
    if (toggles.java_memory_limits) {
      runtimeConfigIssues.push(...javaMemoryResult.issues);
    }
    if (toggles.disabled_features && disabledResult.issue) {
      runtimeConfigIssues.push(disabledResult.issue);
    }
    if (toggles.runtime_database && runtimeDbResult.issue) {
      runtimeConfigIssues.push(runtimeDbResult.issue);
    }

    categoryScores.push({
      category: 'runtime_config',
      label: 'Runtime Config',
      score: runtimeConfigScore,
      weight: categoryWeights.runtime_config,
      issues: runtimeConfigIssues,
    });
    allIssues.push(...runtimeConfigIssues);

    // ============================================
    // CALCULATE OVERALL SCORE
    // ============================================
    const overallScore = categoryScores.reduce((sum, cat) => {
      return sum + (cat.score * cat.weight);
    }, 0);

    // Deduplicate issues by id
    const uniqueIssues = allIssues.filter(
      (issue, index, self) => index === self.findIndex(i => i.id === issue.id)
    );

    // Sort issues by severity
    const severityOrder: Record<HealthSeverity, number> = {
      critical: 0,
      warning: 1,
      info: 2,
      good: 3,
    };
    uniqueIssues.sort((a, b) => severityOrder[a.severity] - severityOrder[b.severity]);

    // Determine status based on score only
    const criticalBelow = t?.healthCriticalBelow ?? 50;
    const warningBelow = t?.healthWarningBelow ?? 80;
    let status: HealthScore['status'] = 'healthy';
    if (overallScore < criticalBelow) {
      status = 'critical';
    } else if (overallScore < warningBelow) {
      status = 'warning';
    }

  return {
    overall: Math.round(overallScore),
    status,
    categories: categoryScores,
    issues: uniqueIssues,
    criticalCount: uniqueIssues.filter(i => i.severity === 'critical').length,
    warningCount: uniqueIssues.filter(i => i.severity === 'warning').length,
    infoCount: uniqueIssues.filter(i => i.severity === 'info').length,
  };
}

/**
 * React hook for calculating health score with memoization
 * Use this in React components
 */
export function useHealthScore(
  parsedData: ParsedData,
  factorToggles: Partial<HealthFactorToggles> = DEFAULT_HEALTH_FACTOR_TOGGLES
): HealthScore {
  const { thresholds } = useThresholds();
  return useMemo(() => calculateHealthScore(parsedData, factorToggles, thresholds), [parsedData, factorToggles, thresholds]);
}
