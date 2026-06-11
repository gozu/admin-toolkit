import { useEffect, useMemo, useState } from 'react';
import { fetchJson } from '../utils/api';
import { useDiag } from '../context/DiagContext';
import { ProgressIndicator } from './common/ProgressIndicator';
import { k8sInsightsScan, setK8sScanClusterId } from '../state/k8sInsightsStore';
import { k8sClusterHealthStore } from '../state/k8sClusterHealthStore';
import { DataGrid } from './common/DataGrid';
import { K8sNodeDetail } from './K8sNodeDetail';
import { K8sFindingEvidence } from './K8sFindingEvidence';
import type { ColumnDef } from '../utils/dataGridTypes';
import type {
  K8sClusterHealth,
  K8sFinding,
  K8sInsightsClustersResult,
  K8sNodeBreakdown,
  K8sPricingStatus,
  K8sProbeStatus,
  K8sSeverity,
  K8sRemediation,
} from '../types';

const KUBECTL_PROBES = [
  'probe_pods',
  'probe_nodes',
  'probe_daemonsets',
  'probe_replicasets',
  'probe_deployments_all',
  'probe_deployments_kubesystem',
  'probe_pdbs',
  'probe_events',
  'probe_top_pods',
  'probe_top_nodes',
  'probe_kubectl_version',
];

const SEVERITY_ORDER: K8sSeverity[] = ['critical', 'high', 'medium', 'low', 'info'];

const SEVERITY_STYLES: Record<K8sSeverity, { dot: string; label: string; chip: string }> = {
  critical: { dot: 'bg-red-500', label: 'Critical', chip: 'bg-red-500/15 text-red-300 border-red-500/40' },
  high: { dot: 'bg-red-400', label: 'High', chip: 'bg-red-400/15 text-red-200 border-red-400/40' },
  medium: { dot: 'bg-yellow-400', label: 'Medium', chip: 'bg-yellow-400/15 text-yellow-200 border-yellow-400/40' },
  low: { dot: 'bg-white/70', label: 'Low', chip: 'bg-white/10 text-[var(--text-primary)] border-white/20' },
  info: { dot: 'bg-white/40', label: 'Info', chip: 'bg-white/5 text-[var(--text-muted)] border-white/10' },
};

function severityChip(s: K8sSeverity) {
  const st = SEVERITY_STYLES[s];
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-mono uppercase border ${st.chip}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${st.dot}`} />
      {st.label}
    </span>
  );
}

function formatUsd(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  if (value < 1) return `$${value.toFixed(2)}`;
  if (value < 100) return `$${value.toFixed(1)}`;
  return `$${Math.round(value).toLocaleString()}`;
}

function formatPct(value: number | null | undefined): string {
  if (value == null) return '—';
  return `${value.toFixed(0)}%`;
}

type UnavailableCluster = NonNullable<K8sInsightsClustersResult['unavailable']>[number];

const UNAVAILABLE_CLUSTER_COLUMNS: ColumnDef<UnavailableCluster>[] = [
  { id: 'id', label: 'id', defaultSortDir: 'asc', mono: true, render: (u) => u.id, sortValue: (u) => u.id },
  {
    id: 'state',
    label: 'state',
    defaultSortDir: 'asc',
    cellClassName: 'text-[var(--text-muted)]',
    render: (u) => u.state || '—',
    sortValue: (u) => u.state || '',
  },
  {
    id: 'type',
    label: 'type',
    defaultSortDir: 'asc',
    cellClassName: 'text-[var(--text-muted)]',
    render: (u) => u.type || '—',
    sortValue: (u) => u.type || '',
  },
  {
    id: 'kubeconfig',
    label: 'kubeconfig?',
    render: (u) =>
      u.hasKubeconfig ? (
        <span className="text-green-300">yes</span>
      ) : (
        <span className="text-[var(--text-muted)]">no</span>
      ),
    sortValue: (u) => (u.hasKubeconfig ? 1 : 0),
  },
  {
    id: 'dirFiles',
    label: 'files in cluster dir',
    defaultSortDir: 'asc',
    cellClassName: 'text-[var(--text-muted)]',
    render: (u) => (u.dirFiles || []).join(', ') || '—',
    sortValue: (u) => (u.dirFiles || []).join(', '),
  },
];

export function K8sInsights() {
  const { data, loading, scanMessage, error, scanStarted } = k8sInsightsScan.use();
  const health = k8sClusterHealthStore.use();
  const { addDebugLog } = useDiag();
  const [clusters, setClusters] = useState<K8sInsightsClustersResult | null>(null);
  const [clusterError, setClusterError] = useState<string | null>(null);
  const [selectedCluster, setSelectedCluster] = useState<string>('');
  const [expandedFinding, setExpandedFinding] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await fetchJson<K8sInsightsClustersResult>('/api/k8s-insights/clusters');
        if (cancelled) return;
        setClusters(result);
        if (result.clusters?.length && !selectedCluster) {
          setSelectedCluster(result.clusters[0].id);
        }
      } catch (err) {
        if (!cancelled) {
          setClusterError(err instanceof Error ? err.message : String(err));
        }
      }
    })();
    void k8sClusterHealthStore.load();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const healthById = useMemo(() => {
    const m = new Map<string, K8sClusterHealth>();
    for (const c of health.data?.clusters || []) m.set(c.id, c);
    return m;
  }, [health.data]);
  const selectedHealth = selectedCluster ? healthById.get(selectedCluster) : undefined;
  const auditBlockedReason = blockedAuditReason(selectedHealth);

  // Auto-run only when there's exactly one available cluster, so the common
  // single-cluster case stays one-click while multi-cluster picks are deliberate.
  useEffect(() => {
    if (!clusters || scanStarted || loading) return;
    if (clusters.clusters?.length === 1 && selectedCluster) {
      setK8sScanClusterId(selectedCluster);
      void k8sInsightsScan.load();
    }
  }, [clusters, scanStarted, loading, selectedCluster]);

  // Log scan completion details for debug diagnosis
  useEffect(() => {
    if (!data) return;
    const fs = data.findings || [];
    const bySev: Record<string, number> = {};
    for (const f of fs) bySev[f.severity] = (bySev[f.severity] ?? 0) + 1;
    const sevStr = `${bySev['critical'] ?? 0}C/${bySev['high'] ?? 0}H/${bySev['medium'] ?? 0}M/${bySev['low'] ?? 0}L`;
    // Mirrors savings logic in K8sOverviewCard
    const floorFinding = fs.find((f) => f.rule === 'cluster-floor-projection');
    const ev = (floorFinding?.evidence ?? {}) as { consolidationSavingsMonthly?: number; idleNodeSavingsMonthly?: number };
    const consolidation = ev.consolidationSavingsMonthly ?? floorFinding?.costImpactPerMonth ?? 0;
    const idleNodes = ev.idleNodeSavingsMonthly ?? 0;
    const gpuMap = new Map<string, number>();
    for (const f of fs) {
      if (f.rule !== 'gpu-pod-not-using-gpu' || (f.costImpactPerMonth ?? 0) <= 0) continue;
      const node = (f.evidence as { node?: string }).node || f.id;
      gpuMap.set(node, Math.max(gpuMap.get(node) ?? 0, f.costImpactPerMonth!));
    }
    const total = consolidation + idleNodes + [...gpuMap.values()].reduce((a, b) => a + b, 0);
    const ps = data.pricingStatus;
    const savingsNote = ps?.ok === false
      ? `savings unavailable (pricing: ${ps.error || 'unknown'})`
      : total > 0
        ? `savings $${Math.round(total)}/mo`
        : `savings — (no cluster-floor-projection finding or total=0; idle-pod/memory findings don't contribute)`;
    addDebugLog(`K8S Insights: ${data.findingsCount} finding(s) ${sevStr} | ${savingsNote}`, 'lifecycle');
  // addDebugLog identity is stable; data is the scan result ref
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  const runScan = () => {
    if (!selectedCluster || loading) return;
    setK8sScanClusterId(selectedCluster);
    void k8sInsightsScan.load(true);
  };
  const abortScan = () => {
    k8sInsightsScan.abort();
  };

  const findings = useMemo<K8sFinding[]>(() => data?.findings || [], [data?.findings]);
  const groupedFindings = useMemo(() => {
    const groups = new Map<K8sSeverity, K8sFinding[]>();
    for (const s of SEVERITY_ORDER) groups.set(s, []);
    for (const f of findings) {
      const list = groups.get(f.severity);
      if (list) list.push(f);
    }
    return groups;
  }, [findings]);

  const lifecycle = k8sInsightsScan.lifecycle();

  if (clusterError) {
    return (
      <div className="w-full py-8 px-4">
        <div className="glass-card p-4 border-red-500/40 text-red-300">
          Failed to enumerate DSS-managed clusters: {clusterError}
        </div>
      </div>
    );
  }

  if (clusters && clusters.clusters && clusters.clusters.length === 0) {
    const unavailable = clusters.unavailable || [];
    return (
      <div className="w-full py-8 px-4">
        <div className="glass-card p-4">
          <h3 className="text-lg font-semibold mb-1">No active clusters to audit</h3>
          <p className="text-sm text-[var(--text-muted)]">
            A cluster is "active" when it has a kubeconfig on disk or is in state{' '}
            <span className="font-mono">RUNNING</span>. Start a managed cluster in DSS, or
            attach an EKS cluster, then refresh this page.
          </p>
          {unavailable.length > 0 && (
            <details className="mt-3 text-xs">
              <summary className="cursor-pointer text-[var(--text-muted)] font-mono">
                {unavailable.length} known cluster{unavailable.length === 1 ? '' : 's'} excluded
              </summary>
              <div className="mt-2">
                <DataGrid
                  rows={unavailable}
                  columns={UNAVAILABLE_CLUSTER_COLUMNS}
                  rowKey={(u) => u.id}
                />
              </div>
            </details>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="w-full py-4 space-y-4 px-4">
      <div className="glass-card p-3 space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <K8sClusterPicker
            clusters={clusters?.clusters || []}
            healthById={healthById}
            selected={selectedCluster}
            onChange={setSelectedCluster}
            disabled={loading}
          />
          <div className="ml-auto flex items-center gap-2">
            {loading ? (
              <button
                type="button"
                onClick={abortScan}
                className="px-3 py-1 text-xs font-mono rounded border border-red-500/40 text-red-300 hover:bg-red-500/10"
              >
                Abort
              </button>
            ) : (
              <button
                type="button"
                onClick={runScan}
                disabled={!selectedCluster || !!auditBlockedReason}
                title={auditBlockedReason || undefined}
                className="px-3 py-1 text-xs font-mono rounded border border-white/15 hover:bg-white/10 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {data || error ? 'Run again' : 'Run audit'}
              </button>
            )}
          </div>
        </div>
        {selectedHealth && !selectedHealth.ok && (
          <div className="text-xs font-mono text-[var(--text-muted)] border-l-2 border-red-500/40 pl-2 py-1">
            <span className="text-red-300 uppercase">{selectedHealth.errorClass}</span>{' '}
            <span className="text-[var(--text-secondary)]">{selectedHealth.errorSummary || 'unreachable'}</span>
            {auditBlockedReason && (
              <div className="text-[var(--text-muted)] mt-0.5">{auditBlockedReason}</div>
            )}
          </div>
        )}
      </div>

      {clusters && clusters.totalDiscovered != null && clusters.totalDiscovered > (clusters.clusters?.length || 0) && (
        <div className="text-[11px] text-[var(--text-muted)] font-mono px-1">
          Showing {clusters.clusters.length} active of {clusters.totalDiscovered} cluster
          {clusters.totalDiscovered === 1 ? '' : 's'} known to DSS — stopped or orphan dirs hidden.
        </div>
      )}

      {error && (
        <div className="glass-card p-3 border border-red-500/40 text-red-300 text-sm">
          {error}
        </div>
      )}

      {loading && (
        <div className="glass-card p-3">
          <ProgressIndicator lifecycle={lifecycle} />
          <div className="text-xs text-[var(--text-muted)] mt-1 font-mono">{scanMessage}</div>
        </div>
      )}

      {!loading && !data && !error && (
        <div className="glass-card p-4 text-sm text-[var(--text-muted)]">
          {selectedCluster
            ? <>Click <span className="font-mono">Run audit</span> to scan <span className="font-mono text-[var(--text-primary)]">{selectedCluster}</span>.</>
            : 'Select a cluster to begin.'}
        </div>
      )}

      {data?.ok && (
        <>
          <K8sProbeDiagBanner cluster={data.cluster} probes={data.probes || {}} />
          <K8sPricingStatusBanner status={data.pricingStatus} />
          <K8sOverviewCard data={data} />
          <K8sNodeTable nodes={data.nodeBreakdown || []} clusterId={data.cluster?.id || ''} />
          <K8sFindingsList
            grouped={groupedFindings}
            expanded={expandedFinding}
            onToggle={(id) => setExpandedFinding(expandedFinding === id ? null : id)}
          />
          <K8sProbeStatus probes={data.probes || {}} />
        </>
      )}

      {data && !data.ok && (
        <div className="glass-card p-4 border border-red-500/40">
          <h4 className="font-semibold text-red-300 mb-1">Audit failed</h4>
          <p className="text-sm font-mono">{data.error || 'unknown error'}</p>
        </div>
      )}
    </div>
  );
}

const HEALTH_DOT_BG: Record<NonNullable<K8sClusterHealth['errorClass']> | 'ok' | 'pending', string> = {
  ok: 'bg-emerald-400',
  dns: 'bg-[var(--neon-red)]',
  network: 'bg-[var(--neon-red)]',
  unknown: 'bg-[var(--neon-red)]',
  auth: 'bg-yellow-400',
  tls: 'bg-yellow-400',
  pending: 'bg-white/20',
};

const HEALTH_LABEL: Record<NonNullable<K8sClusterHealth['errorClass']>, string> = {
  dns: 'DNS',
  network: 'unreachable',
  auth: 'auth',
  tls: 'tls',
  unknown: 'error',
};

function healthTone(h: K8sClusterHealth | undefined): keyof typeof HEALTH_DOT_BG {
  if (!h) return 'pending';
  if (h.ok) return 'ok';
  return (h.errorClass || 'unknown') as keyof typeof HEALTH_DOT_BG;
}

function blockedAuditReason(h: K8sClusterHealth | undefined): string | null {
  // Allow audits on auth/tls failures — they're informative and the audit
  // will surface them as findings. Block on dns/network/unknown since the
  // probes can't talk to the cluster at all.
  if (!h || h.ok) return null;
  if (h.errorClass === 'auth' || h.errorClass === 'tls') return null;
  return `Cluster is unreachable (${h.errorClass || 'error'}) — kubectl probes will fail.`;
}

function K8sClusterPicker({
  clusters,
  healthById,
  selected,
  onChange,
  disabled,
}: {
  clusters: K8sInsightsClustersResult['clusters'];
  healthById: Map<string, K8sClusterHealth>;
  selected: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}) {
  if (!clusters.length) {
    return (
      <div className="text-xs text-[var(--text-muted)] font-mono">No active clusters available.</div>
    );
  }
  return (
    <div className="flex items-center gap-2 text-sm">
      <label className="text-[var(--text-muted)]">Cluster:</label>
      <div className="relative">
        <select
          value={selected}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          className="bg-[var(--bg-elevated)] border border-white/10 rounded-md pl-6 pr-2 py-1 font-mono text-xs disabled:opacity-50 appearance-none"
        >
          {clusters.map((c) => {
            const h = healthById.get(c.id);
            const state = c.state && c.state !== 'NONE' ? ` · ${c.state.toLowerCase()}` : '';
            const cls = h && !h.ok && h.errorClass ? ` · ${HEALTH_LABEL[h.errorClass]}` : '';
            return (
              <option key={c.id} value={c.id}>
                {c.id}{state}{cls}
              </option>
            );
          })}
        </select>
        {/* Coloured dot overlay — <option> can't be styled per-option cross-browser */}
        <span
          aria-hidden
          className={`absolute left-2 top-1/2 -translate-y-1/2 w-2 h-2 rounded-full ${HEALTH_DOT_BG[healthTone(healthById.get(selected))]}`}
        />
      </div>
    </div>
  );
}

function K8sSavingsRow({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex justify-between">
      <span className="text-[var(--text-muted)]">{label}</span>
      <span>{formatUsd(value)}/mo</span>
    </div>
  );
}

function K8sOverviewCard({ data }: { data: NonNullable<ReturnType<typeof k8sInsightsScan.use>['data']> }) {
  const cost = data.costSnapshot || { currentHourly: null, currentMonthly: null, nodes: [] };
  const pricingOk = data.pricingStatus?.ok !== false;
  const floorFinding = (data.findings || []).find((f) => f.rule === 'cluster-floor-projection');
  // The backend floor rule now splits its savings into workload consolidation vs
  // idle/empty-node reclaim and emits both as evidence; the frontend just reads them.
  const ev = (floorFinding?.evidence ?? {}) as {
    consolidationSavingsMonthly?: number;
    idleNodeSavingsMonthly?: number;
  };
  const consolidation = ev.consolidationSavingsMonthly ?? floorFinding?.costImpactPerMonth ?? 0;
  const idleNodes = ev.idleNodeSavingsMonthly ?? 0;
  // Idle GPU pods hold a GPU node the bin-pack floor must keep (the pod requests a
  // GPU), so their recoverable savings are additive. De-dup per node.
  const gpuWasteByNode = new Map<string, number>();
  for (const f of data.findings || []) {
    if (f.rule !== 'gpu-pod-not-using-gpu' || (f.costImpactPerMonth ?? 0) <= 0) continue;
    const node = (f.evidence as { node?: string }).node || f.id;
    gpuWasteByNode.set(node, Math.max(gpuWasteByNode.get(node) ?? 0, f.costImpactPerMonth!));
  }
  const gpuWaste = [...gpuWasteByNode.values()].reduce((a, b) => a + b, 0);
  const total = consolidation + idleNodes + gpuWaste;
  const savingsMonthly = total > 0 ? total : null;
  const savingsPct =
    savingsMonthly != null && cost.currentMonthly
      ? Math.round((savingsMonthly / cost.currentMonthly) * 100)
      : null;

  return (
    <div className="glass-card p-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div>
          <div className="text-xs uppercase text-[var(--text-muted)] tracking-wider">Monthly cost</div>
          <div className="text-2xl font-mono">
            {cost.currentMonthly != null ? `${formatUsd(cost.currentMonthly)}/mo` : '—'}
          </div>
          <div className="text-xs text-[var(--text-muted)] font-mono">
            {cost.currentHourly != null ? `${formatUsd(cost.currentHourly)} / hr` : ''}
          </div>
        </div>
        <div>
          <div className="text-xs uppercase text-[var(--text-muted)] tracking-wider">Nodes</div>
          <div className="text-2xl font-mono">{data.cluster.nodeCount ?? '—'}</div>
        </div>
        <div>
          <div className="text-xs uppercase text-[var(--text-muted)] tracking-wider">Pods</div>
          <div className="text-2xl font-mono">{data.cluster.podCount ?? '—'}</div>
        </div>
        {pricingOk && (
          <div>
            <div className="text-xs uppercase text-[var(--text-muted)] tracking-wider">
              Potential savings
            </div>
            <div className="text-2xl font-mono text-green-300">
              {savingsMonthly != null ? `${formatUsd(savingsMonthly)}/mo` : '—'}
            </div>
            <div className="text-xs text-[var(--text-muted)] font-mono">
              {savingsPct != null ? `~${savingsPct}% of spend` : ''}
            </div>
          </div>
        )}
      </div>
      {pricingOk && savingsMonthly != null && savingsMonthly > 0 && (
        <div className="mt-4 pt-3 border-t border-white/10">
          <div className="text-xs uppercase text-[var(--text-muted)] tracking-wider mb-2">
            Potential savings
          </div>
          <div className="space-y-1 max-w-xs font-mono text-sm">
            <K8sSavingsRow label="Consolidate nodes (bin-pack)" value={consolidation} />
            {idleNodes > 0 && <K8sSavingsRow label="Reclaim idle / empty nodes" value={idleNodes} />}
            {gpuWaste > 0 && <K8sSavingsRow label="Free GPU from idle pods" value={gpuWaste} />}
            <div className="flex justify-between border-t border-white/10 pt-1 mt-1 text-green-300">
              <span>Total</span>
              <span>{formatUsd(savingsMonthly)}/mo</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function K8sPricingStatusBanner({ status }: { status?: K8sPricingStatus }) {
  if (!status || status.ok) return null;
  return (
    <div className="glass-card p-3 border border-red-500/40 bg-red-500/5 text-sm">
      <div className="font-semibold text-red-300">Cost analysis unavailable</div>
      <div className="text-xs text-[var(--text-muted)] mt-0.5 font-mono">
        pricing source <span className="text-[var(--text-primary)]">{status.source}</span>
        {status.region ? <> · region <span className="text-[var(--text-primary)]">{status.region}</span></> : null}
        {' — '}
        <span className="text-red-200">{status.error || 'unknown error'}</span>
      </div>
      <div className="text-xs text-[var(--text-muted)] mt-1">
        Hourly figures are suppressed. Scheduling, lifecycle, and DSS-drift findings are unaffected.
      </div>
    </div>
  );
}

function K8sFindingsList({
  grouped,
  expanded,
  onToggle,
}: {
  grouped: Map<K8sSeverity, K8sFinding[]>;
  expanded: string | null;
  onToggle: (id: string) => void;
}) {
  const totalCount = SEVERITY_ORDER.reduce((sum, s) => sum + (grouped.get(s)?.length || 0), 0);
  if (totalCount === 0) {
    return (
      <div className="glass-card p-4">
        <h4 className="font-semibold">Findings</h4>
        <p className="text-sm text-[var(--text-muted)] mt-1">No findings — cluster looks clean.</p>
      </div>
    );
  }
  return (
    <div className="glass-card">
      <div className="px-4 py-3 border-b border-white/10 flex items-center gap-3">
        <h4 className="font-semibold">Findings</h4>
        <div className="flex gap-2 ml-auto">
          {SEVERITY_ORDER.map((s) => {
            const n = grouped.get(s)?.length || 0;
            if (!n) return null;
            return (
              <span key={s} className={`text-xs font-mono ${SEVERITY_STYLES[s].chip} border px-2 py-0.5 rounded`}>
                {n} {SEVERITY_STYLES[s].label}
              </span>
            );
          })}
        </div>
      </div>
      <div className="divide-y divide-white/5">
        {SEVERITY_ORDER.flatMap((s) => grouped.get(s) || []).map((f) => (
          <K8sFindingRow
            key={f.id}
            finding={f}
            expanded={expanded === f.id}
            onToggle={() => onToggle(f.id)}
          />
        ))}
      </div>
    </div>
  );
}

// Rules whose per-finding savings are already inside the bin-pack floor
// (cluster-floor-projection). They keep firing as findings but suppress the green
// $/mo badge so the list's badges don't double-count the floor / exceed the bill.
const FLOOR_SUBSUMED_RULES = new Set([
  'node-over-provisioned',
  'node-locked-by-single-pod',
  'gpu-node-idle',
  'cpu-pod-on-gpu-node',
]);

function K8sFindingRow({
  finding,
  expanded,
  onToggle,
}: {
  finding: K8sFinding;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="px-4 py-3 hover:bg-white/5 transition">
      <button
        type="button"
        onClick={onToggle}
        className="w-full text-left flex items-start gap-3"
      >
        <div className="pt-0.5">{severityChip(finding.severity)}</div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-[var(--text-primary)]">{finding.title}</span>
            {finding.costImpactPerMonth != null &&
              finding.costImpactPerMonth > 0 &&
              !FLOOR_SUBSUMED_RULES.has(finding.rule) && (
                <span className="text-xs font-mono text-green-300">
                  ~{formatUsd(finding.costImpactPerMonth)}/mo
                </span>
              )}
          </div>
          <div className="text-xs text-[var(--text-muted)] font-mono mt-0.5">
            {finding.rule}{' '}
            <span className="opacity-60">· {finding.category}</span>
          </div>
        </div>
        <span className="text-xs text-[var(--text-muted)] font-mono">{expanded ? '▾' : '▸'}</span>
      </button>
      {expanded && (
        <div className="mt-3 pl-8 space-y-3">
          <p className="text-sm text-[var(--text-secondary)] whitespace-pre-wrap">{finding.summary}</p>
          {finding.remediation.length > 0 && (
            <div>
              <div className="text-xs uppercase text-[var(--text-muted)] tracking-wider mb-1">
                Remediation
              </div>
              <ol className="space-y-2">
                {finding.remediation.map((r, idx) => (
                  <li key={idx}>
                    <K8sRemediationStep step={r} />
                  </li>
                ))}
              </ol>
            </div>
          )}
          {finding.evidence && Object.keys(finding.evidence).length > 0 && (
            <div>
              <div className="text-xs uppercase text-[var(--text-muted)] tracking-wider mb-1">
                Evidence
              </div>
              <K8sFindingEvidence evidence={finding.evidence as Record<string, unknown>} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function K8sRemediationStep({ step }: { step: K8sRemediation }) {
  const kindLabel: Record<K8sRemediation['kind'], string> = {
    kubectl: 'kubectl',
    'file-edit': 'edit',
    'gui-step': 'gui',
    'doc-link': 'doc',
  };
  return (
    <div>
      <div className="flex items-center gap-2 text-xs">
        <span className="font-mono uppercase text-[var(--text-muted)] border border-white/10 rounded px-1.5">
          {kindLabel[step.kind] || step.kind}
        </span>
        <span className="font-medium">{step.title}</span>
      </div>
      {step.body && step.kind !== 'doc-link' && (
        <pre className="mt-1 p-2 bg-black/30 border border-white/5 rounded text-xs overflow-x-auto font-mono whitespace-pre-wrap">
          {step.body}
        </pre>
      )}
      {step.target && step.kind === 'doc-link' && (
        <a
          href={step.target}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-[var(--neon-cyan)] hover:underline"
        >
          {step.target}
        </a>
      )}
    </div>
  );
}

function K8sNodeTable({ nodes, clusterId }: { nodes: K8sNodeBreakdown[]; clusterId: string }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const toggle = (name: string) => setExpanded((cur) => (cur === name ? null : name));
  const columns = useMemo<ColumnDef<K8sNodeBreakdown>[]>(
    () => [
      {
        id: 'expand',
        label: '',
        render: (row) => {
          const isOpen = expanded === row.name;
          return (
            <button
              type="button"
              onClick={() => toggle(row.name)}
              className="inline-flex items-center justify-center w-5 h-5 rounded border border-white/10 bg-white/5 text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-white/10 hover:border-white/20 transition-colors"
              aria-label={isOpen ? 'Collapse details' : 'Expand details'}
              title={isOpen ? 'Collapse details' : 'Expand details'}
            >
              <svg
                width="12"
                height="12"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2.5}
                strokeLinecap="round"
                strokeLinejoin="round"
                className={`transition-transform ${isOpen ? 'rotate-90' : ''}`}
              >
                <polyline points="9 6 15 12 9 18" />
              </svg>
            </button>
          );
        },
      },
      {
        id: 'name',
        label: 'Node',
        defaultSortDir: 'asc',
        render: (row) => (
          <button
            type="button"
            onClick={() => toggle(row.name)}
            title={expanded === row.name ? 'Collapse details' : 'Click to expand details'}
            className="font-mono text-xs text-left text-[var(--text-primary)] hover:text-[var(--neon-cyan)] hover:underline underline-offset-2 decoration-dotted decoration-[var(--text-muted)] cursor-pointer"
          >
            {row.name}
          </button>
        ),
        sortValue: (row) => row.name,
      },
      {
        id: 'instanceType',
        label: 'Instance',
        defaultSortDir: 'asc',
        render: (row) => (
          <span className="font-mono text-xs">
            {row.instanceType}
            {row.isGpu && (
              <span className="ml-1 text-[10px] uppercase text-yellow-300 border border-yellow-500/40 rounded px-1">
                gpu
              </span>
            )}
          </span>
        ),
        sortValue: (row) => row.instanceType,
      },
      {
        id: 'ready',
        label: 'Ready',
        render: (row) => (
          <span className={row.ready ? 'text-green-300' : 'text-red-300'}>
            {row.ready ? '✓' : '✗'}
          </span>
        ),
        sortValue: (row) => (row.ready ? 1 : 0),
      },
      {
        id: 'podCount',
        label: 'Pods',
        render: (row) => (
          <span className="font-mono text-xs">
            {row.podCount}{' '}
            <span className="text-[var(--text-muted)]">({row.userPodCount} user)</span>
          </span>
        ),
        sortValue: (row) => row.podCount,
      },
      {
        id: 'cpu',
        label: 'CPU',
        render: (row) => (
          <span className="font-mono text-xs">
            {formatPct(row.cpuPct)} <span className="text-[var(--text-muted)]">of {row.allocatableCpu || '—'}</span>
          </span>
        ),
        sortValue: (row) => row.cpuPct ?? -1,
      },
      {
        id: 'mem',
        label: 'Memory',
        render: (row) => (
          <span className="font-mono text-xs">
            {formatPct(row.memPct)} <span className="text-[var(--text-muted)]">of {row.allocatableMemory || '—'}</span>
          </span>
        ),
        sortValue: (row) => row.memPct ?? -1,
      },
      {
        id: 'hourly',
        label: '$/hr',
        render: (row) => <span className="font-mono text-xs">{formatUsd(row.hourly)}</span>,
        sortValue: (row) => row.hourly ?? -1,
      },
    ],
    [expanded],
  );

  if (!nodes.length) return null;
  const expandedNode = expanded ? nodes.find((n) => n.name === expanded) : null;
  return (
    <div className="glass-card">
      <div className="px-4 py-3 border-b border-white/10">
        <h4 className="font-semibold">Nodes</h4>
      </div>
      <DataGrid rows={nodes} columns={columns} rowKey={(r) => r.name} />
      {expandedNode && (
        <div className="p-3 border-t border-white/5">
          <K8sNodeDetail node={expandedNode} clusterId={clusterId} />
        </div>
      )}
    </div>
  );
}

function K8sProbeDiagBanner({
  cluster,
  probes,
}: {
  cluster: { nodeCount?: number | null };
  probes: Record<string, K8sProbeStatus>;
}) {
  const failedKubectl = KUBECTL_PROBES
    .map((n) => [n, probes[n]] as const)
    .filter((entry): entry is readonly [string, K8sProbeStatus] => !!entry[1] && !entry[1].ok);
  const showBanner = cluster.nodeCount == null && failedKubectl.length > 0;
  if (!showBanner) return null;
  const head = failedKubectl.slice(0, 3);
  const rest = failedKubectl.length - head.length;
  return (
    <div className="glass-card p-4 border border-yellow-500/40 bg-yellow-500/5 space-y-3">
      <div>
        <div className="font-semibold text-yellow-300">
          Audit returned no cluster data — every kubectl probe failed
        </div>
        <div className="text-xs text-[var(--text-muted)] mt-0.5">
          Filesystem-only findings (if any) may still be valid. Cluster overview, nodes,
          pods, and cost are unavailable until kubectl can reach the API server.
        </div>
      </div>
      <ul className="space-y-2 text-xs">
        {head.map(([name, p]) => (
          <li key={name} className="border-l-2 border-yellow-500/40 pl-2">
            <div className="font-mono text-[var(--text-primary)]">
              {name}
              {p.rc != null && (
                <span className="text-[var(--text-muted)]"> · rc={p.rc}</span>
              )}
            </div>
            {p.error && (
              <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap font-mono text-red-200 text-[11px]">
                {p.error}
              </pre>
            )}
          </li>
        ))}
        {rest > 0 && (
          <li className="text-[var(--text-muted)] font-mono">
            …and {rest} more failed kubectl probe{rest === 1 ? '' : 's'} (expand below for detail).
          </li>
        )}
      </ul>
    </div>
  );
}

function probeLabel(name: string): string {
  // probe_dss_general_settings → dss general settings
  return name.replace(/^probe_/, '').replace(/_/g, ' ');
}

function K8sProbeStatus({ probes }: { probes: Record<string, K8sProbeStatus> }) {
  const entries = Object.entries(probes);
  if (!entries.length) return null;
  const failed = entries.filter(([, p]) => !p.ok);
  const ok = entries.filter(([, p]) => p.ok);
  return (
    <div className="glass-card">
      <div className="px-4 py-3 border-b border-white/10 flex items-center gap-3">
        <h4 className="font-semibold">Probes</h4>
        <div className="ml-auto flex items-center gap-2 text-xs font-mono">
          {failed.length > 0 && (
            <span className="px-2 py-0.5 rounded border border-red-500/40 bg-red-500/10 text-red-300">
              {failed.length} failed
            </span>
          )}
          <span className="px-2 py-0.5 rounded border border-white/10 text-[var(--text-muted)]">
            {ok.length} ok
          </span>
        </div>
      </div>
      {failed.length > 0 && (
        <details open className="border-b border-white/5">
          <summary className="px-4 py-2 cursor-pointer text-xs font-mono uppercase tracking-wider text-red-300">
            Failed ({failed.length})
          </summary>
          <div className="divide-y divide-white/5">
            {failed.map(([name, p]) => (
              <K8sProbeRow key={name} name={name} probe={p} />
            ))}
          </div>
        </details>
      )}
      {ok.length > 0 && (
        <details>
          <summary className="px-4 py-2 cursor-pointer text-xs font-mono uppercase tracking-wider text-[var(--text-muted)]">
            OK ({ok.length})
          </summary>
          <div className="divide-y divide-white/5">
            {ok.map(([name, p]) => (
              <K8sProbeRow key={name} name={name} probe={p} />
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function K8sProbeRow({ name, probe }: { name: string; probe: K8sProbeStatus }) {
  return (
    <div className="px-4 py-2 text-xs">
      <div className="flex items-center gap-2">
        <span className={`w-2 h-2 rounded-full shrink-0 ${probe.ok ? 'bg-green-400' : 'bg-red-400'}`} />
        <span className="font-mono text-[var(--text-primary)]">{probeLabel(name)}</span>
        <span className="text-[var(--text-muted)] font-mono">
          {probe.durationMs}ms
          {probe.itemCount != null && <> · {probe.itemCount} item{probe.itemCount === 1 ? '' : 's'}</>}
          {probe.rc != null && <> · rc={probe.rc}</>}
        </span>
      </div>
      {!probe.ok && probe.error && (
        <div className="mt-1 ml-4 font-mono text-red-300 text-[11px] whitespace-pre-wrap max-h-32 overflow-auto">
          {probe.error}
        </div>
      )}
      <details className="mt-1 ml-4">
        <summary className="cursor-pointer text-[var(--text-muted)] font-mono text-[11px]">
          Raw output
        </summary>
        <div className="mt-1 space-y-2">
          <div>
            <div className="text-[var(--text-muted)] font-mono text-[10px] uppercase tracking-wider">
              stdout {probe.stdoutHead ? `(${probe.stdoutHead.length} chars)` : ''}
            </div>
            <pre className="mt-0.5 max-h-40 overflow-auto whitespace-pre-wrap p-2 bg-black/30 border border-white/5 rounded text-[10px] font-mono">
              {probe.stdoutHead || <span className="text-[var(--text-muted)]">no output</span>}
            </pre>
          </div>
          <div>
            <div className="text-[var(--text-muted)] font-mono text-[10px] uppercase tracking-wider">
              stderr {probe.stderrFull ? `(${probe.stderrFull.length} chars)` : ''}
            </div>
            <pre className="mt-0.5 max-h-40 overflow-auto whitespace-pre-wrap p-2 bg-black/30 border border-white/5 rounded text-[10px] font-mono">
              {probe.stderrFull || <span className="text-[var(--text-muted)]">no output</span>}
            </pre>
          </div>
        </div>
      </details>
    </div>
  );
}
