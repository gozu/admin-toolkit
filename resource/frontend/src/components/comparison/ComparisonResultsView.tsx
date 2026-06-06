import { motion } from 'framer-motion';
import { useDiag } from '../../context/DiagContext';
import { useUltraWideLayout } from '../../hooks/useUltraWideLayout';
import { Header } from '../Header';
import { Container } from '../Container';
import { ComparisonHealthSection } from './ComparisonHealthSection';
import { ComparisonSystemSection } from './ComparisonSystemSection';
import { ComparisonSettingsSection } from './ComparisonSettingsSection';
import { ComparisonCollectionsSection } from './ComparisonCollectionsSection';
import { ComparisonChartsSection } from './ComparisonChartsSection';

interface ComparisonResultsViewProps {
  onBack: () => void;
}

export function ComparisonResultsView({ onBack }: ComparisonResultsViewProps) {
  const { state } = useDiag();
  const { ultraWideEnabled } = useUltraWideLayout();
  const { comparison } = state;
  const { before, after, result } = comparison;

  if (!before || !after || !result) {
    return null;
  }

  const healthChange = result.health.change;
  const healthDirection = healthChange > 0 ? 'improvement' : healthChange < 0 ? 'regression' : 'neutral';

  return (
    <div className="min-h-screen flex flex-col bg-[var(--bg-void)]">
      <Header
        showBackButton
        onBack={onBack}
      />

      <main className="flex-1 py-6">
        <Container ultraWide={ultraWideEnabled}>
          {/* Critical Changes Alert Banner */}
          {result.summary.critical > 0 && (
            <motion.div
              className="mb-4 p-4 rounded-xl bg-[var(--status-critical-bg)] border border-[var(--status-critical-border)]"
              initial={{ opacity: 0, y: -20 }}
              animate={{ opacity: 1, y: 0 }}
            >
              <div className="flex items-center gap-3">
                <div className="flex-shrink-0 p-2 rounded-lg bg-[var(--neon-red)]/20">
                  <svg className="w-5 h-5 text-[var(--neon-red)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                  </svg>
                </div>
                <div>
                  <span className="text-[var(--neon-red)] font-semibold">
                    {result.summary.critical} Critical Change{result.summary.critical !== 1 ? 's' : ''} Detected
                  </span>
                  <p className="text-sm text-[var(--text-muted)] mt-0.5">
                    Review the highlighted sections below for details
                  </p>
                </div>
              </div>
            </motion.div>
          )}

          {/* File comparison header */}
          <motion.div
            className="mb-6"
            initial={{ opacity: 0, y: -20 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <div className="flex flex-col sm:flex-row items-center justify-center gap-4 sm:gap-6">
              {/* Before file card */}
              <div className="w-full sm:flex-1 sm:max-w-sm p-5 rounded-xl">
                <div className="flex items-center gap-2 mb-3">
                  <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-[var(--neon-cyan)]/15 text-[var(--neon-cyan)]">
                    Before
                  </span>
                </div>
                <p className="text-[var(--text-primary)] font-medium truncate mb-1" title={before.filename}>
                  {before.filename}
                </p>
                <div className="flex items-center justify-between text-sm">
                  <span className="text-[var(--text-muted)]">
                    {before.parsedData.dssVersion || 'Unknown version'}
                  </span>
                  <span className="font-bold text-[var(--text-primary)]">
                    {before.healthScore?.overall ?? '—'}
                  </span>
                </div>
              </div>

              {/* Arrow - hidden on mobile */}
              <div className="hidden sm:flex flex-col items-center">
                <svg className="w-8 h-8 text-[var(--text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14 5l7 7m0 0l-7 7m7-7H3" />
                </svg>
                {healthChange !== 0 && (
                  <span className={`text-sm font-bold mt-1 ${
                    healthDirection === 'improvement' ? 'text-[var(--success)]' :
                    healthDirection === 'regression' ? 'text-[var(--neon-red)]' : 'text-[var(--text-muted)]'
                  }`}>
                    {healthChange > 0 ? '+' : ''}{healthChange}
                  </span>
                )}
              </div>
              {/* Mobile: show change between cards */}
              <div className="sm:hidden text-center py-2">
                {healthChange !== 0 && (
                  <span className={`text-lg font-bold ${
                    healthDirection === 'improvement' ? 'text-[var(--success)]' :
                    healthDirection === 'regression' ? 'text-[var(--neon-red)]' : 'text-[var(--text-muted)]'
                  }`}>
                    {healthChange > 0 ? '+' : ''}{healthChange}
                  </span>
                )}
              </div>

              {/* After file card */}
              <div className="w-full sm:flex-1 sm:max-w-sm p-5 rounded-xl">
                <div className="flex items-center gap-2 mb-3">
                  <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-[var(--neon-purple)]/15 text-[var(--neon-purple)]">
                    After
                  </span>
                </div>
                <p className="text-[var(--text-primary)] font-medium truncate mb-1" title={after.filename}>
                  {after.filename}
                </p>
                <div className="flex items-center justify-between text-sm">
                  <span className="text-[var(--text-muted)]">
                    {after.parsedData.dssVersion || 'Unknown version'}
                  </span>
                  <span className={`font-bold ${
                    healthDirection === 'improvement' ? 'text-[var(--success)]' :
                    healthDirection === 'regression' ? 'text-[var(--neon-red)]' : 'text-[var(--text-primary)]'
                  }`}>
                    {after.healthScore?.overall ?? '—'}
                  </span>
                </div>
              </div>
            </div>
          </motion.div>

          {/* Summary stats */}
          <motion.div
            className="mb-6 p-4 rounded-xl"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
          >
            <div className="flex flex-wrap items-center justify-center gap-6 sm:gap-8 text-sm">
              <div className="text-center min-w-[80px]">
                <div className="text-2xl font-bold text-[var(--text-primary)]">{result.summary.totalChanges}</div>
                <div className="text-[var(--text-muted)]">Total Changes</div>
              </div>
              <div className="hidden sm:block w-px h-10 bg-[var(--border-glass)]" />
              <div className="text-center min-w-[80px] group relative">
                <div className="text-2xl font-bold text-[var(--success)]">{result.summary.improvements}</div>
                <div className="text-[var(--text-muted)]">Improvements</div>
                {result.summary.improvementDeltas.length > 0 && (
                  <div className="absolute left-1/2 -translate-x-1/2 top-full mt-2 z-50 hidden group-hover:block">
                    <div className="bg-[var(--bg-surface)] border border-[var(--border-glass)] rounded-lg p-3 shadow-xl min-w-[220px] max-w-[320px] max-h-[400px] overflow-y-auto">
                      <div className="text-xs text-[var(--text-muted)] mb-2">Improvements:</div>
                      {result.summary.improvementDeltas.slice(0, 30).map((d, i) => (
                        <div key={i} className="text-xs text-[var(--success)] truncate">
                          <span className="text-[var(--text-muted)]">{d.category}:</span> {d.label}
                        </div>
                      ))}
                      {result.summary.improvementDeltas.length > 30 && (
                        <div className="text-xs text-[var(--text-muted)] mt-1">+{result.summary.improvementDeltas.length - 30} more</div>
                      )}
                    </div>
                  </div>
                )}
              </div>
              <div className="hidden sm:block w-px h-10 bg-[var(--border-glass)]" />
              <div className="text-center min-w-[80px] group relative">
                <div className="text-2xl font-bold text-[var(--neon-red)]">{result.summary.regressions}</div>
                <div className="text-[var(--text-muted)]">Regressions</div>
                {result.summary.regressionDeltas.length > 0 && (
                  <div className="absolute left-1/2 -translate-x-1/2 top-full mt-2 z-50 hidden group-hover:block">
                    <div className="bg-[var(--bg-surface)] border border-[var(--border-glass)] rounded-lg p-3 shadow-xl min-w-[220px] max-w-[320px] max-h-[400px] overflow-y-auto">
                      <div className="text-xs text-[var(--text-muted)] mb-2">Regressions:</div>
                      {result.summary.regressionDeltas.slice(0, 30).map((d, i) => (
                        <div key={i} className="text-xs text-[var(--neon-red)] truncate">
                          <span className="text-[var(--text-muted)]">{d.category}:</span> {d.label}
                        </div>
                      ))}
                      {result.summary.regressionDeltas.length > 30 && (
                        <div className="text-xs text-[var(--text-muted)] mt-1">+{result.summary.regressionDeltas.length - 30} more</div>
                      )}
                    </div>
                  </div>
                )}
              </div>
              <div className="hidden sm:block w-px h-10 bg-[var(--border-glass)]" />
              <div className="text-center min-w-[80px]">
                <div className="text-2xl font-bold text-[var(--text-muted)]">{result.summary.neutral}</div>
                <div className="text-[var(--text-muted)]">Neutral</div>
              </div>
              {result.summary.critical > 0 && (
                <>
                  <div className="hidden sm:block w-px h-10 bg-[var(--border-glass)]" />
                  <div className="text-center min-w-[80px]">
                    <div className="text-2xl font-bold text-[var(--neon-red)]">{result.summary.critical}</div>
                    <div className="text-[var(--text-muted)]">Critical</div>
                  </div>
                </>
              )}
            </div>
          </motion.div>

          {/* Health Score Comparison */}
          <ComparisonHealthSection
            beforeHealth={before.healthScore}
            afterHealth={after.healthScore}
            healthChange={healthChange}
            dynamicTitle={
              healthChange !== 0
                ? `Health ${healthChange > 0 ? 'improved' : 'declined'} by ${healthChange > 0 ? '+' : ''}${healthChange}`
                : undefined
            }
          />

          {/* System Overview Comparison */}
          <ComparisonSystemSection
            beforeData={before.parsedData}
            afterData={after.parsedData}
          />

          {/* Resource Usage Comparison (Charts) */}
          <ComparisonChartsSection
            beforeData={before.parsedData}
            afterData={after.parsedData}
          />

          {/* Configuration Comparison (Settings Tables) */}
          <ComparisonSettingsSection
            beforeData={before.parsedData}
            afterData={after.parsedData}
          />

          {/* Collections Comparison (Users, Projects, etc.) */}
          <ComparisonCollectionsSection
            users={result.collections.users}
            projects={result.collections.projects}
            clusters={result.collections.clusters}
            codeEnvs={result.collections.codeEnvs}
            plugins={result.collections.plugins}
          />

          {/* Empty state if no significant changes */}
          {result.summary.totalChanges === 0 && (
            <motion.div
              className="p-8 rounded-xl text-center"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.3 }}
            >
              <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-[var(--status-success-bg)] flex items-center justify-center">
                <svg className="w-8 h-8 text-[var(--neon-green)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <h3 className="text-lg font-semibold text-[var(--text-primary)] mb-2">
                No Significant Changes Detected
              </h3>
              <p className="text-[var(--text-muted)] max-w-md mx-auto">
                The two diagnostic files appear to be very similar. All compared values match.
              </p>
            </motion.div>
          )}
        </Container>
      </main>
    </div>
  );
}
