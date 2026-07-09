import { useMemo, useState } from 'react';
import { useDiag } from '../context/DiagContext';
import { getBackendUrl } from '../utils/api';
import { ScanIncompleteNotice } from './ScanIncompleteNotice';
import { DataGrid } from './common/DataGrid';
import { Spinner } from './common/Spinner';
import type { ColumnDef } from '../utils/dataGridTypes';
import type { LlmAuditRow, LlmAuditStatus, LlmAuditUsageAsset } from '../types';

type UsageAssetWithProject = LlmAuditUsageAsset & { projectKey: string };

const ASSET_TYPE_LABEL: Record<LlmAuditUsageAsset['assetType'], string> = {
  recipe: 'recipe',
  notebook: 'notebook',
  knowledge_bank: 'knowledge-bank',
  agent: 'agent',
};

function assetDeepLink(baseUrl: string, a: UsageAssetWithProject): string {
  const pk = encodeURIComponent(a.projectKey);
  const name = encodeURIComponent(a.assetName);
  switch (a.assetType) {
    case 'recipe':
      return `${baseUrl}/projects/${pk}/recipes/${name}/`;
    case 'notebook':
      return `${baseUrl}/projects/${pk}/notebooks/jupyter/${name}/`;
    case 'knowledge_bank':
      return `${baseUrl}/projects/${pk}/knowledge-banks/${name}/`;
    case 'agent':
      return `${baseUrl}/projects/${pk}/agents/${name}/`;
  }
}

function UsageAssetList({
  dssBaseUrl,
  assets,
}: {
  dssBaseUrl: string;
  assets: UsageAssetWithProject[];
}) {
  const [expanded, setExpanded] = useState(false);
  if (assets.length === 0) {
    return (
      <div className="text-[11px] text-[var(--text-tertiary)] mt-1 italic">
        no asset references found
      </div>
    );
  }
  const CAP = 5;
  const visible = expanded ? assets : assets.slice(0, CAP);
  const hidden = assets.length - visible.length;
  return (
    <ul className="mt-1 space-y-0.5 text-[11px]">
      {visible.map((a, i) => (
        <li key={`${a.projectKey}:${a.assetType}:${a.assetName}:${i}`}>
          <a
            href={assetDeepLink(dssBaseUrl, a)}
            target="_blank"
            rel="noopener noreferrer"
            title={a.recipeType || undefined}
            className="text-[var(--neon-cyan)] hover:underline"
          >
            {ASSET_TYPE_LABEL[a.assetType]}: {a.projectKey}/{a.assetName}
          </a>
        </li>
      ))}
      {hidden > 0 && (
        <li>
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className="text-[11px] text-[var(--text-tertiary)] hover:text-[var(--text-primary)] focus:outline-none"
          >
            + {hidden} more ▾
          </button>
        </li>
      )}
    </ul>
  );
}

// Wrapper LLMs (SAVED_MODEL_AGENT, RETRIEVAL_AUGMENTED) are filtered out in the backend,
// so 'not_applicable' never reaches this table even though the type is still part of the
// shared classifier vocabulary in python-lib/llm_audit.py.
type DisplayStatus = Exclude<LlmAuditStatus, 'not_applicable'>;

const STATUS_ORDER: Record<DisplayStatus, number> = {
  ripoff: 0,
  obsolete: 1,
  unknown: 2,
  current: 3,
};

const STATUS_LABEL: Record<DisplayStatus, string> = {
  ripoff: 'Overpriced',
  obsolete: 'Obsolete',
  unknown: 'Unknown',
  current: 'Current',
};

const STATUS_TOOLTIP: Record<DisplayStatus, string> = {
  ripoff:
    'Overpriced: a current replacement exists at lower input or output price. Obsolete: a current replacement exists at equal or cheaper price.',
  obsolete:
    'Overpriced: a current replacement exists at lower input or output price. Obsolete: a current replacement exists at equal or cheaper price.',
  current: 'Current: this is the latest model in its family.',
  unknown: 'Unknown: no matching model family in the LiteLLM catalog.',
};

const FILTER_STATUSES: DisplayStatus[] = ['ripoff', 'obsolete', 'unknown', 'current'];

function statusBadgeClass(status: DisplayStatus): string {
  switch (status) {
    case 'ripoff':
      return 'bg-[var(--neon-red)]/20 text-[var(--neon-red)] border border-[var(--neon-red)]/40 pulse-glow';
    case 'obsolete':
      return 'bg-[var(--neon-amber)]/20 text-[var(--neon-amber)] border border-[var(--neon-amber)]/40';
    case 'current':
      return 'bg-[var(--neon-green)]/20 text-[var(--neon-green)] border border-[var(--neon-green)]/40';
    case 'unknown':
    default:
      return 'bg-[var(--text-tertiary)]/15 text-[var(--text-secondary)] border border-[var(--text-tertiary)]/30';
  }
}

function priceDeltaClass(
  model: number | null | undefined,
  current: number | null | undefined,
): string {
  if (model == null || current == null) return 'text-[var(--text-secondary)]';
  if (model > current) return 'text-[var(--neon-red)]';
  if (model < current) return 'text-[var(--neon-green)]';
  return 'text-[var(--text-secondary)]';
}

function formatPrice(value: number | null | undefined): string {
  if (value == null) return '—';
  if (value === 0) return '$0';
  if (value < 0.01) return `$${value.toFixed(4)}`;
  if (value < 1) return `$${value.toFixed(3)}`;
  return `$${value.toFixed(2)}`;
}

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return 'unknown';
  const ts = Date.parse(iso);
  if (!Number.isFinite(ts)) return iso;
  const diffSec = Math.max(0, (Date.now() - ts) / 1000);
  if (diffSec < 60) return `${Math.round(diffSec)}s ago`;
  if (diffSec < 3600) return `${Math.round(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.round(diffSec / 3600)}h ago`;
  return `${Math.round(diffSec / 86400)}d ago`;
}

interface GroupedRow {
  llmId: string;
  status: DisplayStatus;
  friendlyName: string;
  friendlyNameShort: string;
  type: string;
  connection: string;
  rawModel: string;
  effectiveModel: string;
  currentModel: string;
  modelInputPrice: number | null;
  modelOutputPrice: number | null;
  currentInputPrice: number | null;
  currentOutputPrice: number | null;
  provider: string;
  family: string;
  projectsUsing: number;
  referencingProjects: string[];
  usageAssets: UsageAssetWithProject[];
}

export function LlmAuditTable() {
  const { state } = useDiag();
  const audit = state.parsedData.llmAudit;
  const loading = state.parsedData.llmAuditLoading;
  const isLoading = loading?.phase === 'running' || loading?.phase === 'queued';
  const loadingMessage =
    loading?.phase === 'running' || loading?.phase === 'done'
      ? loading.message
      : loading?.phase === 'error'
        ? loading.error
        : undefined;
  const loadingPct =
    loading?.phase === 'running' || loading?.phase === 'error' ? loading.progressPct : 0;
  const rows: LlmAuditRow[] = useMemo(() => audit?.rows || [], [audit]);

  const [statusFilter, setStatusFilter] = useState<Set<DisplayStatus>>(new Set());
  const [search, setSearch] = useState('');
  const [modalLlm, setModalLlm] = useState<GroupedRow | null>(null);

  const grouped: GroupedRow[] = useMemo(() => {
    const map = new Map<string, GroupedRow>();
    for (const r of rows) {
      if (r.status === 'not_applicable') continue;
      const key = r.llmId || `${r.connection || '-'}::${r.rawModel || r.effectiveModel || '-'}`;
      const assetsForRow: UsageAssetWithProject[] = (r.usageAssets || []).map((a) => ({
        ...a,
        projectKey: a.projectKey || r.projectKey,
      }));
      const existing = map.get(key);
      if (existing) {
        existing.usageAssets.push(...assetsForRow);
        continue;
      }
      map.set(key, {
        llmId: r.llmId || key,
        status: r.status as DisplayStatus,
        friendlyName: r.friendlyName || r.llmId || '',
        friendlyNameShort: r.friendlyNameShort || r.friendlyName || r.llmId || '',
        type: r.type || '',
        connection: r.connection || '',
        rawModel: r.rawModel || '',
        effectiveModel: r.effectiveModel || r.rawModel || '',
        currentModel: r.currentModel || '',
        modelInputPrice: r.modelInputPrice ?? null,
        modelOutputPrice: r.modelOutputPrice ?? null,
        currentInputPrice: r.currentInputPrice ?? null,
        currentOutputPrice: r.currentOutputPrice ?? null,
        provider: r.provider || '',
        family: r.family || '',
        projectsUsing: r.projectsUsing ?? 0,
        referencingProjects: r.referencingProjects ?? [],
        usageAssets: assetsForRow,
      });
    }
    return Array.from(map.values());
  }, [rows]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return grouped.filter((r) => {
      if (statusFilter.size > 0 && !statusFilter.has(r.status)) return false;
      if (!q) return true;
      return (
        r.friendlyName.toLowerCase().includes(q) ||
        r.llmId.toLowerCase().includes(q) ||
        r.effectiveModel.toLowerCase().includes(q) ||
        r.rawModel.toLowerCase().includes(q) ||
        (r.connection || '').toLowerCase().includes(q) ||
        (r.currentModel || '').toLowerCase().includes(q)
      );
    });
  }, [grouped, statusFilter, search]);

  const counts = useMemo(() => {
    const c: Record<DisplayStatus, number> = {
      ripoff: 0,
      obsolete: 0,
      current: 0,
      unknown: 0,
    };
    for (const r of grouped) c[r.status] += 1;
    return c;
  }, [grouped]);

  const distinctModels = useMemo(() => {
    const r = new Set<string>();
    const o = new Set<string>();
    for (const g of grouped) {
      const k = g.effectiveModel || g.rawModel;
      if (!k) continue;
      if (g.status === 'ripoff') r.add(k);
      if (g.status === 'obsolete') o.add(k);
    }
    return { ripoff: r.size, obsolete: o.size };
  }, [grouped]);

  const dssBaseUrl = useMemo(() => {
    const bUrl = getBackendUrl('/');
    try {
      const u = new URL(bUrl, window.location.origin);
      return `${u.protocol}//${u.host}`;
    } catch {
      return '';
    }
  }, []);

  const columns = useMemo<ColumnDef<GroupedRow>[]>(
    () => [
      {
        id: 'status',
        label: 'Status',
        defaultSortDir: 'asc',
        render: (r) => (
          <span
            title={STATUS_TOOLTIP[r.status]}
            className={`px-2 py-0.5 text-xs font-semibold rounded ${statusBadgeClass(r.status)}`}
          >
            {STATUS_LABEL[r.status]}
          </span>
        ),
        sortValue: (r) => STATUS_ORDER[r.status],
      },
      {
        id: 'friendlyName',
        label: 'LLM',
        defaultSortDir: 'asc',
        render: (r) => (
          <>
            <div className="text-[var(--text-primary)] text-sm" title={r.llmId}>
              {r.friendlyNameShort}
            </div>
            <UsageAssetList dssBaseUrl={dssBaseUrl} assets={r.usageAssets} />
          </>
        ),
        sortValue: (r) => r.friendlyName,
      },
      {
        id: 'type',
        label: 'Type',
        defaultSortDir: 'asc',
        render: (r) => (
          <span className="text-[11px] font-mono text-[var(--text-secondary)]">{r.type}</span>
        ),
        sortValue: (r) => r.type,
      },
      {
        id: 'connection',
        label: 'Connection',
        defaultSortDir: 'asc',
        render: (r) =>
          r.connection ? (
            <a
              href={`${dssBaseUrl}/admin/connections/${encodeURIComponent(r.connection)}/`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[var(--neon-cyan)] hover:underline text-sm"
            >
              {r.connection}
            </a>
          ) : (
            <span className="text-[var(--text-secondary)]">{'—'}</span>
          ),
        sortValue: (r) => r.connection || '',
      },
      {
        id: 'effectiveModel',
        label: 'Model',
        defaultSortDir: 'asc',
        render: (r) => (
          <>
            <div className="font-mono text-sm text-[var(--text-primary)] break-all">
              {r.effectiveModel || '—'}
            </div>
            {r.rawModel && r.effectiveModel && r.rawModel !== r.effectiveModel && (
              <div
                className="font-mono text-[11px] text-[var(--text-tertiary)] mt-0.5 break-all"
                title="Original DSS model string before CustomLLM unwrap"
              >
                raw: {r.rawModel}
              </div>
            )}
          </>
        ),
        sortValue: (r) => r.effectiveModel,
      },
      {
        id: 'currentModel',
        label: 'Replacement',
        defaultSortDir: 'asc',
        render: (r) =>
          r.currentModel ? (
            <span className="font-mono text-sm text-[var(--neon-cyan)] break-all">
              {r.currentModel}
            </span>
          ) : (
            <span className="text-[var(--text-tertiary)]">{'—'}</span>
          ),
        sortValue: (r) => r.currentModel || '',
      },
      {
        id: 'modelInputPrice',
        label: 'In $/1M',
        align: 'right',
        mono: true,
        cellClassName: (r) => priceDeltaClass(r.modelInputPrice, r.currentInputPrice),
        render: (r) => (
          <>
            {formatPrice(r.modelInputPrice)}
            {r.currentInputPrice != null &&
              r.modelInputPrice != null &&
              Math.abs(r.currentInputPrice - r.modelInputPrice) > 0.0001 && (
                <div className="text-[11px] text-[var(--text-tertiary)]">
                  vs {formatPrice(r.currentInputPrice)}
                </div>
              )}
          </>
        ),
        sortValue: (r) => r.modelInputPrice ?? -1,
      },
      {
        id: 'modelOutputPrice',
        label: 'Out $/1M',
        align: 'right',
        mono: true,
        cellClassName: (r) => priceDeltaClass(r.modelOutputPrice, r.currentOutputPrice),
        render: (r) => (
          <>
            {formatPrice(r.modelOutputPrice)}
            {r.currentOutputPrice != null &&
              r.modelOutputPrice != null &&
              Math.abs(r.currentOutputPrice - r.modelOutputPrice) > 0.0001 && (
                <div className="text-[11px] text-[var(--text-tertiary)]">
                  vs {formatPrice(r.currentOutputPrice)}
                </div>
              )}
          </>
        ),
        sortValue: (r) => r.modelOutputPrice ?? -1,
      },
      {
        id: 'projectsUsing',
        label: 'Projects',
        align: 'right',
        mono: true,
        render: (r) =>
          r.projectsUsing > 0 ? (
            <button
              type="button"
              onClick={() => setModalLlm(r)}
              className="font-mono text-sm text-[var(--neon-cyan)] hover:underline focus:outline-none"
              title="Show projects referencing this LLM"
            >
              {r.projectsUsing} {r.projectsUsing === 1 ? 'project' : 'projects'}
            </button>
          ) : (
            <span
              className="font-mono text-sm text-[var(--text-tertiary)]"
              title="No scanned asset references this LLM — candidate for retirement."
            >
              0 projects
            </span>
          ),
        sortValue: (r) => r.projectsUsing,
      },
    ],
    [dssBaseUrl],
  );

  const toggleStatusFilter = (s: DisplayStatus) => {
    setStatusFilter((prev) => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });
  };

  const filtersActive = statusFilter.size > 0 || search.trim().length > 0;

  return (
    <div className="space-y-4">
      <SummaryCard
        total={grouped.length}
        counts={counts}
        distinct={distinctModels}
        pricingFetchedAt={audit?.pricingFetchedAt}
        pricingError={audit?.pricingError}
        loading={isLoading}
      />

      <div className="rounded-xl overflow-hidden" id="llm-audit-table">
        <div className="px-4 py-3 border-b border-[var(--border-glass)]">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h4 className="text-lg font-semibold text-[var(--text-primary)]">
              {grouped.length > 0
                ? filtersActive
                  ? `${filtered.length} of ${grouped.length} LLMs`
                  : `${grouped.length} LLMs`
                : 'LLMs'}
            </h4>
            <div className="flex flex-wrap items-center gap-2">
              {FILTER_STATUSES.map((s) => {
                const active = statusFilter.has(s);
                const c = counts[s];
                return (
                  <button
                    key={s}
                    type="button"
                    title={STATUS_TOOLTIP[s]}
                    onClick={() => toggleStatusFilter(s)}
                    className={`px-2 py-0.5 text-xs font-semibold rounded transition-all ${statusBadgeClass(s)} ${active ? 'ring-2 ring-[var(--neon-cyan)]/60' : 'opacity-70 hover:opacity-100'}`}
                  >
                    {STATUS_LABEL[s]} {c}
                  </button>
                );
              })}
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search model / connection..."
                className="ml-2 px-2 py-1 text-xs rounded bg-[var(--bg-elevated)] border border-[var(--border-glass)] text-[var(--text-primary)] placeholder-[var(--text-tertiary)] focus:outline-none focus:border-[var(--neon-cyan)]/50 w-[220px]"
              />
            </div>
          </div>
        </div>

        {audit?.pricingError && (
          <div className="px-4 py-3 border-b border-[var(--border-glass)]">
            <div
              className="px-2.5 py-1 rounded bg-[var(--status-warning-bg)]
                         border border-[var(--status-warning-border)] text-sm"
            >
              <span className="text-[var(--neon-amber)] font-medium">
                Pricing catalog unreachable — models are listed without pricing or obsolescence
                verdicts.
              </span>{' '}
              <span className="text-[var(--text-tertiary)] font-mono text-xs break-all">
                {audit.pricingError}
              </span>
            </div>
          </div>
        )}
        {(audit?.failedProjectCount ?? 0) > 0 && (
          <div className="px-4 py-3 border-b border-[var(--border-glass)]">
            <ScanIncompleteNotice
              failedProjectCount={audit?.failedProjectCount}
              scannedProjectCount={audit?.scannedProjectCount}
            />
          </div>
        )}

        {isLoading && (
          <div className="px-4 py-3 border-b border-[var(--border-glass)]">
            <div className="flex items-center justify-between text-xs text-[var(--text-secondary)]">
              <span>{loadingMessage || 'Scanning...'}</span>
              <span className="font-mono">
                {Math.max(0, Math.min(100, Math.round(loadingPct)))}%
              </span>
            </div>
            <div className="mt-2 h-2 rounded-full bg-[var(--bg-glass)] overflow-hidden">
              <div
                className="h-full rounded-full bg-gradient-to-r from-[var(--neon-cyan)] to-[var(--neon-green)] transition-all duration-300 ease-out"
                style={{
                  width: `${Math.max(0, Math.min(100, Math.round(loadingPct)))}%`,
                }}
              />
            </div>
          </div>
        )}

        {grouped.length === 0 && !isLoading ? (
          <div className="p-6 text-sm text-[var(--text-secondary)]">
            No LLMs found. Either this instance has no LLM Mesh connections configured, or the scan
            failed — see the page logs for details.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <DataGrid
              rows={filtered}
              columns={columns}
              rowKey={(r) => r.llmId}
              defaultSortColumnId="status"
              filtersActive={filtersActive}
              noMatchMessage="No LLMs match the current filters."
              rowClassName={() => '[&>td]:align-top'}
              scroll="none"
            />
          </div>
        )}
      </div>

      {modalLlm && (
        <ProjectsModal row={modalLlm} onClose={() => setModalLlm(null)} dssBaseUrl={dssBaseUrl} />
      )}
    </div>
  );
}

function ProjectsModal({
  row,
  onClose,
  dssBaseUrl,
}: {
  row: GroupedRow;
  onClose: () => void;
  dssBaseUrl: string;
}) {
  const projects = row.referencingProjects;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="bg-[var(--bg-surface)] rounded-xl shadow-2xl border border-[var(--border-glass)] max-w-2xl w-full max-h-[80vh] flex flex-col mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-4 py-3 border-b border-[var(--border-glass)] flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h3 className="text-base font-semibold text-[var(--text-primary)]">
              Projects referencing{' '}
              <span className="font-mono text-[var(--neon-cyan)] break-all">{row.llmId}</span>
            </h3>
            <p className="text-[11px] text-[var(--text-tertiary)] mt-1">
              {row.projectsUsing} {row.projectsUsing === 1 ? 'project' : 'projects'}
              {projects.length < row.projectsUsing ? ` (showing first ${projects.length})` : ''}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-[var(--text-secondary)] hover:text-[var(--text-primary)] text-lg leading-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <div className="px-4 py-3 overflow-y-auto flex-1">
          {projects.length === 0 ? (
            <div className="text-sm text-[var(--text-secondary)]">No referencing projects.</div>
          ) : (
            <ul className="space-y-1">
              {projects.map((pk) => (
                <li
                  key={pk}
                  className="font-mono text-sm px-2 py-1 rounded hover:bg-[var(--bg-glass-hover)]"
                >
                  <a
                    href={`${dssBaseUrl}/projects/${pk}/`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[var(--neon-cyan)] hover:underline"
                  >
                    {pk}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="px-4 py-2 border-t border-[var(--border-glass)] text-[11px] text-[var(--text-tertiary)]">
          References found in prompt/LLM recipes, knowledge banks, agents, and code recipes (Python
          / R / Scala / PySpark / SparkScala). Notebooks and dynamically-built llmIds are not
          scanned.
        </div>
      </div>
    </div>
  );
}

function SummaryCard({
  total,
  counts,
  distinct,
  pricingFetchedAt,
  pricingError,
  loading,
}: {
  total: number;
  counts: Record<DisplayStatus, number>;
  distinct: { ripoff: number; obsolete: number };
  pricingFetchedAt: string | null | undefined;
  pricingError?: string | null;
  loading: boolean;
}) {
  const tiles: Array<{ label: string; value: number; sub?: string; color: string }> = [
    {
      label: 'LLMs',
      value: total,
      color: 'text-[var(--text-primary)]',
    },
    {
      label: 'Overpriced',
      value: counts.ripoff,
      sub: distinct.ripoff > 0 ? `${distinct.ripoff} distinct model(s)` : undefined,
      color: 'text-[var(--neon-red)]',
    },
    {
      label: 'Obsolete',
      value: counts.obsolete,
      sub: distinct.obsolete > 0 ? `${distinct.obsolete} distinct model(s)` : undefined,
      color: 'text-[var(--neon-amber)]',
    },
    {
      label: 'Current',
      value: counts.current,
      color: 'text-[var(--neon-green)]',
    },
    {
      label: 'Unknown',
      value: counts.unknown,
      sub: 'See raw model string',
      color: 'text-[var(--text-secondary)]',
    },
  ];

  return (
    <div className="rounded-xl">
      <div className="px-4 py-3 border-b border-[var(--border-glass)] flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-[var(--text-primary)]">LLM Model Audit</h3>
          <p className="text-[11px] text-[var(--text-tertiary)] mt-0.5">
            {pricingError
              ? 'Cross-instance scan. Pricing catalog unreachable — verdicts unavailable.'
              : `Cross-instance scan against the LiteLLM pricing catalog. Pricing fetched ${formatRelative(pricingFetchedAt)}.`}
          </p>
        </div>
        {loading && (
          <span className="text-xs text-[var(--neon-cyan)] flex items-center gap-1.5">
            <Spinner size="w-3 h-3" color="border-[var(--neon-cyan)]" />
            scanning
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 divide-x divide-[var(--border-glass)]">
        {tiles.map((t) => (
          <div key={t.label} className="px-4 py-3">
            <div className="text-[11px] uppercase tracking-wider text-[var(--text-tertiary)]">
              {t.label}
            </div>
            <div className={`text-2xl font-semibold ${t.color}`}>{t.value}</div>
            {t.sub && <div className="text-[11px] text-[var(--text-tertiary)] mt-0.5">{t.sub}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
