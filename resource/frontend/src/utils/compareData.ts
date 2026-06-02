import type {
  DiagFile,
  ParsedData,
  ComparisonResult,
  FieldDelta,
  CollectionDelta,
  DeltaSection,
  ChangeType,
  DeltaDirection,
  DeltaSeverity,
  User,
  Project,
  Cluster,
  CodeEnv,
  HealthIssue,
} from '../types';

/**
 * Compare two scalar values and determine change type
 */
export function compareScalars(
  before: unknown,
  after: unknown
): { changed: boolean; changeType: ChangeType } {
  if (before === undefined && after === undefined) {
    return { changed: false, changeType: 'unchanged' };
  }
  if (before === undefined && after !== undefined) {
    return { changed: true, changeType: 'added' };
  }
  if (before !== undefined && after === undefined) {
    return { changed: true, changeType: 'removed' };
  }
  if (JSON.stringify(before) !== JSON.stringify(after)) {
    return { changed: true, changeType: 'modified' };
  }
  return { changed: false, changeType: 'unchanged' };
}

/**
 * Compare two records and return field deltas
 */
export function compareRecords(
  before: Record<string, unknown> | undefined,
  after: Record<string, unknown> | undefined,
  category: string,
  labelMap?: Record<string, string>
): FieldDelta[] {
  const deltas: FieldDelta[] = [];
  const beforeObj = before || {};
  const afterObj = after || {};
  const allKeys = new Set([...Object.keys(beforeObj), ...Object.keys(afterObj)]);

  for (const key of allKeys) {
    const beforeVal = beforeObj[key];
    const afterVal = afterObj[key];
    const { changed, changeType } = compareScalars(beforeVal, afterVal);

    if (changed) {
      const numericDelta = getNumericDelta(beforeVal, afterVal);
      const direction = determineDirection(key, beforeVal, afterVal, numericDelta);
      const severity = determineSeverity(key, changeType, direction);

      deltas.push({
        field: key,
        label: labelMap?.[key] || formatFieldLabel(key),
        category,
        before: beforeVal,
        after: afterVal,
        changeType,
        direction,
        severity,
        numericDelta,
        percentChange: getPercentChange(beforeVal, afterVal),
      });
    }
  }

  return deltas;
}

/**
 * Compare two collections (arrays of objects) using a key function
 */
export function compareCollections<T>(
  before: T[] | undefined,
  after: T[] | undefined,
  keyFn: (item: T) => string,
  compareFn?: (a: T, b: T) => string[]
): CollectionDelta<T> {
  const beforeArr = before || [];
  const afterArr = after || [];

  const beforeMap = new Map(beforeArr.map((item) => [keyFn(item), item]));
  const afterMap = new Map(afterArr.map((item) => [keyFn(item), item]));

  const added: T[] = [];
  const removed: T[] = [];
  const modified: Array<{ before: T; after: T; changes: string[] }> = [];
  let unchanged = 0;

  // Find added and modified
  for (const [key, afterItem] of afterMap) {
    const beforeItem = beforeMap.get(key);
    if (!beforeItem) {
      added.push(afterItem);
    } else if (compareFn) {
      const changes = compareFn(beforeItem, afterItem);
      if (changes.length > 0) {
        modified.push({ before: beforeItem, after: afterItem, changes });
      } else {
        unchanged++;
      }
    } else if (JSON.stringify(beforeItem) !== JSON.stringify(afterItem)) {
      modified.push({ before: beforeItem, after: afterItem, changes: ['Changed'] });
    } else {
      unchanged++;
    }
  }

  // Find removed
  for (const [key, beforeItem] of beforeMap) {
    if (!afterMap.has(key)) {
      removed.push(beforeItem);
    }
  }

  return { added, removed, modified, unchanged };
}

/**
 * Compare users with detailed change detection
 */
function compareUsers(before: User, after: User): string[] {
  const changes: string[] = [];
  if (before.email !== after.email) changes.push('email');
  if (before.enabled !== after.enabled) changes.push('enabled');
  if (before.userProfile !== after.userProfile) changes.push('userProfile');
  return changes;
}

/**
 * Compare projects with detailed change detection
 */
function compareProjects(before: Project, after: Project): string[] {
  const changes: string[] = [];
  if (before.name !== after.name) changes.push('name');
  if (before.owner !== after.owner) changes.push('owner');
  if (before.versionNumber !== after.versionNumber) changes.push('version');
  if (JSON.stringify(before.permissions) !== JSON.stringify(after.permissions)) {
    changes.push('permissions');
  }
  return changes;
}

/**
 * Compare clusters with detailed change detection
 */
function compareClusters(before: Cluster, after: Cluster): string[] {
  const changes: string[] = [];
  if (before.version !== after.version) changes.push('version');
  if (before.status !== after.status) changes.push('status');
  if (before.region !== after.region) changes.push('region');
  if (JSON.stringify(before.nodeGroups) !== JSON.stringify(after.nodeGroups)) {
    changes.push('nodeGroups');
  }
  return changes;
}

/**
 * Compare code envs with detailed change detection
 */
function compareCodeEnvs(before: CodeEnv, after: CodeEnv): string[] {
  const changes: string[] = [];
  if (before.version !== after.version) changes.push('version');
  if (before.language !== after.language) changes.push('language');
  return changes;
}

/**
 * Compute numeric delta between values
 */
function getNumericDelta(before: unknown, after: unknown): number | undefined {
  const beforeNum = parseNumeric(before);
  const afterNum = parseNumeric(after);
  if (beforeNum !== undefined && afterNum !== undefined) {
    return afterNum - beforeNum;
  }
  return undefined;
}

/**
 * Get percent change between values
 */
function getPercentChange(before: unknown, after: unknown): number | undefined {
  const beforeNum = parseNumeric(before);
  const afterNum = parseNumeric(after);
  if (beforeNum !== undefined && afterNum !== undefined && beforeNum !== 0) {
    return ((afterNum - beforeNum) / beforeNum) * 100;
  }
  return undefined;
}

/**
 * Parse numeric value from various inputs
 */
function parseNumeric(value: unknown): number | undefined {
  if (typeof value === 'number') return value;
  if (typeof value === 'string') {
    // Handle memory strings like "8G", "1024M"
    const memMatch = value.match(/^([\d.]+)\s*([KMGTP])?B?$/i);
    if (memMatch) {
      const num = parseFloat(memMatch[1]);
      const unit = memMatch[2]?.toUpperCase();
      const multipliers: Record<string, number> = { K: 1024, M: 1024 ** 2, G: 1024 ** 3, T: 1024 ** 4 };
      return num * (multipliers[unit || ''] || 1);
    }
    const num = parseFloat(value);
    if (!isNaN(num)) return num;
  }
  return undefined;
}

/**
 * Determine if change is improvement, regression, or neutral
 */
function determineDirection(
  field: string,
  before: unknown,
  after: unknown,
  numericDelta?: number
): DeltaDirection {
  // Fields where higher is better
  const higherIsBetter = [
    'cpuCores',
    'memory',
    'total',
    'available',
    'healthScore',
  ];

  // Fields where lower is better
  const lowerIsBetter = [
    'errorCount',
    'warningCount',
    'criticalCount',
    'used',
    'Use%',
  ];

  // Version comparisons - newer is better
  if (field.toLowerCase().includes('version')) {
    const beforeStr = String(before || '');
    const afterStr = String(after || '');
    if (beforeStr < afterStr) return 'improvement';
    if (beforeStr > afterStr) return 'regression';
    return 'neutral';
  }

  if (numericDelta !== undefined) {
    const fieldLower = field.toLowerCase();
    if (higherIsBetter.some((f) => fieldLower.includes(f.toLowerCase()))) {
      return numericDelta > 0 ? 'improvement' : numericDelta < 0 ? 'regression' : 'neutral';
    }
    if (lowerIsBetter.some((f) => fieldLower.includes(f.toLowerCase()))) {
      return numericDelta < 0 ? 'improvement' : numericDelta > 0 ? 'regression' : 'neutral';
    }
  }

  return 'neutral';
}

/**
 * Determine severity of a change
 */
function determineSeverity(
  field: string,
  _changeType: ChangeType,
  direction: DeltaDirection
): DeltaSeverity {
  // Critical fields
  const criticalFields = ['dssVersion', 'pythonVersion', 'license', 'auth'];
  const fieldLower = field.toLowerCase();

  if (criticalFields.some((f) => fieldLower.includes(f.toLowerCase()))) {
    if (direction === 'regression') return 'critical';
    return 'warning';
  }

  if (direction === 'regression') return 'warning';
  return 'info';
}

/**
 * Format field name to human-readable label
 */
function formatFieldLabel(field: string): string {
  return field
    .replace(/([A-Z])/g, ' $1')
    .replace(/[._]/g, ' ')
    .replace(/^\s+/, '')
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();
}

/**
 * Compare health issues and find new/resolved
 */
export function compareHealthIssues(
  beforeIssues: HealthIssue[],
  afterIssues: HealthIssue[]
): { newIssues: HealthIssue[]; resolvedIssues: HealthIssue[]; persistingIssues: HealthIssue[] } {
  const beforeIds = new Set(beforeIssues.map((i) => i.id));
  const afterIds = new Set(afterIssues.map((i) => i.id));

  const newIssues = afterIssues.filter((i) => !beforeIds.has(i.id));
  const resolvedIssues = beforeIssues.filter((i) => !afterIds.has(i.id));
  const persistingIssues = afterIssues.filter((i) => beforeIds.has(i.id));

  return { newIssues, resolvedIssues, persistingIssues };
}

/**
 * Compute full comparison between two diagnostic files
 */
export function computeFullComparison(before: DiagFile, after: DiagFile): ComparisonResult {
  const beforeData = before.parsedData;
  const afterData = after.parsedData;
  const beforeHealth = before.healthScore;
  const afterHealth = after.healthScore;

  // Track all deltas for summary
  const allDeltas: FieldDelta[] = [];

  // System section
  const systemDeltas = compareRecords(
    {
      dssVersion: beforeData.dssVersion,
      pythonVersion: beforeData.pythonVersion,
      cpuCores: beforeData.cpuCores,
      osInfo: beforeData.osInfo,
      lastRestartTime: beforeData.lastRestartTime,
    },
    {
      dssVersion: afterData.dssVersion,
      pythonVersion: afterData.pythonVersion,
      cpuCores: afterData.cpuCores,
      osInfo: afterData.osInfo,
      lastRestartTime: afterData.lastRestartTime,
    },
    'system',
    {
      dssVersion: 'DSS Version',
      pythonVersion: 'Python Version',
      cpuCores: 'CPU Cores',
      osInfo: 'Operating System',
      lastRestartTime: 'Last Restart',
    }
  );
  allDeltas.push(...systemDeltas);

  // Memory info section
  const memoryDeltas = compareRecords(
    beforeData.memoryInfo as Record<string, unknown> | undefined,
    afterData.memoryInfo as Record<string, unknown> | undefined,
    'Memory'
  );
  allDeltas.push(...memoryDeltas);

  // Config section - all settings
  const configDeltas: FieldDelta[] = [];

  const settingsTypes = [
    { before: beforeData.enabledSettings, after: afterData.enabledSettings, cat: 'Enabled Settings' },
    { before: beforeData.sparkSettings, after: afterData.sparkSettings, cat: 'Spark Settings' },
    { before: beforeData.authSettings, after: afterData.authSettings, cat: 'Auth Settings' },
    { before: beforeData.containerSettings, after: afterData.containerSettings, cat: 'Container Settings' },
    { before: beforeData.integrationSettings, after: afterData.integrationSettings, cat: 'Integration Settings' },
    { before: beforeData.resourceLimits, after: afterData.resourceLimits, cat: 'Resource Limits' },
    { before: beforeData.cgroupSettings, after: afterData.cgroupSettings, cat: 'CGroup Settings' },
    { before: beforeData.proxySettings, after: afterData.proxySettings, cat: 'Proxy Settings' },
    { before: beforeData.maxRunningActivities, after: afterData.maxRunningActivities, cat: 'Max Running Activities' },
    { before: beforeData.javaMemorySettings, after: afterData.javaMemorySettings, cat: 'Java Memory' },
    { before: beforeData.javaMemoryLimits, after: afterData.javaMemoryLimits, cat: 'Java Memory Limits' },
  ];

  for (const { before: b, after: a, cat } of settingsTypes) {
    const deltas = compareRecords(
      b as Record<string, unknown> | undefined,
      a as Record<string, unknown> | undefined,
      cat
    );
    configDeltas.push(...deltas);
  }
  allDeltas.push(...configDeltas);

  // Scale section
  const scaleDeltas = compareRecords(
    {
      userCount: beforeData.users?.length,
      projectCount: beforeData.projects?.length,
      pluginCount: beforeData.pluginsCount ?? beforeData.plugins?.length,
      clusterCount: beforeData.clusters?.length,
      codeEnvCount: beforeData.codeEnvs?.length,
      ...beforeData.userStats,
    },
    {
      userCount: afterData.users?.length,
      projectCount: afterData.projects?.length,
      pluginCount: afterData.pluginsCount ?? afterData.plugins?.length,
      clusterCount: afterData.clusters?.length,
      codeEnvCount: afterData.codeEnvs?.length,
      ...afterData.userStats,
    },
    'scale',
    {
      userCount: 'Total Users',
      projectCount: 'Total Projects',
      pluginCount: 'Plugins',
      clusterCount: 'K8s Clusters',
      codeEnvCount: 'Code Environments',
    }
  );
  allDeltas.push(...scaleDeltas);

  // Infrastructure section (connections)
  const infraDeltas = compareRecords(
    beforeData.connectionCounts as Record<string, unknown> | undefined,
    afterData.connectionCounts as Record<string, unknown> | undefined,
    'infrastructure'
  );
  allDeltas.push(...infraDeltas);

  // Collections
  const usersComparison = compareCollections(
    beforeData.users,
    afterData.users,
    (u) => u.login,
    compareUsers
  );

  const projectsComparison = compareCollections(
    beforeData.projects,
    afterData.projects,
    (p) => p.key,
    compareProjects
  );

  const clustersComparison = compareCollections(
    beforeData.clusters,
    afterData.clusters,
    (c) => c.name,
    compareClusters
  );

  const codeEnvsComparison = compareCollections(
    beforeData.codeEnvs,
    afterData.codeEnvs,
    (e) => e.name,
    compareCodeEnvs
  );

  const pluginsComparison = compareCollections(
    beforeData.plugins,
    afterData.plugins,
    (p) => p
  );

  // Critical section - changes that need immediate attention
  const criticalDeltas = allDeltas.filter((d) => d.severity === 'critical');

  // Health comparison
  const healthChange =
    (afterHealth?.overall ?? 0) - (beforeHealth?.overall ?? 0);
  const healthDirection: DeltaDirection =
    healthChange > 0 ? 'improvement' : healthChange < 0 ? 'regression' : 'neutral';

  // Calculate summary counts
  const improvementDeltas = allDeltas.filter((d) => d.direction === 'improvement');
  const regressionDeltas = allDeltas.filter((d) => d.direction === 'regression');
  const improvements = improvementDeltas.length;
  const regressions = regressionDeltas.length;
  const neutral = allDeltas.filter((d) => d.direction === 'neutral').length;

  // Add collection changes to totals
  const collectionChanges =
    usersComparison.added.length +
    usersComparison.removed.length +
    usersComparison.modified.length +
    projectsComparison.added.length +
    projectsComparison.removed.length +
    projectsComparison.modified.length +
    clustersComparison.added.length +
    clustersComparison.removed.length +
    clustersComparison.modified.length +
    codeEnvsComparison.added.length +
    codeEnvsComparison.removed.length +
    codeEnvsComparison.modified.length +
    pluginsComparison.added.length +
    pluginsComparison.removed.length;

  const totalChanges = allDeltas.length + collectionChanges;

  // Build sections
  const buildSection = (
    id: string,
    label: string,
    icon: string,
    deltas: FieldDelta[]
  ): DeltaSection => ({
    id,
    label,
    icon,
    deltas,
    changeCount: deltas.length,
  });

  return {
    computedAt: new Date(),
    summary: {
      totalChanges,
      improvements,
      regressions,
      neutral,
      critical: criticalDeltas.length,
      improvementDeltas,
      regressionDeltas,
    },
    health: {
      before: beforeHealth?.overall ?? 0,
      after: afterHealth?.overall ?? 0,
      change: healthChange,
      direction: healthDirection,
    },
    sections: {
      critical: buildSection('critical', 'Critical Changes', '⚠️', criticalDeltas),
      system: buildSection('system', 'System', '💻', systemDeltas),
      versions: buildSection('versions', 'Versions & Memory', '📦', memoryDeltas),
      config: buildSection('config', 'Configuration', '⚙️', configDeltas),
      scale: buildSection('scale', 'Scale', '📊', scaleDeltas),
      infrastructure: buildSection('infrastructure', 'Infrastructure', '☸️', infraDeltas),
    },
    collections: {
      users: usersComparison,
      projects: projectsComparison,
      clusters: clustersComparison,
      codeEnvs: codeEnvsComparison,
      plugins: pluginsComparison,
    },
  };
}

/**
 * Get settings comparison as a structured object for display
 */
export function getSettingsComparison(
  before: ParsedData,
  after: ParsedData
): Record<string, { before: Record<string, unknown>; after: Record<string, unknown>; deltas: FieldDelta[] }> {
  const settingsTypes: Array<{
    key: string;
    label: string;
    before: Record<string, unknown> | undefined;
    after: Record<string, unknown> | undefined;
  }> = [
    { key: 'enabledSettings', label: 'Enabled Settings', before: before.enabledSettings, after: after.enabledSettings },
    { key: 'sparkSettings', label: 'Spark Settings', before: before.sparkSettings, after: after.sparkSettings },
    { key: 'authSettings', label: 'Auth Settings', before: before.authSettings as Record<string, unknown>, after: after.authSettings as Record<string, unknown> },
    { key: 'containerSettings', label: 'Container Settings', before: before.containerSettings, after: after.containerSettings },
    { key: 'integrationSettings', label: 'Integration Settings', before: before.integrationSettings, after: after.integrationSettings },
    { key: 'resourceLimits', label: 'Resource Limits', before: before.resourceLimits, after: after.resourceLimits },
    { key: 'cgroupSettings', label: 'CGroup Settings', before: before.cgroupSettings, after: after.cgroupSettings },
    { key: 'proxySettings', label: 'Proxy Settings', before: before.proxySettings as Record<string, unknown>, after: after.proxySettings as Record<string, unknown> },
    { key: 'maxRunningActivities', label: 'Max Running Activities', before: before.maxRunningActivities, after: after.maxRunningActivities },
    { key: 'javaMemorySettings', label: 'Java Memory', before: before.javaMemorySettings, after: after.javaMemorySettings },
    { key: 'javaMemoryLimits', label: 'Java Memory Limits', before: before.javaMemoryLimits, after: after.javaMemoryLimits },
  ];

  const result: Record<string, { before: Record<string, unknown>; after: Record<string, unknown>; deltas: FieldDelta[] }> = {};

  for (const { key, label, before: b, after: a } of settingsTypes) {
    const beforeObj = b || {};
    const afterObj = a || {};

    // Only include if at least one side has data
    if (Object.keys(beforeObj).length > 0 || Object.keys(afterObj).length > 0) {
      const deltas = compareRecords(beforeObj, afterObj, label);
      result[key] = {
        before: beforeObj,
        after: afterObj,
        deltas,
      };
    }
  }

  return result;
}
