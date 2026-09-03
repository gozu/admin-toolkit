import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { useDiag } from '../context/DiagContext';
import { Modal } from './Modal';
import { useModal } from '../hooks/useModal';
import { useRowSelection } from '../hooks/useRowSelection';
import { fetchJson } from '../utils/api';
import { dssUrls, getDssBaseUrl } from '../utils/codeEnvUsageLinks';
import { DataGrid } from './common/DataGrid';
import { ProgressIndicator } from './common/ProgressIndicator';
import { ScanIncompleteNotice } from './ScanIncompleteNotice';
import { computePlacementScan } from '../state/computePlacementStore';
import { useRedState } from '../state/redUnlockStore';
import { pushToast } from '../state/toastStore';
import type { ColumnDef } from '../utils/dataGridTypes';
import type {
  ComputeMigrationOp,
  ComputeMigrationResult,
  ComputeMigrationStrategy,
  ComputePlacementKind,
  ComputePlacementObjectType,
  ComputePlacementRow,
  EmailPreviewItem,
  EmailPreviewResponse,
  EmailSendResponse,
  OutreachRecipient,
} from '../types';

// ── constants ────────────────────────────────────────────────────────────────

const CAMPAIGN = 'compute_local' as const;
const KEEP_CLUSTER = '__KEEP__';
const PAGE_SIZE = 250;

type PlacementFilter = 'all' | ComputePlacementKind;

const PLACEMENT_LABEL: Record<ComputePlacementKind, string> = {
  local: 'Local (DSS host)',
  container: 'Containerized',
  spark: 'Spark',
};

const OBJECT_TYPE_LABEL: Record<ComputePlacementObjectType, string> = {
  PROJECT: 'Project defaults',
  RECIPE: 'Recipes',
  WEBAPP: 'Webapps',
  ML_TASK: 'ML tasks',
  NOTEBOOK: 'Notebooks',
};
const OBJECT_TYPES = Object.keys(OBJECT_TYPE_LABEL) as ComputePlacementObjectType[];

const RESOLVED_LABEL: Record<string, string> = {
  object: 'set on object',
  project: 'project default',
  instance: 'instance default',
  kernel: 'last kernel',
  engine: 'Spark engine',
};

// ── helpers ──────────────────────────────────────────────────────────────────

function placementClass(kind: ComputePlacementKind): string {
  if (kind === 'local') return 'border-[var(--neon-yellow)]/40 bg-[var(--neon-yellow)]/10 text-[var(--neon-yellow)]';
  if (kind === 'container') return 'border-[var(--neon-cyan)]/40 bg-[var(--neon-cyan)]/10 text-[var(--neon-cyan)]';
  return 'border-[var(--border-glass)] bg-[var(--bg-glass)] text-[var(--text-secondary)]';
}

function PlacementPill({ kind }: { kind: ComputePlacementKind }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium whitespace-nowrap ${placementClass(kind)}`}>
      {PLACEMENT_LABEL[kind]}
    </span>
  );
}

function objectHref(row: ComputePlacementRow): string {
  if (row.objectType === 'RECIPE') return dssUrls.recipe(row.projectKey, row.objectId);
  if (row.objectType === 'WEBAPP') return dssUrls.webapp(row.projectKey, row.objectId, row.objectName);
  if (row.objectType === 'NOTEBOOK') return dssUrls.notebook(row.projectKey, row.objectId);
  if (row.objectType === 'ML_TASK' && row.extra.analysisId && row.extra.mlTaskId) {
    // ui-router: projects.project.analyses.analysis.ml.{pred,clust}mltask.list — no trailing slash.
    const family = String(row.extra.taskType || '').toUpperCase() === 'CLUSTERING' ? 'c' : 'p';
    return `${getDssBaseUrl()}/projects/${encodeURIComponent(row.projectKey)}/analysis/${encodeURIComponent(row.extra.analysisId)}/ml/${family}/${encodeURIComponent(row.extra.mlTaskId)}/list`;
  }
  return dssUrls.project(row.projectKey);
}

function configHref(): string {
  return `${getDssBaseUrl()}/admin/general/containers/#adminSettingUIView`;
}

function clusterHref(clusterId: string): string {
  // ui-router: admin.clusters.cluster = /admin/clusters/:clusterId — no trailing slash.
  return `${getDssBaseUrl()}/admin/clusters/${encodeURIComponent(clusterId)}`;
}

function Stat({ label, value, tone = 'neutral', hint }: { label: string; value: number | string; tone?: 'neutral' | 'warn' | 'ok'; hint?: string }) {
  const color = tone === 'warn' ? 'text-[var(--neon-yellow)]' : tone === 'ok' ? 'text-[var(--neon-cyan)]' : 'text-[var(--text-primary)]';
  return (
    <div className="rounded-lg px-4 py-3" title={hint}>
      <div className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">{label}</div>
      <div className={`mt-1 font-mono text-xl font-semibold ${color}`}>{value}</div>
    </div>
  );
}

function Chip({ active, onClick, children, title }: { active: boolean; onClick: () => void; children: ReactNode; title?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
        active
          ? 'border-[var(--neon-cyan)]/60 bg-[var(--neon-cyan)]/10 text-[var(--text-primary)]'
          : 'border-[var(--border-glass)] text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)]'
      }`}
    >
      {children}
    </button>
  );
}

const SELECT_CLASS = 'min-h-9 rounded border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-2 py-1.5 text-sm text-[var(--text-primary)]';
const BTN_SECONDARY = 'rounded border border-[var(--border-glass)] px-3 py-1.5 text-sm text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] hover:text-[var(--text-primary)] disabled:opacity-50';

function opLabel(op: ComputeMigrationOp): string {
  if (op.kind === 'project-cluster') return `${op.projectKey}: cluster ${op.from} → ${op.to}`;
  if (op.kind === 'project-default') return `${op.projectKey}: ${op.objectKind || op.surface} → ${op.to}`;
  if (op.kind === 'object-inherit') return `${op.projectKey} / ${op.objectName}: NONE → inherit project default`;
  if (op.kind === 'object-unchanged') return `${op.projectKey} / ${op.objectName}: ${op.note || 'unchanged'}`;
  return `${op.projectKey} / ${op.objectName} (${op.objectKind || op.objectType}): ${op.from} → ${op.to}`;
}

function opStatusClass(status: ComputeMigrationOp['status']): string {
  if (status === 'failed') return 'text-[var(--neon-red)]';
  if (status === 'updated') return 'text-[var(--neon-cyan)]';
  if (status === 'unchanged') return 'text-[var(--text-muted)]';
  return 'text-[var(--neon-yellow)]';
}

function MigrationPlan({ result }: { result: ComputeMigrationResult }) {
  return (
    <div className="rounded-lg border border-[var(--border-glass)] bg-[var(--bg-elevated)] p-3 text-sm">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-[var(--text-primary)]">
        <span>{result.dryRun ? 'Plan' : 'Applied'}: <span className="font-mono">{result.matchedRows}</span> object{result.matchedRows === 1 ? '' : 's'}</span>
        <span className="text-xs text-[var(--text-muted)]">
          {result.dryRun ? `${result.plannedOps} change${result.plannedOps === 1 ? '' : 's'}` : `${result.updatedOps} updated`}
          {result.unchangedOps > 0 ? ` · ${result.unchangedOps} unchanged` : ''}
          {result.failedOps > 0 ? ` · ${result.failedOps} failed` : ''}
        </span>
        <span className="text-xs text-[var(--text-muted)]">
          → <span className="font-mono">{result.targetConfig}</span>
          {result.clusterId ? <> on <span className="font-mono">{result.clusterId}</span></> : null}
          {' · '}{result.strategy === 'objects' ? 'pin each object' : 'project defaults'}
        </span>
      </div>
      <div className="mt-2 max-h-72 space-y-0.5 overflow-auto font-mono text-xs">
        {result.results.slice(0, 500).map((op, idx) => (
          <div key={`${op.rowId || op.projectKey}-${op.kind}-${idx}`} className={opStatusClass(op.status)}>
            <span className="inline-block w-20 uppercase text-[10px] tracking-wide">{op.status}</span>
            {opLabel(op)}
            {op.error ? <span className="ml-2 text-[var(--neon-red)]">— {op.error}</span> : null}
          </div>
        ))}
        {result.results.length === 0 && <div className="text-[var(--text-muted)]">Nothing to change.</div>}
      </div>
    </div>
  );
}

interface OwnerGroup {
  owner: string;
  email: string;
  displayName: string;
  rows: ComputePlacementRow[];
}

function groupByOwner(rows: ComputePlacementRow[]): OwnerGroup[] {
  const groups = new Map<string, OwnerGroup>();
  for (const row of rows) {
    const group = groups.get(row.owner) || { owner: row.owner, email: row.ownerEmail, displayName: row.ownerDisplayName, rows: [] };
    if (!group.email && row.ownerEmail) group.email = row.ownerEmail;
    group.rows.push(row);
    groups.set(row.owner, group);
  }
  return Array.from(groups.values()).sort((a, b) => b.rows.length - a.rows.length || a.owner.localeCompare(b.owner));
}

function ownerGroupToRecipient(group: OwnerGroup): OutreachRecipient {
  const projectKeys = Array.from(new Set(group.rows.map((r) => r.projectKey))).sort();
  const projectNames = new Map(group.rows.map((r) => [r.projectKey, r.projectName]));
  return {
    recipientKey: group.owner,
    owner: group.displayName || group.owner,
    email: group.email || group.owner,
    projectKeys,
    codeEnvNames: [],
    usageDetails: group.rows.map((r) => ({
      usageType: r.objectType,
      objectType: r.objectType,
      objectId: r.objectId,
      objectName: r.objectName,
      projectKey: r.projectKey,
      codeEnvName: r.resolvedFrom === 'object' ? 'Local compute · set on the object' : `Local compute · via ${RESOLVED_LABEL[r.resolvedFrom] || r.resolvedFrom}`,
      codeEnvKey: 'local',
    })),
    projectKeyForSend: projectKeys[0] || null,
    projects: projectKeys.map((key) => ({
      projectKey: key,
      name: projectNames.get(key) || key,
      codeEnvCount: 0,
      codeEnvNames: [],
      totalObjects: group.rows.filter((r) => r.projectKey === key).length,
    })),
  };
}

// ── page ─────────────────────────────────────────────────────────────────────

export function ComputePlacement() {
  const { data, loading, progressPct, scanPhase, scanMessage, error, scanStarted } = computePlacementScan.use();
  const { state } = useDiag();
  const { authed: unlocked } = useRedState();

  const [placementFilter, setPlacementFilter] = useState<PlacementFilter>('all');
  const [typeFilter, setTypeFilter] = useState<Set<ComputePlacementObjectType>>(() => new Set(OBJECT_TYPES.filter((t) => t !== 'PROJECT')));
  const [configFilter, setConfigFilter] = useState('');
  const [clusterFilter, setClusterFilter] = useState('');
  const [query, setQuery] = useState('');
  const [pageLimit, setPageLimit] = useState(PAGE_SIZE);
  const { selectedKeys, toggleSelect, setSelectedKeys, clear: clearSelection } = useRowSelection();

  const [targetConfigRaw, setTargetConfig] = useState('');
  const [clusterChoice, setClusterChoice] = useState(KEEP_CLUSTER);
  const [strategy, setStrategy] = useState<ComputeMigrationStrategy>('objects');
  const [plan, setPlan] = useState<ComputeMigrationResult | null>(null);
  const [migrateBusy, setMigrateBusy] = useState(false);
  const [migrateError, setMigrateError] = useState<string | null>(null);
  const [confirmText, setConfirmText] = useState('');
  const confirmModal = useModal();

  const previewModal = useModal();
  const [previewItems, setPreviewItems] = useState<EmailPreviewItem[]>([]);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [sendLoading, setSendLoading] = useState(false);
  const [sendResult, setSendResult] = useState<EmailSendResponse | null>(null);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [skippedOwners, setSkippedOwners] = useState<string[]>([]);

  useEffect(() => {
    if (!scanStarted) void computePlacementScan.load();
  }, [scanStarted]);

  const rows = useMemo(() => data?.rows || [], [data?.rows]);
  const configNames = data?.configNames || [];
  const configTypes = data?.configTypes || {};
  const clusters = data?.clusters || [];
  const summary = data?.summary;
  const rowsById = useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows]);

  const clusterIds = useMemo(
    () => Array.from(new Set(rows.map((r) => r.clusterId).filter((c): c is string => Boolean(c)))).sort(),
    [rows],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter((row) => {
      if (placementFilter !== 'all' && row.placement !== placementFilter) return false;
      if (!typeFilter.has(row.objectType)) return false;
      if (configFilter && row.effectiveConf !== configFilter) return false;
      if (clusterFilter && row.clusterId !== clusterFilter) return false;
      if (q) {
        const hay = `${row.projectKey} ${row.projectName} ${row.objectName} ${row.objectKind} ${row.owner} ${row.ownerEmail}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [rows, placementFilter, typeFilter, configFilter, clusterFilter, query]);

  const visible = useMemo(() => filtered.slice(0, pageLimit), [filtered, pageLimit]);
  const filteredMigratable = useMemo(() => filtered.filter((r) => r.migratable), [filtered]);
  const filteredLocal = useMemo(() => filtered.filter((r) => r.placement === 'local'), [filtered]);

  const selectedRows = useMemo(
    () => Array.from(selectedKeys).map((id) => rowsById.get(id)).filter((r): r is ComputePlacementRow => Boolean(r)),
    [selectedKeys, rowsById],
  );
  const selectedMigratable = useMemo(() => selectedRows.filter((r) => r.migratable), [selectedRows]);
  const selectedLocal = useMemo(() => selectedRows.filter((r) => r.placement === 'local'), [selectedRows]);
  const allFilteredMigratableSelected = filteredMigratable.length > 0 && filteredMigratable.every((r) => selectedKeys.has(r.id));

  // Target config defaults to the instance default, then the first K8s config.
  const targetConfig =
    targetConfigRaw && configNames.includes(targetConfigRaw)
      ? targetConfigRaw
      : data?.globalDefaultConfig && configNames.includes(data.globalDefaultConfig)
        ? data.globalDefaultConfig
        : configNames.find((n) => configTypes[n] === 'KUBERNETES') || configNames[0] || '';
  const targetIsK8s = configTypes[targetConfig] === 'KUBERNETES';
  const clusterId = targetIsK8s && clusterChoice !== KEEP_CLUSTER && clusters.some((c) => c.id === clusterChoice) ? clusterChoice : null;

  const toggleType = (type: ComputePlacementObjectType) => {
    setTypeFilter((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
    setPageLimit(PAGE_SIZE);
  };

  const toggleAllMigratable = () => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (allFilteredMigratableSelected) filteredMigratable.forEach((r) => next.delete(r.id));
      else filteredMigratable.forEach((r) => next.add(r.id));
      return next;
    });
  };

  const runMigration = async (dryRun: boolean) => {
    if (!targetConfig || selectedMigratable.length === 0) return;
    setMigrateBusy(true);
    setMigrateError(null);
    try {
      const result = await fetchJson<ComputeMigrationResult>('/api/compute-placement/migrate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          rowIds: selectedMigratable.map((r) => r.id),
          targetConfig,
          clusterId,
          strategy,
          dryRun,
        }),
      });
      setPlan(result);
      if (!dryRun) {
        confirmModal.close();
        setConfirmText('');
        clearSelection();
        pushToast(
          result.failedOps > 0 ? 'error' : 'success',
          result.failedOps > 0
            ? `Migration finished with ${result.failedOps} failure${result.failedOps === 1 ? '' : 's'}`
            : `Migrated ${result.matchedRows} object${result.matchedRows === 1 ? '' : 's'} to ${result.targetConfig}`,
          { detail: `${result.updatedOps} setting${result.updatedOps === 1 ? '' : 's'} written` },
        );
        await computePlacementScan.load(true);
      }
    } catch (err) {
      setMigrateError(err instanceof Error ? err.message : String(err));
    } finally {
      setMigrateBusy(false);
    }
  };

  const openConfirm = () => {
    setConfirmText('');
    confirmModal.open();
  };

  const selectedChannel = state.parsedData.configuredMailChannel || state.parsedData.mailChannels?.[0]?.id || '';

  const openEmailPreview = async () => {
    const source = selectedLocal.length > 0 ? selectedLocal : filteredLocal;
    const groups = groupByOwner(source);
    const withEmail = groups.filter((g) => g.email);
    setSkippedOwners(groups.filter((g) => !g.email).map((g) => g.owner));
    if (withEmail.length === 0) {
      setEmailError('None of the owners has an email address on their DSS profile.');
      return;
    }
    setPreviewLoading(true);
    setSendResult(null);
    setEmailError(null);
    try {
      const response = await fetchJson<EmailPreviewResponse>('/api/tools/email/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaign: CAMPAIGN, recipients: withEmail.map(ownerGroupToRecipient) }),
      });
      setPreviewItems(response.previews);
      previewModal.open();
    } catch (err) {
      setEmailError(err instanceof Error ? err.message : String(err));
    } finally {
      setPreviewLoading(false);
    }
  };

  const sendEmails = async () => {
    if (previewItems.length === 0) return;
    setSendLoading(true);
    setEmailError(null);
    try {
      const response = await fetchJson<EmailSendResponse>('/api/tools/email/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaign: CAMPAIGN, channelId: selectedChannel || undefined, plainText: false, previews: previewItems }),
      });
      setSendResult(response);
      pushToast(response.sentCount === response.requestedCount ? 'success' : 'error', `Sent ${response.sentCount}/${response.requestedCount} emails`);
    } catch (err) {
      setEmailError(err instanceof Error ? err.message : String(err));
    } finally {
      setSendLoading(false);
    }
  };

  const columns = useMemo<ColumnDef<ComputePlacementRow>[]>(() => [
    {
      id: 'select',
      label: '',
      width: '2.5rem',
      align: 'center',
      render: (row) =>
        row.migratable ? (
          <input
            type="checkbox"
            checked={selectedKeys.has(row.id)}
            onChange={() => toggleSelect(row.id)}
            onClick={(e) => e.stopPropagation()}
            className="h-4 w-4 accent-[var(--neon-cyan)]"
            aria-label={`Select ${row.objectName}`}
          />
        ) : row.placement === 'local' ? (
          <span className="text-[var(--text-muted)]" title={row.migrateBlocker || 'Not migratable'}>–</span>
        ) : null,
    },
    {
      id: 'project',
      label: 'Project',
      width: '16%',
      sortValue: (row) => row.projectKey,
      defaultSortDir: 'asc',
      render: (row) => (
        <a href={dssUrls.project(row.projectKey)} target="_blank" rel="noreferrer" className="group flex min-w-0 flex-col">
          <span className="truncate font-mono text-xs font-semibold text-[var(--text-primary)] group-hover:text-[var(--neon-cyan)] group-hover:underline">{row.projectKey}</span>
          {row.projectName !== row.projectKey && <span className="truncate text-[11px] text-[var(--text-muted)]">{row.projectName}</span>}
        </a>
      ),
    },
    {
      id: 'object',
      label: 'Object',
      width: '24%',
      sortValue: (row) => `${row.objectType} ${row.objectName}`.toLowerCase(),
      defaultSortDir: 'asc',
      render: (row) => (
        <a href={objectHref(row)} target="_blank" rel="noreferrer" className="group flex min-w-0 flex-col">
          <span className="truncate text-sm text-[var(--text-primary)] group-hover:text-[var(--neon-cyan)] group-hover:underline">
            {row.objectType === 'PROJECT' ? row.objectKind : row.objectName}
          </span>
          <span className="truncate text-[11px] text-[var(--text-muted)]">
            {row.objectType === 'PROJECT' ? 'applies to inheriting objects' : row.objectKind}
            {row.extra.kernel ? ` · ${row.extra.kernel}` : ''}
          </span>
        </a>
      ),
    },
    {
      id: 'owner',
      label: 'Owner',
      width: '12%',
      sortValue: (row) => row.owner.toLowerCase(),
      defaultSortDir: 'asc',
      render: (row) => (
        <span className="flex min-w-0 flex-col" title={row.ownerEmail || 'No email on the DSS profile'}>
          <span className="truncate font-mono text-xs text-[var(--text-primary)]">{row.owner}</span>
          <span className="truncate text-[11px] text-[var(--text-muted)]">{row.ownerSource === 'object' ? 'last modified by' : 'project owner'}</span>
        </span>
      ),
    },
    {
      id: 'placement',
      label: 'Placement',
      width: '12%',
      sortValue: (row) => row.placement,
      defaultSortDir: 'asc',
      render: (row) => (
        <span className="flex min-w-0 flex-col gap-0.5">
          <PlacementPill kind={row.placement} />
          <span className="truncate text-[11px] text-[var(--text-muted)]" title={row.migrateBlocker || undefined}>
            {RESOLVED_LABEL[row.resolvedFrom] || row.resolvedFrom}
            {row.containerMode === 'NONE' && row.resolvedFrom === 'object' ? ' (NONE)' : ''}
          </span>
        </span>
      ),
    },
    {
      id: 'config',
      label: 'Container config',
      width: '16%',
      sortValue: (row) => row.effectiveConf || '',
      defaultSortDir: 'asc',
      render: (row) =>
        row.effectiveConf ? (
          <a href={configHref()} target="_blank" rel="noreferrer" className="flex min-w-0 flex-col">
            <span className="truncate font-mono text-xs text-[var(--neon-cyan)] hover:underline">{row.effectiveConf}</span>
            {row.configType && <span className="text-[11px] text-[var(--text-muted)]">{row.configType.toLowerCase()}</span>}
          </a>
        ) : (
          <span className="text-[var(--text-muted)]">—</span>
        ),
    },
    {
      id: 'cluster',
      label: 'Cluster',
      width: '14%',
      sortValue: (row) => row.clusterId || '',
      defaultSortDir: 'asc',
      render: (row) =>
        row.clusterId ? (
          <a href={clusterHref(row.clusterId)} target="_blank" rel="noreferrer" className="flex min-w-0 flex-col">
            <span className="truncate font-mono text-xs text-[var(--text-primary)] hover:text-[var(--neon-cyan)] hover:underline">{row.clusterId}</span>
            <span className="text-[11px] text-[var(--text-muted)]">{row.clusterSource === 'project' ? 'project setting' : 'instance default'}</span>
          </a>
        ) : row.configType === 'KUBERNETES' ? (
          <span className="text-[11px] text-[var(--neon-red)]" title="Kubernetes config with no cluster selected anywhere">no cluster</span>
        ) : (
          <span className="text-[var(--text-muted)]">—</span>
        ),
    },
  ], [selectedKeys, toggleSelect]);

  const byPlacement = summary?.byPlacement || {};
  const previewsWithoutEmail = skippedOwners.length;

  return (
    <div className="w-full py-4 space-y-4">
      <div className="px-4">
        <h2 className="text-xl font-semibold text-[var(--text-primary)]">Compute Placement</h2>
        <p className="text-sm text-[var(--text-muted)]">
          Where every compute-using object runs: on the DSS host or in a container execution config (and on which cluster).
          Select local objects to migrate them or to email their owners.
        </p>
        <ScanIncompleteNotice failedProjectCount={data?.failedProjectCount} scannedProjectCount={data?.scannedProjectCount} className="mt-2" />
        {data?.timedOut && (
          <div className="mt-2 text-xs text-[var(--neon-yellow)]">Scan hit the timeout — results cover the projects scanned so far.</div>
        )}
        {(data?.warnings || []).map((w) => (
          <div key={w} className="mt-1 text-xs text-[var(--neon-yellow)]">{w}</div>
        ))}
      </div>

      {error && (
        <div className="mx-4 rounded-lg border border-[var(--neon-red)]/30 bg-[var(--neon-red)]/10 px-4 py-3 text-sm text-[var(--neon-red)]">{error}</div>
      )}

      {loading && (
        <div className="rounded-lg px-4 py-3">
          <ProgressIndicator active pct={progressPct} message={scanMessage} phase={scanPhase} />
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        <Stat label="Objects" value={summary?.objectRowCount ?? 0} hint="Recipes, webapps, ML tasks and notebooks across all projects" />
        <Stat label="Local" value={summary?.localCount ?? 0} tone={(summary?.localCount ?? 0) > 0 ? 'warn' : 'neutral'} hint="Run on the DSS host" />
        <Stat label="Containerized" value={summary?.containerCount ?? 0} tone="ok" />
        <Stat label="Spark" value={summary?.sparkCount ?? 0} />
        <Stat label="Migratable" value={summary?.migratableCount ?? 0} hint="Local rows the toolkit can switch to a container config (incl. project defaults)" />
        <Stat label="Owners w/ local" value={summary?.localOwnerCount ?? 0} hint="Distinct owners of local objects" />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 px-4 text-xs text-[var(--text-muted)]">
        <span>
          Instance default config:{' '}
          {data?.globalDefaultConfig
            ? <a href={configHref()} target="_blank" rel="noreferrer" className="font-mono text-[var(--neon-cyan)] hover:underline">{data.globalDefaultConfig}</a>
            : <span className="text-[var(--neon-yellow)]">none — inheriting objects run locally</span>}
          {' · '}default cluster:{' '}
          {data?.globalDefaultClusterId
            ? <a href={clusterHref(data.globalDefaultClusterId)} target="_blank" rel="noreferrer" className="font-mono text-[var(--text-primary)] hover:underline">{data.globalDefaultClusterId}</a>
            : <span>none</span>}
        </span>
        <span>
          {summary ? `${summary.projectsLocalByDefault}/${summary.scannedProjectCount} projects default to local · ${Math.round(data?.elapsedMs || 0)}ms` : ''}
        </span>
      </div>

      {/* filters */}
      <div className="flex flex-wrap items-center gap-2 px-4">
        <div className="flex items-center gap-1 rounded-lg border border-[var(--border-glass)] p-1">
          {(['all', 'local', 'container', 'spark'] as PlacementFilter[]).map((kind) => (
            <button
              key={kind}
              type="button"
              onClick={() => { setPlacementFilter(kind); setPageLimit(PAGE_SIZE); }}
              className={`rounded px-2.5 py-1 text-xs ${placementFilter === kind ? 'bg-[var(--bg-glass-hover)] text-[var(--text-primary)]' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}
            >
              {kind === 'all' ? 'All' : PLACEMENT_LABEL[kind]}
              <span className="ml-1 font-mono text-[10px] text-[var(--text-muted)]">
                {kind === 'all' ? summary?.objectRowCount ?? 0 : byPlacement[kind] ?? 0}
              </span>
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1">
          {OBJECT_TYPES.map((type) => (
            <Chip key={type} active={typeFilter.has(type)} onClick={() => toggleType(type)} title={type === 'PROJECT' ? 'Project-level defaults (3 per project)' : undefined}>
              {OBJECT_TYPE_LABEL[type]}
              <span className="ml-1 font-mono text-[10px] text-[var(--text-muted)]">
                {type === 'PROJECT' ? summary?.projectDefaultRowCount ?? 0 : summary?.byObjectType?.[type] ?? 0}
              </span>
            </Chip>
          ))}
        </div>
        <select value={configFilter} onChange={(e) => { setConfigFilter(e.target.value); setPageLimit(PAGE_SIZE); }} className={SELECT_CLASS} aria-label="Filter by config">
          <option value="">Any config</option>
          {configNames.map((name) => <option key={name} value={name}>{name} ({summary?.byConfig?.[name] ?? 0})</option>)}
        </select>
        <select value={clusterFilter} onChange={(e) => { setClusterFilter(e.target.value); setPageLimit(PAGE_SIZE); }} className={SELECT_CLASS} aria-label="Filter by cluster">
          <option value="">Any cluster</option>
          {clusterIds.map((id) => <option key={id} value={id}>{id} ({summary?.byCluster?.[id] ?? 0})</option>)}
        </select>
        <input
          value={query}
          onChange={(e) => { setQuery(e.target.value); setPageLimit(PAGE_SIZE); }}
          placeholder="Search project, object, owner…"
          className={`${SELECT_CLASS} min-w-[14rem] flex-1`}
        />
      </div>

      {/* actions */}
      <div className="mx-4 rounded-xl border border-[var(--border-glass)] bg-[var(--bg-glass)] p-4">
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
            <input
              type="checkbox"
              checked={allFilteredMigratableSelected}
              onChange={toggleAllMigratable}
              disabled={filteredMigratable.length === 0}
              className="h-4 w-4 accent-[var(--neon-cyan)]"
            />
            Select all migratable in view
            <span className="font-mono text-xs text-[var(--text-muted)]">{filteredMigratable.length}</span>
          </label>
          <span className="text-sm text-[var(--text-secondary)]">
            Selected <span className="font-mono text-[var(--text-primary)]">{selectedRows.length}</span>
            {selectedRows.length > selectedMigratable.length && (
              <span className="text-xs text-[var(--text-muted)]"> ({selectedMigratable.length} migratable)</span>
            )}
          </span>
          {selectedRows.length > 0 && (
            <button type="button" onClick={clearSelection} className={BTN_SECONDARY}>Clear</button>
          )}
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={() => void openEmailPreview()}
              disabled={previewLoading || (selectedLocal.length === 0 && filteredLocal.length === 0)}
              className={BTN_SECONDARY}
              title={selectedLocal.length > 0 ? `Email the owners of the ${selectedLocal.length} selected local objects` : `Email the owners of all ${filteredLocal.length} local objects in view`}
            >
              {previewLoading ? 'Preparing…' : selectedLocal.length > 0 ? `Email owners of selected (${groupByOwner(selectedLocal).length})` : `Email owners in view (${groupByOwner(filteredLocal).length})`}
            </button>
          </div>
        </div>
        {emailError && <div className="mt-2 text-sm text-[var(--neon-red)]">{emailError}</div>}

        <div className="mt-4 flex flex-col gap-3 border-t border-[var(--border-glass)] pt-4 lg:flex-row lg:flex-wrap lg:items-end">
          <label className="flex flex-col gap-1 text-xs text-[var(--text-secondary)]">
            Migrate to config
            <select value={targetConfig} onChange={(e) => setTargetConfig(e.target.value)} disabled={configNames.length === 0} className={`${SELECT_CLASS} min-w-[12rem]`}>
              {configNames.map((name) => <option key={name} value={name}>{name}{configTypes[name] ? ` (${configTypes[name].toLowerCase()})` : ''}</option>)}
              {configNames.length === 0 && <option value="">No container execution configs</option>}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-[var(--text-secondary)]">
            Cluster {targetIsK8s ? '' : '(Kubernetes configs only)'}
            <select value={targetIsK8s ? clusterChoice : KEEP_CLUSTER} onChange={(e) => setClusterChoice(e.target.value)} disabled={!targetIsK8s || clusters.length === 0} className={`${SELECT_CLASS} min-w-[12rem]`}>
              <option value={KEEP_CLUSTER}>Keep each project's cluster</option>
              {clusters.map((c) => <option key={c.id} value={c.id}>{c.name !== c.id ? `${c.name} (${c.id})` : c.id}{c.state ? ` · ${c.state.toLowerCase()}` : ''}</option>)}
            </select>
          </label>
          <div className="flex flex-col gap-1 text-xs text-[var(--text-secondary)]">
            Strategy
            <div className="flex items-center gap-1 rounded-lg border border-[var(--border-glass)] p-1">
              {([
                ['objects', 'Pin each object', 'Write an explicit container selection on every selected object'],
                ['project-defaults', 'Project defaults', 'Set the project default for each selected workload family and let objects inherit (explicit NONE is reset to inherit)'],
              ] as [ComputeMigrationStrategy, string, string][]).map(([value, label, title]) => (
                <button
                  key={value}
                  type="button"
                  title={title}
                  onClick={() => setStrategy(value)}
                  className={`rounded px-2.5 py-1.5 text-xs ${strategy === value ? 'bg-[var(--bg-glass-hover)] text-[var(--text-primary)]' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2 lg:ml-auto">
            <button
              type="button"
              onClick={() => void runMigration(true)}
              disabled={migrateBusy || !targetConfig || selectedMigratable.length === 0}
              className={BTN_SECONDARY}
            >
              {migrateBusy ? 'Planning…' : `Plan (${selectedMigratable.length})`}
            </button>
            <button
              type="button"
              onClick={openConfirm}
              disabled={migrateBusy || !targetConfig || selectedMigratable.length === 0 || !unlocked}
              title={!unlocked ? 'Unlock Agentic Actions (top bar) to apply changes' : undefined}
              className="rounded bg-[var(--neon-red)] px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              Migrate {selectedMigratable.length > 0 ? selectedMigratable.length : ''}…
            </button>
          </div>
        </div>
        {!unlocked && selectedMigratable.length > 0 && (
          <div className="mt-2 text-xs text-[var(--text-muted)]">Planning is always available; applying needs Agentic Actions unlocked.</div>
        )}
        {migrateError && <div className="mt-3 text-sm text-[var(--neon-red)]">{migrateError}</div>}
        {plan && <div className="mt-3"><MigrationPlan result={plan} /></div>}
      </div>

      <div className="px-4">
        <DataGrid<ComputePlacementRow>
          rows={visible}
          columns={columns}
          rowKey={(row) => row.id}
          defaultSortColumnId="project"
          defaultSortDir="asc"
          filtersActive={placementFilter !== 'all' || configFilter !== '' || clusterFilter !== '' || query !== '' || typeFilter.size !== OBJECT_TYPES.length}
          emptyMessage={loading ? 'Scanning…' : 'No compute-using objects found.'}
          noMatchMessage="No rows match the current filters."
          countBadge={{ total: rows.length, filtered: filtered.length }}
          showRowCount
          rowClassName={(row) => (selectedKeys.has(row.id) ? 'bg-[var(--neon-cyan)]/5' : '')}
        />
        {filtered.length > visible.length && (
          <div className="mt-3 flex items-center justify-center gap-3 text-xs text-[var(--text-muted)]">
            Showing {visible.length.toLocaleString()} of {filtered.length.toLocaleString()}
            <button type="button" onClick={() => setPageLimit((n) => n + PAGE_SIZE)} className={BTN_SECONDARY}>Show more</button>
            <button type="button" onClick={() => setPageLimit(filtered.length)} className={BTN_SECONDARY}>Show all</button>
          </div>
        )}
      </div>

      <Modal isOpen={confirmModal.isOpen} onClose={confirmModal.close} title="Migrate compute placement">
        <div className="space-y-4">
          <div className="rounded-lg border border-[var(--neon-red)]/30 bg-[var(--neon-red)]/10 px-3 py-2 text-sm text-[var(--text-primary)]">
            This writes container settings on <span className="font-mono">{selectedMigratable.length}</span> selected object{selectedMigratable.length === 1 ? '' : 's'}
            {strategy === 'project-defaults' ? ' and their project defaults' : ''}, targeting <span className="font-mono">{targetConfig}</span>
            {clusterId ? <> and pins every touched project to cluster <span className="font-mono">{clusterId}</span></> : null}.
            Running jobs are not affected; the next run uses the new placement. Type <span className="font-mono">CONFIRM</span> to enable.
          </div>
          {plan?.dryRun && plan.matchedRows > 0 && (
            <div className="text-xs text-[var(--text-muted)]">Last plan: {plan.plannedOps} change{plan.plannedOps === 1 ? '' : 's'} across {plan.matchedRows} object{plan.matchedRows === 1 ? '' : 's'}.</div>
          )}
          <input
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder="CONFIRM"
            className="w-full rounded border border-[var(--border-glass)] bg-[var(--bg-elevated)] px-2 py-2 text-[var(--text-primary)]"
          />
          <div className="flex justify-end gap-2">
            <button type="button" onClick={confirmModal.close} className={BTN_SECONDARY}>Cancel</button>
            <button
              type="button"
              onClick={() => void runMigration(false)}
              disabled={confirmText !== 'CONFIRM' || migrateBusy}
              className="rounded bg-[var(--neon-red)] px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
            >
              {migrateBusy ? 'Applying…' : 'Apply migration'}
            </button>
          </div>
        </div>
      </Modal>

      <Modal isOpen={previewModal.isOpen} onClose={previewModal.close} title="Email owners of local-compute objects" sizePreset="large">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-[var(--text-secondary)]">
            <span>
              {sendResult
                ? `Sent ${sendResult.sentCount}/${sendResult.requestedCount} via ${sendResult.channelId}`
                : `${previewItems.length} email${previewItems.length === 1 ? '' : 's'} ready${selectedChannel ? ` · channel ${selectedChannel}` : ' · no mail channel configured'}`}
              {previewsWithoutEmail > 0 && (
                <span className="ml-2 text-xs text-[var(--neon-yellow)]" title={skippedOwners.join(', ')}>
                  {previewsWithoutEmail} owner{previewsWithoutEmail === 1 ? '' : 's'} skipped (no email on profile)
                </span>
              )}
            </span>
            <div className="flex gap-2">
              <button type="button" onClick={previewModal.close} className={BTN_SECONDARY}>Close</button>
              <button
                type="button"
                onClick={() => void sendEmails()}
                disabled={sendLoading || previewItems.length === 0 || Boolean(sendResult) || !unlocked}
                title={!unlocked ? 'Unlock Agentic Actions (top bar) to send' : undefined}
                className="rounded bg-[var(--accent)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
              >
                {sendLoading ? 'Sending…' : 'Send emails'}
              </button>
            </div>
          </div>
          {emailError && <div className="text-sm text-[var(--neon-red)]">{emailError}</div>}
          {sendResult?.results.filter((r) => r.status === 'error').map((r) => (
            <div key={r.recipientKey} className="text-xs text-[var(--neon-red)]">{r.to}: {r.error}</div>
          ))}
          <div className="max-h-[60vh] space-y-3 overflow-auto">
            {previewItems.map((item) => (
              <details key={item.recipientKey} className="rounded-lg border border-[var(--border-glass)] bg-[var(--bg-elevated)] p-3">
                <summary className="cursor-pointer text-sm text-[var(--text-primary)]">
                  <span className="font-medium">{item.owner}</span>
                  <span className="ml-2 font-mono text-xs text-[var(--text-muted)]">{item.to}</span>
                  <span className="ml-2 text-xs text-[var(--text-muted)]">{item.objectCount} object{item.objectCount === 1 ? '' : 's'} · {item.projectKeys.length} project{item.projectKeys.length === 1 ? '' : 's'}</span>
                </summary>
                <div className="mt-2 text-xs text-[var(--text-secondary)]">Subject: {item.subject}</div>
                <iframe title={`Email preview for ${item.owner}`} srcDoc={item.body} sandbox="" className="mt-2 h-96 w-full rounded border border-[var(--border-glass)] bg-white" />
              </details>
            ))}
          </div>
        </div>
      </Modal>
    </div>
  );
}
