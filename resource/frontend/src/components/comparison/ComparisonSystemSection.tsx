import { motion } from 'framer-motion';
import type { ParsedData } from '../../types';
import { DeltaBadge } from './DeltaBadge';

// Compare semantic versions - returns positive if v1 > v2, negative if v1 < v2, 0 if equal
function compareVersions(v1: string | undefined, v2: string | undefined): number {
  if (!v1 || !v2) return 0;
  const parts1 = v1.split(/[.-]/).map(p => parseInt(p, 10) || 0);
  const parts2 = v2.split(/[.-]/).map(p => parseInt(p, 10) || 0);
  const maxLen = Math.max(parts1.length, parts2.length);
  for (let i = 0; i < maxLen; i++) {
    const p1 = parts1[i] || 0;
    const p2 = parts2[i] || 0;
    if (p1 > p2) return 1;
    if (p1 < p2) return -1;
  }
  return 0;
}

// Highlight changed segments in version strings
function VersionDiff({ before, after }: { before: string; after: string }) {
  const beforeParts = before.split('.');
  const afterParts = after.split('.');
  const versionComparison = compareVersions(after, before);

  return (
    <span className="font-mono text-sm">
      {afterParts.map((part, i) => {
        const changed = beforeParts[i] !== part;
        return (
          <span key={i}>
            {i > 0 && '.'}
            <span className={changed ? 'text-[var(--neon-amber)] font-bold' : 'text-[var(--text-primary)]'}>
              {part}
            </span>
          </span>
        );
      })}
      {versionComparison !== 0 && (
        <span className={`ml-1.5 inline-flex items-center ${versionComparison > 0 ? 'text-[var(--neon-green)]' : 'text-[var(--neon-red)]'}`}>
          {versionComparison > 0 ? (
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 10l7-7m0 0l7 7m-7-7v18" />
            </svg>
          ) : (
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
            </svg>
          )}
        </span>
      )}
    </span>
  );
}

interface ComparisonSystemSectionProps {
  beforeData: ParsedData;
  afterData: ParsedData;
  dynamicTitle?: string;
}

interface SystemFieldProps {
  label: string;
  beforeValue: string | undefined;
  afterValue: string | undefined;
  icon: React.ReactNode;
  highlight?: boolean;
  delay?: number;
}

function SystemField({ label, beforeValue, afterValue, icon, highlight = false, delay = 0 }: SystemFieldProps) {
  const changed = beforeValue !== afterValue;
  const added = beforeValue === undefined && afterValue !== undefined;
  const removed = beforeValue !== undefined && afterValue === undefined;

  const changeType = added ? 'added' : removed ? 'removed' : changed ? 'modified' : 'unchanged';

  return (
    <motion.div
      className={`p-4 rounded-lg ${changed ? 'bg-[var(--status-warning-bg)] border border-[var(--status-warning-border)]' : 'bg-[var(--bg-glass)]'}`}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: delay * 0.1, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="flex items-start gap-3">
        <div className={`flex-shrink-0 p-2 rounded-lg ${highlight ? 'bg-[var(--status-info-bg)] text-[var(--neon-cyan)]' : 'bg-[var(--bg-surface)] text-[var(--text-muted)]'}`}>
          {icon}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs text-[var(--text-muted)] uppercase tracking-wider">{label}</span>
            {changed && <DeltaBadge changeType={changeType} />}
          </div>
          <div className="flex flex-col gap-1">
            {!removed && (
              <div className="flex items-center gap-2">
                {changed && <span className="text-xs text-[var(--text-muted)] w-12">After:</span>}
                <span className={`font-mono text-sm ${changed ? 'text-[var(--text-primary)] font-semibold' : 'text-[var(--text-secondary)]'}`}>
                  {afterValue || '—'}
                </span>
              </div>
            )}
            {changed && beforeValue && (
              <div className="flex items-center gap-2">
                <span className="text-xs text-[var(--text-muted)] w-12">Before:</span>
                <span className="font-mono text-sm text-[var(--text-muted)] line-through">
                  {beforeValue}
                </span>
              </div>
            )}
            {removed && (
              <div className="flex items-center gap-2">
                <span className="text-xs text-[var(--text-muted)] w-12">Was:</span>
                <span className="font-mono text-sm text-[var(--neon-red)] line-through">
                  {beforeValue}
                </span>
              </div>
            )}
          </div>
        </div>
      </div>
    </motion.div>
  );
}

function PythonVersionBadge({ version, changed }: { version: string; changed: boolean }) {
  const versionMatch = version.match(/(\d+)\.(\d+)/);
  let colorClass = 'text-[var(--text-primary)]';
  let bgClass = 'bg-[var(--bg-glass)] border-[var(--border-glass)]';

  if (versionMatch) {
    const major = parseInt(versionMatch[1], 10);
    const minor = parseInt(versionMatch[2], 10);

    if (major < 3) {
      colorClass = 'text-[var(--neon-red)]';
      bgClass = 'bg-[var(--status-critical-bg)] border-[var(--status-critical-border)]';
    } else if (major === 3 && minor >= 9) {
      colorClass = 'text-[var(--neon-green)]';
      bgClass = 'bg-[var(--status-success-bg)] border-[var(--status-success-border)]';
    } else {
      colorClass = 'text-[var(--neon-amber)]';
      bgClass = 'bg-[var(--status-warning-bg)] border-[var(--status-warning-border)]';
    }
  }

  return (
    <span className={`text-sm font-mono font-semibold ${colorClass} ${bgClass} px-2 py-0.5 rounded border ${changed ? 'ring-2 ring-[var(--neon-amber)]' : ''}`}>
      {version}
    </span>
  );
}

// Icons
const ServerIcon = () => (
  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2m-2-4h.01M17 16h.01" />
  </svg>
);

const CodeIcon = () => (
  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
  </svg>
);

const CpuIcon = () => (
  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z" />
  </svg>
);

const MemoryIcon = () => (
  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
  </svg>
);

const DesktopIcon = () => (
  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
  </svg>
);

const ClockIcon = () => (
  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
  </svg>
);

const BuildingIcon = () => (
  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
  </svg>
);

export function ComparisonSystemSection({ beforeData, afterData, dynamicTitle }: ComparisonSystemSectionProps) {
  // Count changes
  const fields = [
    { key: 'company', before: beforeData.company, after: afterData.company },
    { key: 'dssVersion', before: beforeData.dssVersion, after: afterData.dssVersion },
    { key: 'pythonVersion', before: beforeData.pythonVersion, after: afterData.pythonVersion },
    { key: 'cpuCores', before: beforeData.cpuCores, after: afterData.cpuCores },
    { key: 'osInfo', before: beforeData.osInfo, after: afterData.osInfo },
    { key: 'lastRestartTime', before: beforeData.lastRestartTime, after: afterData.lastRestartTime },
    { key: 'memoryTotal', before: beforeData.memoryInfo?.total, after: afterData.memoryInfo?.total },
  ];

  const changedCount = fields.filter((f) => f.before !== f.after).length;

  return (
    <motion.div
      className="glass-card p-5 mb-5"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h2 className="text-xl font-semibold text-neon-subtle">System Overview</h2>
          {(dynamicTitle || changedCount > 0) && (
            <span className="text-sm font-medium px-2 py-0.5 rounded bg-[var(--status-warning-bg)] text-[var(--neon-amber)]">
              {dynamicTitle || `${changedCount} change${changedCount !== 1 ? 's' : ''} detected`}
            </span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {/* Company */}
        {(beforeData.company || afterData.company) && (
          <SystemField
            label="Company"
            beforeValue={beforeData.company}
            afterValue={afterData.company}
            icon={<BuildingIcon />}
            delay={0}
          />
        )}

        {/* DSS Version - with direction indicator */}
        {(beforeData.dssVersion || afterData.dssVersion) && (
          <motion.div
            className={`p-4 rounded-lg ${beforeData.dssVersion !== afterData.dssVersion ? 'bg-[var(--status-warning-bg)] border border-[var(--status-warning-border)]' : 'bg-[var(--bg-glass)]'}`}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.1, ease: [0.16, 1, 0.3, 1] }}
          >
            <div className="flex items-start gap-3">
              <div className="flex-shrink-0 p-2 rounded-lg bg-[var(--status-info-bg)] text-[var(--neon-cyan)]">
                <ServerIcon />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs text-[var(--text-muted)] uppercase tracking-wider">DSS Version</span>
                  {beforeData.dssVersion !== afterData.dssVersion && (
                    <DeltaBadge changeType="modified" />
                  )}
                </div>
                <div className="flex flex-col gap-1">
                  {beforeData.dssVersion !== afterData.dssVersion && beforeData.dssVersion && afterData.dssVersion ? (
                    <>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-[var(--text-muted)] w-12">After:</span>
                        <VersionDiff before={beforeData.dssVersion} after={afterData.dssVersion} />
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-[var(--text-muted)] w-12">Before:</span>
                        <span className="font-mono text-sm text-[var(--text-muted)] line-through">
                          {beforeData.dssVersion}
                        </span>
                      </div>
                    </>
                  ) : (
                    <span className="font-mono text-sm text-[var(--text-secondary)]">
                      {afterData.dssVersion || beforeData.dssVersion || '—'}
                    </span>
                  )}
                </div>
              </div>
            </div>
          </motion.div>
        )}

        {/* Python Version - with direction indicator */}
        {(beforeData.pythonVersion || afterData.pythonVersion) && (
          <motion.div
            className={`p-4 rounded-lg ${beforeData.pythonVersion !== afterData.pythonVersion ? 'bg-[var(--status-warning-bg)] border border-[var(--status-warning-border)]' : 'bg-[var(--bg-glass)]'}`}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.2, ease: [0.16, 1, 0.3, 1] }}
          >
            <div className="flex items-start gap-3">
              <div className="flex-shrink-0 p-2 rounded-lg bg-[var(--bg-surface)] text-[var(--text-muted)]">
                <CodeIcon />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs text-[var(--text-muted)] uppercase tracking-wider">Python</span>
                  {beforeData.pythonVersion !== afterData.pythonVersion && (
                    <DeltaBadge changeType="modified" />
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {beforeData.pythonVersion !== afterData.pythonVersion && beforeData.pythonVersion && afterData.pythonVersion ? (
                    <>
                      <PythonVersionBadge version={afterData.pythonVersion} changed={true} />
                      {compareVersions(afterData.pythonVersion, beforeData.pythonVersion) !== 0 && (
                        <span className={`inline-flex items-center ${compareVersions(afterData.pythonVersion, beforeData.pythonVersion) > 0 ? 'text-[var(--neon-green)]' : 'text-[var(--neon-red)]'}`}>
                          {compareVersions(afterData.pythonVersion, beforeData.pythonVersion) > 0 ? (
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 10l7-7m0 0l7 7m-7-7v18" />
                            </svg>
                          ) : (
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
                            </svg>
                          )}
                        </span>
                      )}
                      <span className="text-[var(--text-muted)]">←</span>
                      <span className="text-sm font-mono text-[var(--text-muted)] line-through">
                        {beforeData.pythonVersion}
                      </span>
                    </>
                  ) : (
                    afterData.pythonVersion && (
                      <PythonVersionBadge version={afterData.pythonVersion} changed={false} />
                    )
                  )}
                </div>
              </div>
            </div>
          </motion.div>
        )}

        {/* CPU Cores */}
        {(beforeData.cpuCores || afterData.cpuCores) && (
          <SystemField
            label="CPU Cores"
            beforeValue={beforeData.cpuCores}
            afterValue={afterData.cpuCores}
            icon={<CpuIcon />}
            delay={3}
          />
        )}

        {/* Memory */}
        {(beforeData.memoryInfo?.total || afterData.memoryInfo?.total) && (
          <SystemField
            label="Memory"
            beforeValue={beforeData.memoryInfo?.total}
            afterValue={afterData.memoryInfo?.total}
            icon={<MemoryIcon />}
            delay={4}
          />
        )}

        {/* OS Info */}
        {(beforeData.osInfo || afterData.osInfo) && (
          <SystemField
            label="Operating System"
            beforeValue={beforeData.osInfo ? (beforeData.osInfo.length > 28 ? `${beforeData.osInfo.substring(0, 28)}...` : beforeData.osInfo) : undefined}
            afterValue={afterData.osInfo ? (afterData.osInfo.length > 28 ? `${afterData.osInfo.substring(0, 28)}...` : afterData.osInfo) : undefined}
            icon={<DesktopIcon />}
            delay={5}
          />
        )}

        {/* Last Restart */}
        {(beforeData.lastRestartTime || afterData.lastRestartTime) && (
          <SystemField
            label="Last Restart"
            beforeValue={beforeData.lastRestartTime}
            afterValue={afterData.lastRestartTime}
            icon={<ClockIcon />}
            delay={6}
          />
        )}
      </div>
    </motion.div>
  );
}
