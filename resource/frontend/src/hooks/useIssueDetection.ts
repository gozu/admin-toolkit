import { useMemo } from 'react';
import type { ParsedData } from '../types';
import { useThresholds } from './useThresholds';

export type IssueSeverity = 'critical' | 'warning' | 'info';

export interface DetectedIssue {
  id: string;
  severity: IssueSeverity;
  title: string;
  description: string;
  category: string;
  scrollTarget?: string;
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
 * Hook to detect issues in parsed diagnostic data
 */
export function useIssueDetection(parsedData: ParsedData) {
  const { thresholds } = useThresholds();
  return useMemo(() => {
    const issues: DetectedIssue[] = [];

    // ============================================
    // FILESYSTEM ISSUES
    // ============================================
    if (parsedData.filesystemInfo) {
      for (const fs of parsedData.filesystemInfo) {
        const usage = parsePercentage(fs['Use%']);
        const mountPoint = fs['Mounted on'] || fs['Filesystem'];

        // Skip invalid entries (bad parsing can cause >100% values)
        if (usage > 100 || usage <= 0) continue;

        if (usage >= thresholds.filesystemCriticalPct) {
          issues.push({
            id: `fs-critical-${mountPoint}`,
            severity: 'critical',
            title: `Filesystem ${usage}% full`,
            description: `${mountPoint} is at ${usage}% capacity`,
            category: 'filesystem',
            scrollTarget: 'filesystem-table',
          });
        } else if (usage >= thresholds.filesystemWarningPct) {
          issues.push({
            id: `fs-warning-${mountPoint}`,
            severity: 'warning',
            title: `Filesystem ${usage}% used`,
            description: `${mountPoint} is at ${usage}% capacity`,
            category: 'filesystem',
            scrollTarget: 'filesystem-table',
          });
        }
      }
    }

    // ============================================
    // OPEN FILES LIMIT
    // ============================================
    if (parsedData.systemLimits) {
      const maxOpenFiles = parsedData.systemLimits['Max open files'];
      if (maxOpenFiles) {
        const limit = parseInt(String(maxOpenFiles), 10);
        if (limit < thresholds.openFilesMinimum) {
          issues.push({
            id: 'open-files-low',
            severity: 'critical',
            title: 'Open files limit too low',
            description: `Max open files is ${limit}, should be >= ${thresholds.openFilesMinimum}`,
            category: 'system',
            scrollTarget: 'systemLimits-table',
          });
        }
      }
    }

    // ============================================
    // JAVA MEMORY SETTINGS
    // ============================================
    if (parsedData.javaMemorySettings) {
      const settings = parsedData.javaMemorySettings;

      // Backend heap check (should be >= 2GB)
      const backendMB = parseMemoryToMB(settings['BACKEND']);
      if (backendMB > 0 && backendMB < thresholds.javaHeapMinimumMB) {
        issues.push({
          id: 'backend-heap-low',
          severity: 'warning',
          title: 'Backend heap < 2GB',
          description: `Backend heap is ${settings['BACKEND']}, recommended >= 2GB`,
          category: 'memory',
          scrollTarget: 'javaMemoryLimits-table',
        });
      }

      // JEK heap check (should be >= 2GB)
      const jekMB = parseMemoryToMB(settings['JEK']);
      if (jekMB > 0 && jekMB < thresholds.javaHeapMinimumMB) {
        issues.push({
          id: 'jek-heap-low',
          severity: 'warning',
          title: 'JEK heap < 2GB',
          description: `JEK heap is ${settings['JEK']}, recommended >= 2GB`,
          category: 'memory',
          scrollTarget: 'javaMemoryLimits-table',
        });
      }

      // FEK heap check (should be >= 2GB)
      const fekMB = parseMemoryToMB(settings['FEK']);
      if (fekMB > 0 && fekMB < thresholds.javaHeapMinimumMB) {
        issues.push({
          id: 'fek-heap-low',
          severity: 'warning',
          title: 'FEK heap < 2GB',
          description: `FEK heap is ${settings['FEK']}, recommended >= 2GB`,
          category: 'memory',
          scrollTarget: 'javaMemoryLimits-table',
        });
      }
    }

    // ============================================
    // CGROUPS ISSUES
    // ============================================
    if (parsedData.cgroupSettings) {
      const emptyTargets = parsedData.cgroupSettings['Empty Target Types'];
      if (emptyTargets && String(emptyTargets).trim() !== '') {
        issues.push({
          id: 'cgroups-empty-targets',
          severity: 'critical',
          title: 'CGroups empty target types',
          description: `Empty target types detected: ${emptyTargets}`,
          category: 'cgroups',
          scrollTarget: 'cgroupSettings-table',
        });
      }

      const cgroupsEnabled =
        parsedData.cgroupSettings['CGroups Enabled'] ?? parsedData.cgroupSettings['Enabled'];
      if (cgroupsEnabled === 'No' || cgroupsEnabled === 'false') {
        issues.push({
          id: 'cgroups-not-enabled',
          severity: 'critical',
          title: 'CGroups not enabled',
          description: 'CGroups isolation is disabled — user workloads share host resources',
          category: 'cgroups',
          scrollTarget: 'cgroupSettings-table',
        });
      }
    }

    // ============================================
    // PYTHON VERSION
    // ============================================
    if (parsedData.pythonVersion) {
      const versionMatch = parsedData.pythonVersion.match(/(\d+)\.(\d+)/);
      if (versionMatch) {
        const major = parseInt(versionMatch[1], 10);
        const minor = parseInt(versionMatch[2], 10);

        const pyCritParsed = thresholds.pythonCriticalBelow.match(/(\d+)\.(\d+)/);
        const pyCritMajor = pyCritParsed ? parseInt(pyCritParsed[1], 10) : 3;
        const pyCritMinor = pyCritParsed ? parseInt(pyCritParsed[2], 10) : 8;
        const pyWarnParsed = thresholds.pythonWarningBelow.match(/(\d+)\.(\d+)/);
        const pyWarnMajor = pyWarnParsed ? parseInt(pyWarnParsed[1], 10) : 3;
        const pyWarnMinor = pyWarnParsed ? parseInt(pyWarnParsed[2], 10) : 10;

        if (major < pyCritMajor || (major === pyCritMajor && minor < pyCritMinor)) {
          issues.push({
            id: 'python-eol',
            severity: 'critical',
            title: 'Python version EOL',
            description: `Python ${parsedData.pythonVersion} is end-of-life`,
            category: 'python',
            scrollTarget: 'code-envs-table',
          });
        } else if (major < pyWarnMajor || (major === pyWarnMajor && minor < pyWarnMinor)) {
          issues.push({
            id: 'python-old',
            severity: 'warning',
            title: 'Python version outdated',
            description: `Python ${parsedData.pythonVersion} is deprecated, consider upgrading`,
            category: 'python',
            scrollTarget: 'code-envs-table',
          });
        }
      }
    }

    // ============================================
    // SPARK VERSION
    // ============================================
    if (parsedData.sparkSettings) {
      const sparkVersion = parsedData.sparkSettings['Spark Version'];
      if (sparkVersion && typeof sparkVersion === 'string') {
        const versionMatch = sparkVersion.match(/^(\d+)/);
        if (versionMatch) {
          const majorVersion = parseInt(versionMatch[1], 10);
          if (majorVersion < thresholds.sparkVersionMinimum) {
            issues.push({
              id: 'spark-old',
              severity: 'warning',
              title: `Spark ${sparkVersion} outdated`,
              description: `Spark version ${sparkVersion} is < 3.0, consider upgrading`,
              category: 'spark',
              scrollTarget: 'sparkSettings-table',
            });
          }
        }
      }
    }

    // ============================================
    // PROJECT COUNT
    // ============================================
    if (parsedData.projects && parsedData.projects.length > thresholds.projectCountWarning) {
      issues.push({
        id: 'large-project-count',
        severity: 'warning',
        title: 'Large project count',
        description: `${parsedData.projects.length} projects may impact performance`,
        category: 'data',
        scrollTarget: 'projects-table',
      });
    }

    // ============================================
    // AI FEATURES NOT ENABLED
    // ============================================
    if (parsedData.disabledFeatures) {
      const aiFeatures = Object.keys(parsedData.disabledFeatures).filter(
        key => key.startsWith('AI:') || key === 'AI Assistants' || key === 'Code Assistant' || key === 'Ask Dataiku'
      );

      if (aiFeatures.length > 0) {
        issues.push({
          id: 'ai-features-disabled',
          severity: 'info',
          title: `${aiFeatures.length} AI feature${aiFeatures.length > 1 ? 's' : ''} disabled`,
          description: aiFeatures.join(', '),
          category: 'ai',
          scrollTarget: 'disabledFeatures-table',
        });
      }
    }

    // ============================================
    // DISABLED FEATURES COUNT (non-AI)
    // ============================================
    if (parsedData.disabledFeatures) {
      const nonAiDisabled = Object.keys(parsedData.disabledFeatures).filter(
        key => !key.startsWith('AI:') && key !== 'AI Assistants' && key !== 'Code Assistant' && key !== 'Ask Dataiku'
      );

      if (nonAiDisabled.length > 0) {
        issues.push({
          id: 'disabled-features',
          severity: nonAiDisabled.length > thresholds.disabledFeaturesSeverityCutoff ? 'warning' : 'info',
          title: `${nonAiDisabled.length} feature${nonAiDisabled.length > 1 ? 's' : ''} disabled`,
          description: nonAiDisabled.slice(0, 3).join(', ') + (nonAiDisabled.length > 3 ? '...' : ''),
          category: 'features',
          scrollTarget: 'disabledFeatures-table',
        });
      }
    }

    // ============================================
    // MEMORY INFO
    // ============================================
    if (parsedData.memoryInfo) {
      const swapTotal = parsedData.memoryInfo['SwapTotal'];
      if (swapTotal && (swapTotal === '0' || swapTotal === '0 kB' || swapTotal === '0 B')) {
        issues.push({
          id: 'no-swap',
          severity: 'warning',
          title: 'No swap memory',
          description: 'System has no swap configured',
          category: 'memory',
          scrollTarget: 'memory-chart',
        });
      }
    }

    // ============================================
    // HTTPS / SECURE COOKIES / LDAP GROUPS
    // ============================================
    if (parsedData.instanceInfo && 'https' in parsedData.instanceInfo) {
      if (parsedData.instanceInfo.https !== true) {
        issues.push({
          id: 'https-not-configured',
          severity: 'warning',
          title: 'HTTPS not configured',
          description: 'Web server is serving plain HTTP — configure SSL in install.ini [server]',
          category: 'security_isolation',
          scrollTarget: 'overview',
        });
      }
    }

    if (parsedData.securityDefaults) {
      const defaults = parsedData.securityDefaults;

      if (defaults['Impersonation'] === 'Disabled') {
        issues.push({
          id: 'impersonation-disabled',
          severity: 'critical',
          title: 'Impersonation disabled',
          description: 'User impersonation is disabled — workloads do not run under per-user identities',
          category: 'security_isolation',
          scrollTarget: 'securityDefaults-table',
        });
      }

      if (defaults['Secure Cookies'] === 'Disabled') {
        issues.push({
          id: 'secure-cookies-disabled',
          severity: 'warning',
          title: 'Secure cookies disabled',
          description: 'Session cookies are not marked Secure — enable security.secureCookies',
          category: 'security_isolation',
          scrollTarget: 'securityDefaults-table',
        });
      }

      if (defaults['Graphics Export'] === 'Disabled') {
        issues.push({
          id: 'graphics-export-disabled',
          severity: 'info',
          title: 'Graphics export not configured',
          description: 'PDF/image export from dashboards is disabled (graphicsExportsEnabled)',
          category: 'config',
          scrollTarget: 'securityDefaults-table',
        });
      }

      const forcedPref = defaults['Forced Preferred Connection'];
      const enginePref = defaults['Engine Preferences'];
      if ((forcedPref !== 'Not set' && forcedPref !== undefined) || enginePref === 'Configured') {
        issues.push({
          id: 'preferred-engines-forced',
          severity: 'info',
          title: 'Preferred connections/engines configured',
          description: 'Review defaults — admins should confirm the forced preferences match the intent',
          category: 'config',
          scrollTarget: 'securityDefaults-table',
        });
      }
    }

    if (
      parsedData.authSettings?.['LDAP Authentication'] === 'Enabled' &&
      (parsedData.ldapAuthorizedGroups?.length ?? 0) === 0
    ) {
      issues.push({
        id: 'ldap-authorized-groups-empty',
        severity: 'info',
        title: 'LDAP authorized groups not set',
        description: 'No LDAP groups restrict sign-in — any LDAP user can authenticate',
        category: 'security_isolation',
        scrollTarget: 'authSettings-table',
      });
    }

    // ============================================
    // CONNECTION AUDIT (filesystem_root + rollup)
    // ============================================
    if (parsedData.connectionAudit && parsedData.connectionAudit.length > 0) {
      const hasFilesystemRoot = parsedData.connectionAudit.some(
        (c) => c.name === 'filesystem_root' && c.severity === 'critical'
      );
      if (hasFilesystemRoot) {
        issues.push({
          id: 'filesystem-root-exists',
          severity: 'critical',
          title: 'Default filesystem_root connection present',
          description: 'Default filesystem_root should be removed — it exposes the DSS data dir as a writable connection',
          category: 'connections',
          scrollTarget: 'connection-health-card',
        });
      }

      const nonCriticalCount = parsedData.connectionAudit.filter(
        (c) => c.severity !== 'critical' && c.configIssues.length > 0
      ).length;
      if (nonCriticalCount > 0) {
        issues.push({
          id: 'connections-config-issues',
          severity: 'warning',
          title: `${nonCriticalCount} connection${nonCriticalCount > 1 ? 's' : ''} with config issues`,
          description: 'Review fast-write, details-readable, and HDFS interface settings in Connection Health',
          category: 'connections',
          scrollTarget: 'connection-health-card',
        });
      }
    }

    // Sort by severity: critical first, then warning, then info
    const severityOrder: Record<IssueSeverity, number> = {
      critical: 0,
      warning: 1,
      info: 2,
    };

    issues.sort((a, b) => severityOrder[a.severity] - severityOrder[b.severity]);

    return {
      issues,
      criticalCount: issues.filter(i => i.severity === 'critical').length,
      warningCount: issues.filter(i => i.severity === 'warning').length,
      infoCount: issues.filter(i => i.severity === 'info').length,
      totalCount: issues.length,
    };
  }, [parsedData, thresholds]);
}
