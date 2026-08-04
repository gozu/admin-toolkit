import { useEffect, useState } from 'react';
import { codeEnvBrokenScan } from '../../state/codeEnvBrokenStore';
import {
  abortCodeEnvAdvice,
  adviceKey,
  clearCodeEnvAdvice,
  codeEnvAdviceStore,
  requestCodeEnvAdvice,
} from '../../state/codeEnvAdviceStore';
import { reportLlmsStore } from '../../state/reportLlmsStore';
import { CodeEnvAdviceModal } from '../CodeEnvAdviceModal';
import { DataGrid } from '../common/DataGrid';
import { ProgressIndicator } from '../common/ProgressIndicator';
import { Spinner } from '../common/Spinner';
import { Modal } from '../Modal';
import { ModelPicker } from '../ModelPicker';
import { dssUrls } from '../../utils/codeEnvUsageLinks';
import { formatAuto, formatInterpreter } from '../../utils/formatters';
import type { ColumnDef } from '../../utils/dataGridTypes';
import type { BrokenEnvRow } from '../../types';

const STATUS_REASON: Record<string, string> = {
  LOG_UNAVAILABLE: 'Build log could not be read',
  NO_BUILD_LOG: 'No recognised build log',
};

function fmtDate(ms: number | null): string {
  if (!ms) return '—';
  return new Date(ms).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

function fmtDateTime(ms: number | null): string | undefined {
  return ms ? new Date(ms).toLocaleString() : undefined;
}

function fmtRelative(ms: number | null, nowMs: number): string {
  if (!ms) return '—';
  if (!nowMs) return fmtDate(ms);
  const days = Math.floor((nowMs - ms) / 86_400_000);
  if (days <= 0) return 'today';
  if (days === 1) return '1 d ago';
  if (days < 30) return `${days} d ago`;
  if (days < 365) return `${Math.floor(days / 30)} mo ago`;
  return `${(days / 365).toFixed(1)} y ago`;
}

function firstLine(text: string): string {
  const line = text.split('\n').find((l) => l.trim());
  return line ? line.trim() : '(no detail)';
}

export function CodeEnvsBrokenPage() {
  const { data, loading, error, scanStarted, scanPhase, scanMessage, finishedAt } =
    codeEnvBrokenScan.use();
  const advice = codeEnvAdviceStore.use();
  const { llms, loading: llmsLoading, loaded: llmsLoaded } = reportLlmsStore.use();
  const [selectedLlmId, setSelectedLlmId] = useState('');
  const [expandedKeys, setExpandedKeys] = useState<ReadonlySet<string>>(new Set());
  const [usagesRow, setUsagesRow] = useState<BrokenEnvRow | null>(null);
  const [adviceRow, setAdviceRow] = useState<BrokenEnvRow | null>(null);
  const [showIndeterminate, setShowIndeterminate] = useState(false);

  useEffect(() => {
    if (!scanStarted) void codeEnvBrokenScan.load();
  }, [scanStarted]);

  useEffect(() => {
    void reportLlmsStore.load();
  }, []);

  // No implicit model: the picker starts blank so the advice always runs on a
  // model the operator picked on purpose.
  const llmId = selectedLlmId;
  const llmLabel = llms.find((l) => l.id === llmId)?.label || llmId;
  const noLlmReason = llms.length
    ? 'Pick an LLM above first'
    : 'No LLM available on this instance';

  const lifecycle = codeEnvBrokenScan.lifecycle();
  const rows = data?.rows ?? [];
  const indeterminate = data?.indeterminate ?? [];
  // An aborted scan leaves partial data and no error — it must never read as a
  // clean bill of health, so the summary/empty states wait for 'complete'.
  const complete = scanPhase === 'complete' && !!data;
  const aborted = scanPhase === 'aborted' && !loading;
  // Reference "now" for relative dates: the scan's own finish timestamp, never
  // the wall clock (the React Compiler purity rule bans Date.now() in render).
  const nowMs = finishedAt ? Date.parse(finishedAt) : 0;

  const rescan = () => {
    clearCodeEnvAdvice();
    setExpandedKeys(new Set());
    void codeEnvBrokenScan.load(true);
  };

  const askLlm = (row: BrokenEnvRow) => {
    setAdviceRow(row);
    if (llmId && !advice[adviceKey(row)]) {
      void requestCodeEnvAdvice(row, llmId, llmLabel);
    }
  };

  const toggleExpanded = (row: BrokenEnvRow) => {
    const key = adviceKey(row);
    const next = new Set(expandedKeys);
    if (!next.delete(key)) next.add(key);
    setExpandedKeys(next);
  };

  const columns: ColumnDef<BrokenEnvRow>[] = [
    {
      id: 'name',
      label: 'Environment',
      sortValue: (row) => row.name.toLowerCase(),
      defaultSortDir: 'asc',
      render: (row) => (
        <div className="flex flex-wrap items-center gap-2">
          <a
            href={dssUrls.codeEnv(row.lang.toLowerCase(), row.name)}
            target="_blank"
            rel="noopener noreferrer"
            className="font-mono text-sm text-[var(--text-primary)] hover:text-[var(--neon-cyan)] hover:underline"
          >
            {row.name}
          </a>
          <span className="font-mono text-[10px] uppercase tracking-wide text-[var(--text-tertiary)]">
            {row.lang.toLowerCase()}
            {row.pythonVersion ? ` · ${formatInterpreter(row.pythonVersion)}` : ''}
          </span>
          {(row.deploymentMode === 'DSS_INTERNAL' || row.deploymentMode === 'PLUGIN_MANAGED') && (
            <span className="rounded-full border border-[var(--border-default)] px-1.5 py-0.5 text-[10px] text-[var(--text-tertiary)]">
              {row.deploymentMode === 'DSS_INTERNAL' ? 'internal' : 'plugin'}
            </span>
          )}
        </div>
      ),
    },
    {
      id: 'createdOn',
      label: 'Created',
      mono: true,
      sortValue: (row) => row.createdOn ?? 0,
      render: (row) => <span title={fmtDateTime(row.createdOn)}>{fmtDate(row.createdOn)}</span>,
    },
    {
      id: 'lastBuildOn',
      label: 'Last build',
      mono: true,
      sortValue: (row) => row.lastBuildOn ?? 0,
      render: (row) => (
        <span title={fmtDateTime(row.lastBuildOn)}>{fmtRelative(row.lastBuildOn, nowMs)}</span>
      ),
    },
    {
      id: 'usages',
      label: 'Usages',
      align: 'right',
      mono: true,
      sortValue: (row) => row.usageCount ?? -1,
      render: (row) =>
        row.usageCount == null ? (
          <span className="text-[var(--text-muted)]">—</span>
        ) : row.usageCount === 0 ? (
          <span className="text-[var(--text-muted)]">0</span>
        ) : (
          <button
            type="button"
            onClick={() => setUsagesRow(row)}
            className="text-[var(--accent)] hover:underline"
          >
            {row.usageCount}
          </button>
        ),
    },
    {
      id: 'sizeBytes',
      label: 'Size',
      align: 'right',
      mono: true,
      sortValue: (row) => row.sizeBytes ?? 0,
      render: (row) =>
        row.sizeBytes == null ? (
          <span className="text-[var(--text-muted)]">—</span>
        ) : (
          formatAuto(row.sizeBytes)
        ),
    },
    {
      id: 'error',
      label: 'Error',
      width: '40%',
      sortValue: (row) => `${row.failureClass || ''}\u0000${row.name.toLowerCase()}`,
      defaultSortDir: 'asc',
      render: (row) => (
        <button
          type="button"
          onClick={() => toggleExpanded(row)}
          className="w-full text-left"
          title="Show the full log excerpt"
        >
          <span className="badge badge-critical">{row.failureLabel || 'Build failed'}</span>
          <span className="mt-1 block line-clamp-2 font-mono text-xs text-[var(--text-secondary)]">
            {firstLine(row.errorExcerpt)}
          </span>
        </button>
      ),
    },
    {
      id: 'advice',
      label: 'Advice',
      render: (row) => {
        const entry = advice[adviceKey(row)];
        const streaming = entry?.status === 'streaming';
        return (
          <button
            type="button"
            onClick={() => (streaming ? abortCodeEnvAdvice(row) : askLlm(row))}
            // Only *asking* needs a model — advice already fetched stays
            // readable (the picker resets whenever the page remounts).
            disabled={!streaming && !entry && !llmId}
            title={streaming ? 'Stop this analysis' : entry || llmId ? undefined : noLlmReason}
            className={
              streaming
                ? 'inline-flex items-center gap-1.5 rounded border border-[var(--neon-red)]/40 px-2 py-1 text-xs text-[var(--neon-red)] hover:bg-[var(--neon-red)]/10'
                : 'inline-flex items-center gap-1.5 rounded border border-[var(--border-default)] px-2 py-1 text-xs text-[var(--text-secondary)] hover:border-[var(--accent)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-50'
            }
          >
            {streaming && <Spinner size="h-3 w-3" color="border-[var(--accent)]" />}
            {streaming
              ? 'Abort'
              : entry
                ? entry.status === 'stopped'
                  ? 'View partial'
                  : 'View advice'
                : 'Ask LLM'}
          </button>
        );
      },
    },
  ];

  return (
    <div className="w-full py-4 space-y-4">
      {/* z-20: the page cascade animation leaves every section with an identity
          transform, i.e. its own stacking context — without a z-index this card
          (and the model dropdown inside it) paints under the grid below. */}
      <div className="glass-card relative z-20 p-3 space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-0">
            <h4 className="text-lg font-semibold text-[var(--text-primary)]">
              Broken code environments
            </h4>
            <p className="mt-0.5 text-xs text-[var(--text-muted)]">
              Each environment&apos;s most recent build attempt, parsed from its build log. Dates
              are derived from the log files — DSS records none on the environment itself.
            </p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <div className="w-64">
              <ModelPicker
                llms={llms}
                selectedId={llmId}
                onChange={setSelectedLlmId}
                placeholder={
                  llmsLoading && !llmsLoaded
                    ? 'Loading models…'
                    : llms.length
                      ? 'Pick an LLM…'
                      : 'No LLM available'
                }
                className="w-full rounded-md border border-[var(--border-default)] bg-[var(--bg-elevated)] px-3 py-1.5 text-sm font-mono text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--accent)]"
              />
            </div>
            {loading ? (
              <button
                type="button"
                onClick={() => codeEnvBrokenScan.abort()}
                className="rounded border border-[var(--neon-red)]/40 px-3 py-1 text-xs font-mono text-[var(--neon-red)] hover:bg-[var(--neon-red)]/10"
              >
                Abort
              </button>
            ) : (
              <button
                type="button"
                onClick={rescan}
                className="rounded px-3 py-1 text-xs text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)]"
              >
                Rescan
              </button>
            )}
          </div>
        </div>

        {loading && (
          <div>
            <ProgressIndicator lifecycle={lifecycle} />
            <div className="mt-1 font-mono text-xs text-[var(--text-muted)]">{scanMessage}</div>
          </div>
        )}

        {complete && data && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="badge badge-critical">{rows.length} broken</span>
            <span className="badge badge-success">{data.okCount} ok</span>
            {indeterminate.length > 0 && (
              <span className="badge badge-neutral">{indeterminate.length} indeterminate</span>
            )}
            {!data.sizesAvailable && (
              <span className="text-[var(--text-tertiary)]">sizes unavailable on this host</span>
            )}
          </div>
        )}

        {aborted && (
          <div className="text-xs text-[var(--neon-yellow)]">
            Scan aborted — showing partial results. Rescan for the full picture.
          </div>
        )}
      </div>

      {error && (
        <div className="glass-card flex items-center gap-3 border border-[var(--neon-red)]/40 p-3 text-sm text-[var(--neon-red)]">
          <span className="flex-1">{error}</span>
          <button
            type="button"
            onClick={rescan}
            className="rounded px-2 py-1 text-xs font-medium text-[var(--accent)] hover:underline"
          >
            Retry
          </button>
        </div>
      )}

      {rows.length > 0 ? (
        <DataGrid
          id="code-envs-broken-table"
          title="Broken code environments"
          countBadge={{ total: rows.length }}
          rows={rows}
          columns={columns}
          rowKey={(row) => adviceKey(row)}
          defaultSortColumnId="error"
          defaultSortDir="asc"
          scroll="card"
          expandedRowKeys={expandedKeys}
          childRowClassName="bg-[var(--bg-glass)]"
          renderExpandedRow={(row) => (
            <pre className="max-h-80 overflow-auto px-4 py-3 font-mono text-xs whitespace-pre-wrap text-[var(--text-secondary)]">
              {row.errorExcerpt || '(no detail)'}
            </pre>
          )}
        />
      ) : complete && data ? (
        <div className="glass-card p-6 text-center">
          <div className="mb-1 text-2xl text-[var(--neon-green)]">&#10003;</div>
          <div className="text-sm text-[var(--text-secondary)]">
            All {data.okCount} environment{data.okCount === 1 ? '' : 's'} built cleanly on their
            last attempt.
          </div>
        </div>
      ) : null}

      {indeterminate.length > 0 && (
        <div className="glass-card p-3">
          <button
            type="button"
            onClick={() => setShowIndeterminate((prev) => !prev)}
            className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
          >
            {showIndeterminate ? '▾' : '▸'} Couldn&apos;t read {indeterminate.length} environment
            {indeterminate.length === 1 ? '' : 's'}
          </button>
          {showIndeterminate && (
            <ul className="mt-2 space-y-1">
              {indeterminate.map((row) => (
                <li key={adviceKey(row)} className="flex flex-wrap items-baseline gap-2 text-xs">
                  <span className="font-mono text-[var(--text-primary)]">{row.name}</span>
                  <span className="text-[var(--text-muted)]">
                    {STATUS_REASON[row.status] || row.status}
                  </span>
                  {row.errorExcerpt && (
                    <span className="font-mono text-[var(--text-tertiary)]">
                      {row.errorExcerpt}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {usagesRow && (
        <Modal isOpen onClose={() => setUsagesRow(null)} title={`Usages — ${usagesRow.name}`}>
          <div className="space-y-1">
            {usagesRow.usages.map((usage, idx) => (
              <div
                key={`${usage.projectKey}-${usage.objectId}-${idx}`}
                className="grid grid-cols-[10rem_8rem_minmax(0,1fr)] items-baseline gap-3 rounded px-2 py-1 text-sm odd:bg-[var(--bg-surface)]"
              >
                <span className="truncate font-mono text-[var(--text-primary)]">
                  {usage.projectKey || '—'}
                </span>
                <span className="text-xs text-[var(--text-muted)]">
                  {usage.objectType || usage.usageType || '—'}
                </span>
                <span className="truncate text-[var(--text-secondary)]">
                  {usage.objectName || usage.objectId || '—'}
                </span>
              </div>
            ))}
            {usagesRow.usagesTruncated && usagesRow.usageCount != null && (
              <div className="px-2 pt-2 text-xs text-[var(--text-muted)]">
                +{usagesRow.usageCount - usagesRow.usages.length} more not shown.
              </div>
            )}
          </div>
        </Modal>
      )}

      {adviceRow && (
        <CodeEnvAdviceModal
          row={adviceRow}
          entry={advice[adviceKey(adviceRow)]}
          onClose={() => setAdviceRow(null)}
          // Re-ask on the model that produced this entry, so a retry after a
          // remount doesn't depend on the picker still holding a selection.
          onRetry={() => {
            const entry = advice[adviceKey(adviceRow)];
            const id = entry?.llmId || llmId;
            if (id) void requestCodeEnvAdvice(adviceRow, id, entry?.llmLabel || llmLabel);
          }}
        />
      )}
    </div>
  );
}
