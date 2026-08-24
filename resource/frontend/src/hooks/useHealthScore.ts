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
import { whitelistStore, activeHostWhitelist } from '../state/whitelistStore';
import type { LifecycleFieldName } from '../utils/moduleRegistry';
import type { ExecResourceConfig } from '../utils/execResources';

// Single source of truth for the lifecycle fields the health score actually
// consumes. `calculateHealthScore` reads code-envs, system/security settings,
// filesystem, memory, project footprint — and nothing else. The Summary score
// gate aggregates over THESE fields, not the global `analysisLoading` (which
// waits on all ~19 modules, including Cost/CRU, which the score never touches).
// IMPORTANT: when adding a new input to `calculateHealthScore`, add its
// lifecycle field here too — this link is a human convention the compiler
// cannot infer.
export const SCORE_LIFECYCLE_FIELDS: readonly LifecycleFieldName[] = [
  'summaryLoading',
  'settingsLoading',
  'filesystemLoading',
  'memoryLoading',
  'codeEnvsLoading',
  'projectFootprintLoading',
  // Both autostart in live mode and their error phase is terminal, so the
  // score reveal cannot wedge on them. The on-demand usage scan is
  // deliberately NOT gated on — broken connections surface as 'unverified'
  // until it completes.
  'connectionsHealthLoading',
  'sanityCheckLoading',
];

/**
 * Category weights for overall score calculation — calibrated against the TAM
 * severity rubric (docs/agent-workflows/severity-rubric.md): infra/runtime
 * dominate; security_isolation is zero-weighted (its cgroups penalties moved
 * into runtime_config so zero-weighting loses nothing) but its issues still
 * surface.
 */
const CATEGORY_WEIGHTS: Record<HealthCategory, number> = {
  system_capacity: 0.30,
  runtime_config: 0.30,
  code_envs: 0.15,
  project_footprint: 0.15,
  version_currency: 0.10,
  security_isolation: 0,
  // Legacy categories kept for compatibility with older snapshots
  version: 0,
  system: 0,
  config: 0,
  security: 0,
  connections: 0,  // Surfaced via issue banner, not weighted
  license: 0,      // Not used
  errors: 0,       // Not used
};

/** Rubric size lines (interview C26/E46): code env >5GB, project >10GB. */
const LARGE_CODE_ENV_GB = 5;
const LARGE_PROJECT_GB = 10;

/** One admin-whitelisted finding item: `rule` is a stable issue-id prefix,
 *  `item` the concrete object (project key, env name). Whitelisted items are
 *  exempted INSIDE the scorers — factor score and issue membership both. */
export interface FindingWhitelistEntry {
  rule: string;
  item: string;
  host?: string;
  note?: string;
  addedBy?: string;
  addedAt?: string;
}

/** Rules that honor the per-item whitelist (false-positive doctrine). */
export const WHITELISTABLE_RULES = [
  { rule: 'project-size', label: 'Project size', itemLabel: 'project key' },
  { rule: 'project-code-envs', label: 'Code envs per project', itemLabel: 'project key' },
  { rule: 'code-env-size', label: 'Code env size', itemLabel: 'env name' },
  { rule: 'python-env-lifecycle', label: 'Deprecated Python env', itemLabel: 'env name' },
  { rule: 'disk-usage', label: 'Disk usage on mount', itemLabel: 'mount point' },
  { rule: 'connection-broken', label: 'Broken connection', itemLabel: 'connection name' },
  { rule: 'exec-config-resources', label: 'Exec config resources', itemLabel: 'exec config name' },
  { rule: 'sanity-check', label: 'Sanity check message', itemLabel: 'message code' },
] as const;

type IsWhitelisted = ((rule: string, item: string) => boolean) & { matched: Set<string> };

function buildWhitelistLookup(entries?: FindingWhitelistEntry[]): IsWhitelisted {
  const keys = new Set((entries ?? []).map((e) => `${e.rule} ${e.item}`));
  const matched = new Set<string>();
  const check = ((rule: string, item: string) => {
    const key = `${rule} ${item}`;
    if (keys.has(key)) {
      matched.add(key);
      return true;
    }
    return false;
  }) as IsWhitelisted;
  check.matched = matched;
  return check;
}

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
  { key: 'connection_health', label: 'Connection Health' },
  { key: 'exec_config_resources', label: 'Exec Config Resources' },
  { key: 'sanity_check', label: 'DSS Sanity Check' },
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
 * Lifecycle-aware Python scorer (interview C23/C23b): severity follows the
 * Dataiku deprecation lifecycle, conditioned on the instance DSS version.
 * - IN-USE env on a deprecated version (2.x/3.6/3.7): score 20, critical.
 * - IN-USE 3.8 env on DSS >= 14: score 60, warning (removal is coming).
 * - UNREFERENCED deprecated env: info-only delete candidate, no score drag.
 * Envs whitelisted under 'python-env-lifecycle' are skipped entirely.
 */
function scorePythonVersion(
  codeEnvs: ParsedData['codeEnvs'],
  dssVersion: ParsedData['dssVersion'],
  t: Partial<ThresholdSettings> | undefined,
  isWhitelisted: IsWhitelisted,
): { score: number; issues: HealthIssue[] } {
  if (!codeEnvs || codeEnvs.length === 0) {
    return { score: 75, issues: [] }; // No code envs = neutral score
  }

  const deprecatedPrefixes = String(t?.deprecatedPythonPrefixes ?? '2.,3.6,3.7')
    .split(',')
    .map((p) => p.trim())
    .filter(Boolean);
  const dssMajor = parseInt(String(dssVersion ?? ''), 10) || 0;
  const isDeprecated = (version: string) =>
    deprecatedPrefixes.some((p) => String(version || '').startsWith(p));
  // usageCount missing (older payloads / uploaded diags) counts as in-use.
  const isInUse = (env: { usageCount?: number }) =>
    typeof env.usageCount === 'number' ? env.usageCount > 0 : true;

  const pythonEnvs = codeEnvs.filter(
    (env) => env.language !== 'r' && !isWhitelisted('python-env-lifecycle', env.name),
  );
  const deprecatedInUse = pythonEnvs.filter((env) => isDeprecated(env.version) && isInUse(env));
  const planInUse = dssMajor >= 14
    ? pythonEnvs.filter((env) => String(env.version || '').startsWith('3.8') && isInUse(env))
    : [];
  const deprecatedUnused = pythonEnvs.filter((env) => isDeprecated(env.version) && !isInUse(env));

  const issues: HealthIssue[] = [];
  let score = 100;

  if (deprecatedInUse.length > 0) {
    score = 20;
    const preview = deprecatedInUse.slice(0, 5).map((e) => `${e.name} (${e.version})`).join(', ');
    const more = deprecatedInUse.length > 5 ? ` and ${deprecatedInUse.length - 5} more` : '';
    issues.push({
      id: 'python-lifecycle-critical',
      category: 'version_currency',
      severity: 'critical',
      title: `${deprecatedInUse.length} in-use code env${deprecatedInUse.length > 1 ? 's' : ''} on deprecated Python`,
      description: `${preview}${more}. These Python versions are deprecated by Dataiku — projects using them must migrate now.`,
      recommendation: 'Migrate the projects using these environments to a supported Python version now.',
      value: deprecatedInUse.length,
      threshold: `not ${deprecatedPrefixes.join('/')}`,
      whitelistRule: 'python-env-lifecycle',
      whitelistItems: deprecatedInUse.map((e) => e.name),
    });
  } else if (planInUse.length > 0) {
    score = 60;
    const preview = planInUse.slice(0, 5).map((e) => e.name).join(', ');
    const more = planInUse.length > 5 ? ` and ${planInUse.length - 5} more` : '';
    issues.push({
      id: 'python-lifecycle-plan',
      category: 'version_currency',
      severity: 'warning',
      title: `${planInUse.length} in-use code env${planInUse.length > 1 ? 's' : ''} on Python 3.8 (deprecated in DSS 14)`,
      description: `${preview}${more}. DSS 14 deprecates Python 3.8; support will be removed in a later release.`,
      recommendation: 'Plan the migration to a supported Python version before the removal release.',
      value: planInUse.length,
      whitelistRule: 'python-env-lifecycle',
      whitelistItems: planInUse.map((e) => e.name),
    });
  }

  if (deprecatedUnused.length > 0) {
    const preview = deprecatedUnused.slice(0, 5).map((e) => `${e.name} (${e.version})`).join(', ');
    const more = deprecatedUnused.length > 5 ? ` and ${deprecatedUnused.length - 5} more` : '';
    issues.push({
      id: 'python-lifecycle-cleanup',
      category: 'version_currency',
      severity: 'info',
      title: `${deprecatedUnused.length} unreferenced code env${deprecatedUnused.length > 1 ? 's' : ''} on deprecated Python`,
      description: `${preview}${more}. Nothing references these environments — they are delete candidates, not migration work.`,
      recommendation: 'Delete these unused environments (backup-first via the code-env cleaner).',
      value: deprecatedUnused.length,
      whitelistRule: 'python-env-lifecycle',
      whitelistItems: deprecatedUnused.map((e) => e.name),
    });
  }

  return { score, issues };
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
function scoreFilesystem(
  filesystemInfo: ParsedData['filesystemInfo'],
  isWhitelisted: IsWhitelisted,
): { score: number; issues: HealthIssue[] } {
  if (!filesystemInfo || filesystemInfo.length === 0) return { score: 75, issues: [] };

  let worstScore = 100;
  const issues: HealthIssue[] = [];

  for (const fs of filesystemInfo) {
    const usage = parsePercentage(fs['Use%']);
    const mountPoint = fs['Mounted on'] || fs.Filesystem;

    // Skip invalid entries
    if (usage > 100 || usage <= 0) continue;
    if (isWhitelisted('disk-usage', String(mountPoint))) continue;

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
        whitelistRule: 'disk-usage',
        whitelistItems: [String(mountPoint)],
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
        whitelistRule: 'disk-usage',
        whitelistItems: [String(mountPoint)],
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

  // Check cgroups setting. The parser serves 'Yes'/'No' strings — anything
  // other than an explicit 'Yes' (missing key included) counts as disabled.
  // (This deliberately fixes the old truthiness bug where the string 'No'
  // passed the check and the penalty never fired live.)
  // cgroups issues live in runtime_config per the TAM rubric: they are a
  // runtime-blowup risk, not a security nicety.
  if (parsedData.cgroupSettings) {
    const cgroupsEnabled = parsedData.cgroupSettings['Enabled'];
    checksPerformed++;
    if (String(cgroupsEnabled ?? '') !== 'Yes') {
      totalScore -= 15;
      issues.push({
        id: 'cgroups-disabled',
        category: 'runtime_config',
        severity: 'warning',
        title: 'CGroups not enabled',
        description: 'CGroups resource limits are not configured — runaway kernels/jobs can take down the host.',
        recommendation: 'Enable CGroups memory limits for kernels and jobs.',
      });
    }

    // Check for empty target types
    const emptyTargets = parsedData.cgroupSettings['Empty Target Types'];
    if (emptyTargets && String(emptyTargets).trim() !== '') {
      totalScore -= 20;
      issues.push({
        id: 'cgroups-empty-targets',
        category: 'runtime_config',
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
 * Calculate runtime database score (rubric F56: internal H2 is critical
 * unconditionally and caps the overall score).
 * PostgreSQL: 100. Settings loaded but no PostgreSQL connection type ⇒ the
 * runtime DB is the embedded H2 (externalized runtime DBs always carry
 * internalDatabase.connection.type) ⇒ score 0 + `cap-runtime-db` critical.
 * Settings not loaded: 75 (neutral) — the guard against an empty payload.
 */
function scoreRuntimeDatabase(
  generalSettings: ParsedData['generalSettings'],
): { score: number; issue?: HealthIssue } {
  const settings = generalSettings as
    | { internalDatabase?: { connection?: { type?: string } } }
    | undefined;
  if (!settings || Object.keys(settings).length === 0) return { score: 75 };
  const type = settings.internalDatabase?.connection?.type;
  if (type === 'PostgreSQL') return { score: 100 };
  const label = type || 'internal H2';
  return {
    score: 0,
    issue: {
      id: 'cap-runtime-db',
      category: 'runtime_config',
      severity: 'critical',
      title: `Runtime database is ${label}, not PostgreSQL`,
      description: `DSS runtime database is '${label}'. The internal H2 runtime database is disqualifying at any instance size — DSS runs unnecessarily slowly until it is externalized.`,
      recommendation: 'Migrate the DSS runtime database to PostgreSQL immediately.',
      value: label,
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
  projectFootprint: ParsedData['projectFootprint'],
  isWhitelisted: IsWhitelisted,
): { score: number; issues: HealthIssue[] } {
  if (!projectFootprint || projectFootprint.length === 0) {
    return { score: 75, issues: [] };
  }

  const risks: number[] = [];
  const issues: HealthIssue[] = [];

  const criticalProjects: string[] = [];
  const criticalKeys: string[] = [];
  const warningProjects: string[] = [];
  const infoProjects: string[] = [];

  for (const row of projectFootprint) {
    if (isWhitelisted('project-code-envs', row.projectKey)) continue;
    const count = row.codeEnvCount || 0;
    const risk = normalizeCodeEnvRisk(count);
    risks.push(risk);

    if (count >= 4) {
      criticalProjects.push(`${row.projectKey} (${count})`);
      criticalKeys.push(row.projectKey);
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
      whitelistRule: 'project-code-envs',
      whitelistItems: criticalKeys,
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
      whitelistRule: 'project-code-envs',
      whitelistItems: warningProjects,
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
      whitelistRule: 'project-code-envs',
      whitelistItems: infoProjects,
    });
  }

  const avgRisk = risks.length > 0 ? risks.reduce((sum, v) => sum + v, 0) / risks.length : 0;
  const score = Math.max(0, Math.min(100, 100 * (1 - avgRisk)));
  return { score, issues };
}

/**
 * Rubric C26: a single code env >5GB on disk is a finding (whitelist-subject —
 * some envs are legitimately huge, e.g. CUDA/torch). Modest score dent; the
 * point is the issue, not the number.
 */
function scoreCodeEnvSize(
  codeEnvs: ParsedData['codeEnvs'],
  isWhitelisted: IsWhitelisted,
): { score: number; issues: HealthIssue[] } {
  if (!codeEnvs || codeEnvs.length === 0) return { score: 100, issues: [] };
  const sized = codeEnvs.filter((env) => typeof env.sizeBytes === 'number' && env.sizeBytes > 0);
  if (sized.length === 0) return { score: 100, issues: [] };

  const large = sized.filter(
    (env) =>
      (env.sizeBytes as number) / (1024 * 1024 * 1024) > LARGE_CODE_ENV_GB &&
      !isWhitelisted('code-env-size', env.name),
  );
  if (large.length === 0) return { score: 100, issues: [] };

  const preview = large
    .slice(0, 5)
    .map((e) => `${e.name} (${((e.sizeBytes as number) / (1024 * 1024 * 1024)).toFixed(1)}GB)`)
    .join(', ');
  const more = large.length > 5 ? ` and ${large.length - 5} more` : '';
  return {
    score: Math.max(40, 100 - large.length * 15),
    issues: [{
      id: 'code-env-size-group',
      category: 'code_envs',
      severity: 'warning',
      title: `${large.length} code env${large.length > 1 ? 's' : ''} over ${LARGE_CODE_ENV_GB}GB`,
      description: `${preview}${more}. Environments this large are usually over-pinned or carry unused heavy packages.`,
      recommendation: 'Slim the environment (or whitelist it if the size is legitimate, e.g. CUDA).',
      value: large.length,
      threshold: `≤${LARGE_CODE_ENV_GB}GB`,
      whitelistRule: 'code-env-size',
      whitelistItems: large.map((e) => e.name),
    }],
  };
}

function scoreProjectSizePressure(
  projectFootprint: ParsedData['projectFootprint'],
  summary: ParsedData['projectFootprintSummary'],
  isWhitelisted: IsWhitelisted,
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
  const hugeKeys: string[] = [];
  const largeProjects: string[] = [];
  const largeKeys: string[] = [];
  const criticalSizeProjects: string[] = [];
  const highSizeProjects: string[] = [];

  for (const row of projectFootprint) {
    if (isWhitelisted('project-size', row.projectKey)) continue;
    const totalGb = row.totalGB ?? ((row.totalBytes || 0) / (1024 * 1024 * 1024));
    const sizeRisk = typeof row.projectSizeIndex === 'number'
      ? row.projectSizeIndex
      : normalizeProjectSizeIndex(totalGb, avgProjectGb);
    risks.push(sizeRisk);

    if (totalGb >= 40) {
      hugeProjects.push(`${row.projectKey} (${totalGb.toFixed(1)}GB)`);
      hugeKeys.push(row.projectKey);
      continue;
    }

    if (totalGb > LARGE_PROJECT_GB) {
      largeProjects.push(`${row.projectKey} (${totalGb.toFixed(1)}GB)`);
      largeKeys.push(row.projectKey);
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
      whitelistRule: 'project-size',
      whitelistItems: hugeKeys,
    });
  }

  if (largeProjects.length > 0) {
    const preview = largeProjects.slice(0, 5).join(', ');
    const more = largeProjects.length > 5 ? ` and ${largeProjects.length - 5} more` : '';
    issues.push({
      id: 'project-size-large-group',
      category: 'project_footprint',
      severity: 'warning',
      title: `${largeProjects.length} project${largeProjects.length > 1 ? 's' : ''} exceed ${LARGE_PROJECT_GB}GB`,
      description: `${preview}${more}. Projects this large usually hide accumulating webapp logs or filesystem data that belongs on block storage.`,
      recommendation: 'Inspect what fills each project (or whitelist it if the size is legitimate).',
      threshold: `≤${LARGE_PROJECT_GB}GB`,
      whitelistRule: 'project-size',
      whitelistItems: largeKeys,
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
      whitelistRule: 'project-size',
      whitelistItems: criticalSizeProjects,
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
      whitelistRule: 'project-size',
      whitelistItems: highSizeProjects,
    });
  }

  const avgRisk = risks.length > 0 ? risks.reduce((sum, v) => sum + v, 0) / risks.length : 0;
  const score = Math.max(0, Math.min(100, 100 * (1 - avgRisk)));
  return { score, issues };
}

/**
 * Broken actively-used connections (rubric: always-lead critical). Issues
 * only — the connections category stays zero-weighted; `cap-connection-broken`
 * clamps the overall score via the existing cap logic. `skipped` rows are
 * ignored. Usage arrays `undefined` means the usage scan has not completed
 * this session ⇒ failing connections surface as 'unverified' (warning) with a
 * pointer to the Insights scan. A project counts against a broken connection
 * only when it BOTH uses it AND has an active scenario with an active trigger
 * (`activeTriggerProjects`; undefined ⇒ every using project counts). NOTE:
 * health results carry no recency, so only "currently failing" is knowable —
 * the rubric's "broken recently" is a documented deviation.
 */
function scoreConnectionHealth(
  health: ParsedData['connectionHealth'] & object,
  datasetUsages: ParsedData['connectionDatasetUsages'],
  llmUsages: ParsedData['connectionLlmUsages'],
  activeTriggerProjects: ParsedData['connectionActiveTriggerProjects'],
  isWhitelisted: IsWhitelisted,
): HealthIssue[] {
  const failing = health.filter(
    (c) => c.status === 'fail' && !isWhitelisted('connection-broken', c.name),
  );
  if (failing.length === 0) return [];

  const preview = (names: string[]) =>
    names.slice(0, 5).join(', ') + (names.length > 5 ? ` and ${names.length - 5} more` : '');

  if (datasetUsages === undefined && llmUsages === undefined) {
    const names = failing.map((c) => c.name);
    return [{
      id: 'connection-broken-unverified',
      category: 'connections',
      severity: 'warning',
      title: `${failing.length} connection${failing.length > 1 ? 's' : ''} failing their test (usage unverified)`,
      description: `${preview(names)}. The connection usage scan has not completed, so it is unknown whether projects actively depend on these connections.`,
      recommendation: 'Run the usage scan on the Connections → Insights page to confirm impact.',
      value: failing.length,
      whitelistRule: 'connection-broken',
      whitelistItems: names,
    }];
  }

  const dsByName = new Map((datasetUsages ?? []).map((u) => [u.name, u]));
  const llmByName = new Map((llmUsages ?? []).map((u) => [u.name, u]));
  const activeSet = activeTriggerProjects && new Set(activeTriggerProjects);
  const usedNames: string[] = [];
  const usedPreview: string[] = [];
  const unusedNames: string[] = [];
  for (const conn of failing) {
    const projects = [
      ...(dsByName.get(conn.name)?.projects ?? []),
      ...(llmByName.get(conn.name)?.projects ?? []),
    ].map((p) => p.projectKey);
    const n = new Set(activeSet ? projects.filter((k) => activeSet.has(k)) : projects).size;
    if (n > 0) {
      usedNames.push(conn.name);
      usedPreview.push(`${conn.name} (${n} project${n === 1 ? '' : 's'})`);
    } else {
      unusedNames.push(conn.name);
    }
  }

  const issues: HealthIssue[] = [];
  if (usedNames.length > 0) {
    issues.push({
      id: 'cap-connection-broken',
      category: 'connections',
      severity: 'critical',
      title: `${usedNames.length} actively-used connection${usedNames.length > 1 ? 's' : ''} failing their test`,
      description: `${preview(usedPreview)}. Projects with active scenario triggers depend on these connections — automated workloads on them are broken right now.`,
      recommendation: 'Repair these connections immediately (credentials, network, or endpoint).',
      value: usedNames.length,
      whitelistRule: 'connection-broken',
      whitelistItems: usedNames,
    });
  }
  if (unusedNames.length > 0) {
    issues.push({
      id: 'connection-broken-unused',
      category: 'connections',
      severity: 'info',
      title: `${unusedNames.length} unused connection${unusedNames.length > 1 ? 's' : ''} failing their test`,
      description: `${preview(unusedNames)}. No project with an active scenario trigger references these connections — a failing test alone is low-impact mess.`,
      recommendation: 'Repair or delete these unused connections.',
      value: unusedNames.length,
      whitelistRule: 'connection-broken',
      whitelistItems: unusedNames,
    });
  }
  return issues;
}

/**
 * K8s exec configs missing memory requests/limits (rubric: important — OOM
 * risk the scheduler cannot manage). Unset OR <=0 counts as "not set"
 * (rules/dss_drift.py semantics). Group issue, code-env-size-group pattern.
 * CPU-missing is mentioned in the description only — no OOM risk, so it
 * neither counts nor escalates.
 */
function scoreExecConfigResources(
  configs: ExecResourceConfig[],
  isWhitelisted: IsWhitelisted,
): { score: number; issues: HealthIssue[] } {
  const unset = (v: number | null | undefined) => typeof v !== 'number' || v <= 0;
  const k8sConfigs = configs.filter((c) => String(c.type || '').toUpperCase() === 'KUBERNETES');
  const offending = k8sConfigs.filter(
    (c) =>
      (unset(c.memRequestMB) || unset(c.memLimitMB)) &&
      !isWhitelisted('exec-config-resources', c.name),
  );
  if (offending.length === 0) return { score: 100, issues: [] };

  const cpuMissing = offending.filter((c) => unset(c.cpuRequest) || unset(c.cpuLimit)).length;
  const names = offending.map((c) => c.name);
  const preview = names.slice(0, 5).join(', ');
  const more = names.length > 5 ? ` and ${names.length - 5} more` : '';
  const cpuNote = cpuMissing > 0
    ? ` ${cpuMissing} of them also lack CPU requests/limits.`
    : '';
  return {
    score: Math.max(30, 100 - offending.length * 25),
    issues: [{
      id: 'exec-config-resources-group',
      category: 'runtime_config',
      severity: 'warning',
      title: `${offending.length} Kubernetes exec config${offending.length > 1 ? 's' : ''} without memory requests/limits`,
      description: `${preview}${more}. Containers on these configs run with unbounded memory — the scheduler cannot protect the node, so a single heavy job can evict or OOM-kill its neighbors.${cpuNote}`,
      recommendation: 'Set memRequestMB and memLimitMB on each containerized execution config.',
      value: offending.length,
      whitelistRule: 'exec-config-resources',
      whitelistItems: names,
    }],
  };
}

const SANITY_MAX_ISSUES = 5;
const SANITY_DESC_MAX = 280;
// Codes exempt from scoring by default (no whitelist entry needed): routine
// post-upgrade housekeeping, not an instance-health signal. Exempt messages
// still render raw on the Sanity Check page. Mirrors _SANITY_SCORE_EXEMPT_CODES
// in atk_agent_common/health.py — keep the twins in sync.
const SANITY_SCORE_EXEMPT_CODES = new Set(['WARN_GIT_PROJECT_NOT_MIGRATED']);

/**
 * DSS's own sanity check: one issue per distinct surviving ERROR code
 * (warning severity) / WARNING code (info severity), ERRORs first, stable
 * code sort, capped at 5. Component severity is computed from the
 * whitelist-FILTERED messages, never from raw sanityCheckMaxSeverity — a
 * whitelisted lone ERROR must not still drag the score.
 */
function scoreSanityCheck(
  messages: ParsedData['sanityCheck'] & object,
  isWhitelisted: IsWhitelisted,
): { score: number; issues: HealthIssue[] } {
  const surviving = messages.filter(
    (m) =>
      (m.severity === 'ERROR' || m.severity === 'WARNING') &&
      !SANITY_SCORE_EXEMPT_CODES.has(String(m.code)) &&
      !isWhitelisted('sanity-check', String(m.code)),
  );
  if (surviving.length === 0) return { score: 100, issues: [] };

  const byCode = (severity: 'ERROR' | 'WARNING') => {
    const map = new Map<string, { first: (typeof surviving)[number]; count: number }>();
    for (const m of surviving) {
      if (m.severity !== severity) continue;
      const code = String(m.code);
      const entry = map.get(code);
      if (entry) entry.count += 1;
      else map.set(code, { first: m, count: 1 });
    }
    return [...map.entries()].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  };

  const errorCodes = byCode('ERROR');
  const warningCodes = byCode('WARNING');
  const issues: HealthIssue[] = [];
  const push = (
    code: string,
    entry: { first: { title?: string; message?: string; details?: string }; count: number },
    kind: 'error' | 'warning',
  ) => {
    if (issues.length >= SANITY_MAX_ISSUES) return;
    const raw = String(entry.first.message || entry.first.details || '');
    const description =
      raw.length > SANITY_DESC_MAX ? `${raw.slice(0, SANITY_DESC_MAX)}…` : raw;
    issues.push({
      id: `sanity-${kind}-${code}`,
      category: 'runtime_config',
      severity: kind === 'error' ? 'warning' : 'info',
      title: `Sanity check ${kind === 'error' ? 'error' : 'warning'}: ${entry.first.title || code}`,
      description,
      recommendation: 'Review this finding on the DSS sanity check (Administration → Maintenance).',
      value: entry.count,
      whitelistRule: 'sanity-check',
      whitelistItems: [code],
    });
  };
  for (const [code, entry] of errorCodes) push(code, entry, 'error');
  for (const [code, entry] of warningCodes) push(code, entry, 'warning');

  const score = errorCodes.length > 0 ? 40 : 75;
  return { score, issues };
}

/**
 * Standalone function to calculate health score from parsed data
 * Use this when you need to calculate outside of React component context
 */
export function calculateHealthScore(
  parsedData: ParsedData,
  factorToggles: Partial<HealthFactorToggles> = DEFAULT_HEALTH_FACTOR_TOGGLES,
  thresholdOverrides?: Partial<ThresholdSettings>,
  whitelist?: FindingWhitelistEntry[]
): HealthScore {
    const toggles: HealthFactorToggles = {
      ...DEFAULT_HEALTH_FACTOR_TOGGLES,
      ...factorToggles,
    };
    const t: ThresholdSettings = thresholdOverrides as ThresholdSettings;
    const isWhitelisted = buildWhitelistLookup(whitelist);
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
    // VERSION CURRENCY (10%)
    // ============================================
    const pythonResult = scorePythonVersion(parsedData.codeEnvs, parsedData.dssVersion, t, isWhitelisted);
    const sparkResult = scoreSparkVersion(parsedData.sparkSettings);

    const versionCurrencyScore = combineEnabledScores([
      { enabled: toggles.python_versions, score: pythonResult.score, weight: 0.7 },
      { enabled: toggles.spark_version, score: sparkResult.score, weight: 0.3 },
    ]);
    const versionCurrencyIssues: HealthIssue[] = [];
    if (toggles.python_versions) versionCurrencyIssues.push(...pythonResult.issues);
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
    // SYSTEM CAPACITY (30%)
    // ============================================
    const memoryResult = scoreMemoryAvailability(parsedData.memoryInfo);
    const filesystemResult = scoreFilesystem(parsedData.filesystemInfo, isWhitelisted);
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

    // Critical cap rules living on the data mount (rubric A5/A6): DIP_HOME on
    // NFS; data mount >= dataMountCriticalPct full. Deterministic, from the
    // dipHomeStorage overview signal — absent (older remote toolkits, macOS
    // dev) means the rules silently skip.
    const dipHome = parsedData.dipHomeStorage;
    if (toggles.filesystem_capacity && dipHome) {
      if (String(dipHome.fsType || '').toLowerCase().startsWith('nfs')) {
        systemCapacityIssues.push({
          id: 'cap-diphome-nfs',
          category: 'system_capacity',
          severity: 'critical',
          title: `DIP_HOME is on NFS (${dipHome.fsType})`,
          description: `The DSS data directory (${dipHome.path || 'DIP_HOME'}) sits on an NFS mount (${dipHome.mount || '?'}). NFS under DIP_HOME causes pervasive performance and locking problems.`,
          recommendation: 'Move DIP_HOME to local or block storage.',
          value: dipHome.fsType,
        });
      }
      const dataMountCritical = t?.dataMountCriticalPct ?? 75;
      if (typeof dipHome.usedPct === 'number' && dipHome.usedPct >= dataMountCritical) {
        systemCapacityIssues.push({
          id: 'cap-data-mount-full',
          category: 'system_capacity',
          severity: 'critical',
          title: `Data mount ${dipHome.usedPct}% full (${dipHome.mount || 'DIP_HOME'})`,
          description: `The mount holding DIP_HOME is at ${dipHome.usedPct}% — past the ${dataMountCritical}% critical line. DSS misbehaves unpredictably when the data disk fills.`,
          recommendation: 'Free space now (job logs, exports, large managed folders) or expand the volume.',
          value: `${dipHome.usedPct}%`,
          threshold: `<${dataMountCritical}%`,
        });
      }
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
    // SECURITY ISOLATION (0% — issues still surface)
    // ============================================
    const userIsolationIssue = securityResult.issues.find((i) => i.id === 'impersonation-disabled');
    const cgroupsDisabledIssue = securityResult.issues.find((i) => i.id === 'cgroups-disabled');
    const cgroupsEmptyIssue = securityResult.issues.find((i) => i.id === 'cgroups-empty-targets');
    const securityIssues: HealthIssue[] = [];
    let securityIsolationScore = 100;

    if (toggles.user_isolation && userIsolationIssue) {
      securityIsolationScore -= 25;
      securityIssues.push(userIsolationIssue);
    }
    securityIsolationScore = Math.max(0, securityIsolationScore);

    categoryScores.push({
      category: 'security_isolation',
      label: 'Security Isolation',
      score: securityIsolationScore,
      weight: categoryWeights.security_isolation,
      issues: securityIssues,
    });
    allIssues.push(...securityIssues);

    // ============================================
    // CODE ENVIRONMENTS (15%)
    // ============================================
    const codeEnvResult = toggles.code_envs_per_project
      ? scoreCodeEnvComplexity(parsedData.projectFootprint, isWhitelisted)
      : { score: 100, issues: [] as HealthIssue[] };
    const codeEnvSizeResult = scoreCodeEnvSize(parsedData.codeEnvs, isWhitelisted);
    const codeEnvsScore = combineEnabledScores([
      { enabled: toggles.code_envs_per_project, score: codeEnvResult.score, weight: 0.7 },
      { enabled: true, score: codeEnvSizeResult.score, weight: 0.3 },
    ]);
    const codeEnvsIssues = [...codeEnvResult.issues, ...codeEnvSizeResult.issues];
    categoryScores.push({
      category: 'code_envs',
      label: 'Code Envs',
      score: codeEnvsScore,
      weight: categoryWeights.code_envs,
      issues: codeEnvsIssues,
    });
    allIssues.push(...codeEnvsIssues);

    // ============================================
    // PROJECT FOOTPRINT (15%)
    // ============================================
    const projectFootprintResult = toggles.project_size_pressure
      ? scoreProjectSizePressure(parsedData.projectFootprint, parsedData.projectFootprintSummary, isWhitelisted)
      : { score: 100, issues: [] as HealthIssue[] };
    categoryScores.push({
      category: 'project_footprint',
      label: 'Project Footprint',
      score: projectFootprintResult.score,
      weight: categoryWeights.project_footprint,
      issues: projectFootprintResult.issues,
    });
    allIssues.push(...projectFootprintResult.issues);

    // ============================================
    // RUNTIME CONFIGURATION (30%)
    // ============================================
    const disabledResult = scoreDisabledFeatures(parsedData.disabledFeatures);
    const javaMemoryResult = scoreJavaMemory(parsedData.javaMemorySettings);
    const runtimeDbResult = scoreRuntimeDatabase(parsedData.generalSettings);

    // cgroups component (relocated here from security_isolation — rubric A1:
    // it is a runtime-blowup risk, and zero-weighting security must not lose it).
    let cgroupsScore = 100;
    const runtimeConfigIssues: HealthIssue[] = [];
    if (toggles.cgroups_enabled && cgroupsDisabledIssue) {
      cgroupsScore -= 60;
      runtimeConfigIssues.push(cgroupsDisabledIssue);
    }
    if (toggles.cgroups_empty_targets && cgroupsEmptyIssue) {
      cgroupsScore -= 20;
      runtimeConfigIssues.push(cgroupsEmptyIssue);
    }
    cgroupsScore = Math.max(0, cgroupsScore);
    const cgroupsChecksEnabled = toggles.cgroups_enabled || toggles.cgroups_empty_targets;

    // Exec-config resources + DSS sanity check (rubric-mandated inputs).
    // Input `undefined` (old payloads, zip mode, sanity 501) ⇒ component
    // disabled ⇒ combineEnabledScores renormalizes the 4×0.20 legacy
    // components back to exactly the old 4×0.25 behavior.
    const execConfigsEnabled =
      toggles.exec_config_resources && parsedData.execResourceConfigs !== undefined;
    const execResourcesResult = execConfigsEnabled
      ? scoreExecConfigResources(parsedData.execResourceConfigs as ExecResourceConfig[], isWhitelisted)
      : { score: 100, issues: [] as HealthIssue[] };
    const sanityEnabled = toggles.sanity_check && parsedData.sanityCheck !== undefined;
    const sanityResult = sanityEnabled
      ? scoreSanityCheck(parsedData.sanityCheck!, isWhitelisted)
      : { score: 100, issues: [] as HealthIssue[] };

    const runtimeConfigScore = combineEnabledScores([
      { enabled: toggles.disabled_features, score: disabledResult.score, weight: 0.20 },
      { enabled: toggles.java_memory_limits, score: javaMemoryResult.score, weight: 0.20 },
      { enabled: toggles.runtime_database, score: runtimeDbResult.score, weight: 0.20 },
      { enabled: cgroupsChecksEnabled, score: cgroupsScore, weight: 0.20 },
      { enabled: execConfigsEnabled, score: execResourcesResult.score, weight: 0.10 },
      { enabled: sanityEnabled, score: sanityResult.score, weight: 0.10 },
    ]);
    if (toggles.java_memory_limits) {
      runtimeConfigIssues.push(...javaMemoryResult.issues);
    }
    if (toggles.disabled_features && disabledResult.issue) {
      runtimeConfigIssues.push(disabledResult.issue);
    }
    if (toggles.runtime_database && runtimeDbResult.issue) {
      runtimeConfigIssues.push(runtimeDbResult.issue);
    }
    if (execConfigsEnabled) {
      runtimeConfigIssues.push(...execResourcesResult.issues);
    }
    if (sanityEnabled) {
      runtimeConfigIssues.push(...sanityResult.issues);
    }

    // Cap rule (rubric A1): impersonation (multi-user security) on but cgroups
    // not configured — the classic preventable-outage configuration.
    const impersonationOn = ((parsedData.generalSettings as
      | { impersonation?: { enabled?: boolean } }
      | undefined)?.impersonation?.enabled === true);
    if (toggles.cgroups_enabled && cgroupsDisabledIssue && impersonationOn) {
      runtimeConfigIssues.push({
        id: 'cap-cgroups-missing',
        category: 'runtime_config',
        severity: 'critical',
        title: 'Multi-user isolation is on but cgroups are not configured',
        description: 'User isolation (impersonation) is enabled but cgroup resource limits are not — a single runaway kernel or job can take down the host for every user.',
        recommendation: 'Configure cgroup memory limits for kernels and jobs now.',
      });
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
    // CONNECTIONS (issues only — category stays zero-weighted, no score bar;
    // cap-connection-broken clamps via the existing cap logic below)
    // ============================================
    if (toggles.connection_health && parsedData.connectionHealth !== undefined) {
      // Usage data counts as "present" only once the usage scan COMPLETED —
      // mid-scan the arrays exist but are partial ([]), which would
      // misclassify a used broken connection as unused. The Python twin never
      // sets connectionUsageLoading, so absent-lifecycle means the arrays are
      // authoritative there.
      const usageReady =
        parsedData.connectionDatasetUsages !== undefined &&
        (parsedData.connectionUsageLoading === undefined ||
          parsedData.connectionUsageLoading.phase === 'done');
      allIssues.push(...scoreConnectionHealth(
        parsedData.connectionHealth,
        usageReady ? parsedData.connectionDatasetUsages : undefined,
        usageReady ? (parsedData.connectionLlmUsages ?? []) : undefined,
        usageReady ? parsedData.connectionActiveTriggerProjects : undefined,
        isWhitelisted,
      ));
    }

    // ============================================
    // CALCULATE OVERALL SCORE
    // ============================================
    let overallScore = categoryScores.reduce((sum, cat) => {
      return sum + (cat.score * cat.weight);
    }, 0);

    // Deduplicate issues by id
    const uniqueIssues = allIssues.filter(
      (issue, index, self) => index === self.findIndex(i => i.id === issue.id)
    );

    // Critical cap (interview K100): any always-lead rule (issue id `cap-*`)
    // clamps the overall score into the critical band — a weighted average
    // must not dilute a disqualifying configuration.
    const capped = uniqueIssues.some((i) => i.id.startsWith('cap-'));
    if (capped) {
      overallScore = Math.min(overallScore, t?.healthCriticalCapScore ?? 49);
    }

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
    capped,
    whitelistSuppressed: isWhitelisted.matched.size,
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
  const { entries } = whitelistStore.use();
  return useMemo(
    () => calculateHealthScore(parsedData, factorToggles, thresholds, activeHostWhitelist(entries)),
    [parsedData, factorToggles, thresholds, entries],
  );
}
