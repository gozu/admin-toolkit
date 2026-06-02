import { useState, useEffect, useCallback, useRef } from 'react';
import { motion } from 'framer-motion';
import { useDiag } from '../../context/DiagContext';
import { fetchJson } from '../../utils/api';
import type {
  CompareManifest,
  CompareDatasetDetail,
  CompareScalarField,
  CompareSummaryDelta,
} from '../../types';

/* ================================================================
   TYPES
   ================================================================ */

interface TrendsRun {
  run_id: number;
  run_at: string;
  health_score: number | null;
  user_count: number | null;
  project_count: number | null;
}

/* ================================================================
   HELPERS
   ================================================================ */

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch { return iso; }
}

function fmtNum(v: unknown): string {
  if (v === null || v === undefined || v === '') return '--';
  if (typeof v === 'number') return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return String(v);
}

function fmtBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || bytes === 0) return '--';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i > 1 ? 1 : 0)} ${units[i]}`;
}

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '--';
  return `${v.toFixed(1)}%`;
}

/** Parse a scalar field value to number, returning null (not 0) for missing data */
function toNum(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function fmtMB(mb: number | null): string {
  if (mb === null) return '--';
  return `${(mb / 1024).toFixed(1)} GB`;
}

function deltaColor(delta: number | null | undefined): string {
  if (!delta || delta === 0) return 'var(--text-muted)';
  return delta > 0 ? 'var(--neon-green)' : 'var(--neon-red)';
}

function deltaSign(delta: number | null | undefined): string {
  if (!delta || delta === 0) return '';
  return delta > 0 ? '+' : '';
}

/** Safe JSON parse for scalar field values that come as strings from the compare API */
function safeJsonParse(val: unknown): unknown {
  if (typeof val === 'string') {
    try { return JSON.parse(val); } catch { return val; }
  }
  return val;
}

/* ================================================================
   URL STATE
   ================================================================ */

function useUrlState() {
  const getParam = (key: string) => {
    try { return new URLSearchParams(window.location.search).get(key); } catch { return null; }
  };
  const setParams = useCallback((updates: Record<string, string | null>) => {
    try {
      const sp = new URLSearchParams(window.location.search);
      for (const [k, v] of Object.entries(updates)) {
        if (v === null) sp.delete(k); else sp.set(k, v);
      }
      const qs = sp.toString();
      window.history.replaceState(null, '', qs ? `?${qs}` : window.location.pathname);
    } catch { /* iframe restrictions */ }
  }, []);
  return { getParam, setParams };
}

/* ================================================================
   DATA HOOKS
   ================================================================ */

function useTrendsCompare() {
  const { addDebugLog } = useDiag();
  const { getParam, setParams } = useUrlState();

  const [runs, setRuns] = useState<TrendsRun[]>([]);
  const [run1Id, setRun1Id] = useState<number | null>(() => {
    const p = getParam('run1');
    return p ? Number(p) : null;
  });
  const [run2Id, setRun2Id] = useState<number | null>(() => {
    const p = getParam('run2');
    return p ? Number(p) : null;
  });
  const [manifest, setManifest] = useState<CompareManifest | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let c = false;
    fetchJson<{ runs: TrendsRun[] }>('/api/tracking/runs?limit=100')
      .then((data) => {
        if (c) return;
        const list = data.runs ?? [];
        setRuns(list);
        if (list.length > 0 && !run1Id) setRun1Id(list[0].run_id);
        if (list.length > 1 && !run2Id) setRun2Id(list[list.length - 1].run_id);
        if (list.length <= 1) setLoading(false);
        addDebugLog(`[trends] Fetched ${list.length} runs`, 'trends');
      })
      .catch((err) => {
        if (!c) { setError(err instanceof Error ? err.message : String(err)); setLoading(false); }
      });
    return () => { c = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!run1Id || !run2Id) return;
    let c = false;
    setLoading(true);
    setError(null);
    setParams({ run1: String(run1Id), run2: String(run2Id) });
    fetchJson<CompareManifest>(`/api/tracking/compare/full?run1=${run1Id}&run2=${run2Id}`)
      .then((data) => {
        if (c) return;
        if (data.swapped) { setRun1Id(data.run1.run_id); setRun2Id(data.run2.run_id); }
        setManifest(data);
      })
      .catch((err) => { if (!c) setError(err instanceof Error ? err.message : String(err)); })
      .finally(() => { if (!c) setLoading(false); });
    return () => { c = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run1Id, run2Id]);

  return { runs, run1Id, run2Id, manifest, loading, error, selectRun1: setRun1Id, selectRun2: setRun2Id };
}

function useDatasetDetail(run1Id: number | null, run2Id: number | null) {
  const [cache, setCache] = useState<Record<string, CompareDatasetDetail>>({});
  const [loadingSet, setLoadingSet] = useState<Set<string>>(new Set());

  const load = useCallback((datasetId: string, changeType: string = 'all', page: number = 1) => {
    if (!run1Id || !run2Id) return;
    setLoadingSet((s) => new Set(s).add(datasetId));
    const params = new URLSearchParams({
      run1: String(run1Id), run2: String(run2Id), dataset: datasetId,
      change_type: changeType, page: String(page), page_size: '500',
    });
    fetchJson<CompareDatasetDetail>(`/api/tracking/compare/full/dataset?${params}`)
      .then((data) => { setCache((c) => ({ ...c, [datasetId]: data })); })
      .finally(() => { setLoadingSet((s) => { const n = new Set(s); n.delete(datasetId); return n; }); });
  }, [run1Id, run2Id]);

  return { cache, loadingSet, load };
}

/* ================================================================
   REUSABLE UI PIECES
   ================================================================ */

function SectionCard({ title, children, subtitle, badge }: {
  title: string; children: React.ReactNode; subtitle?: string; badge?: React.ReactNode;
}) {
  return (
    <motion.div
      className="glass-card overflow-hidden"
      initial={{ opacity: 0, y: 10 }} whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '-40px' }} transition={{ duration: 0.3 }}
    >
      <div className="px-4 py-3 border-b border-[var(--border-glass)] flex items-center gap-3">
        <h3 className="text-sm font-semibold text-[var(--text-primary)]">{title}</h3>
        {badge}
        <span className="flex-1" />
        {subtitle && <span className="text-xs text-[var(--text-muted)]">{subtitle}</span>}
      </div>
      <div className="px-4 py-4">{children}</div>
    </motion.div>
  );
}

function SectionDivider({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 pt-6 pb-2">
      <h2 className="text-base font-semibold text-[var(--text-secondary)] tracking-wide uppercase">{label}</h2>
      <div className="flex-1 h-px bg-[var(--border-default)]" />
    </div>
  );
}

function ChangeBadge({ delta, suffix }: { delta: number | null | undefined; suffix?: string }) {
  if (!delta || delta === 0) return null;
  const color = deltaColor(delta);
  return (
    <span className="text-[10px] font-mono px-1.5 py-0.5 rounded-full"
      style={{ color, border: `1px solid color-mix(in srgb, ${color} 35%, transparent)` }}>
      {deltaSign(delta)}{fmtNum(delta)}{suffix || ''}
    </span>
  );
}

function CompareValue({ label, before, after, unit }: {
  label: string; before: unknown; after: unknown; unit?: string;
}) {
  const changed = String(before) !== String(after);
  return (
    <div className={`rounded-lg px-3 py-2 ${changed ? 'bg-[var(--neon-amber)]/5 border border-[var(--neon-amber)]/15' : 'bg-[var(--bg-glass)]'}`}>
      <div className="text-[10px] text-[var(--text-muted)] uppercase tracking-wide mb-1">{label}</div>
      {changed ? (
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm text-[var(--text-muted)] line-through">{fmtNum(before)}{unit || ''}</span>
          <span className="text-xs text-[var(--text-muted)]">→</span>
          <span className="font-mono text-sm text-[var(--neon-amber)] font-bold">{fmtNum(after)}{unit || ''}</span>
        </div>
      ) : (
        <span className="font-mono text-sm text-[var(--text-primary)]">{fmtNum(after)}{unit || ''}</span>
      )}
    </div>
  );
}

/** Circular score gauge used in health section */
function ScoreCircle({ score, label, size = 120, color }: {
  score: number | null; label: string; size?: number; color?: string;
}) {
  const hasScore = score !== null && score !== undefined;
  const s = hasScore ? score : 0;
  const c = !hasScore ? 'var(--text-muted)' : color || (s >= 80 ? 'var(--neon-green)' : s >= 50 ? 'var(--neon-amber)' : 'var(--neon-red)');
  const r = (size - 12) / 2;
  const circ = 2 * Math.PI * r;
  const offset = hasScore ? circ * (1 - s / 100) : circ;

  return (
    <div className="flex flex-col items-center">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="transform -rotate-90">
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--border-glass)" strokeWidth="6" />
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={c} strokeWidth="6"
            strokeDasharray={circ} strokeDashoffset={offset}
            strokeLinecap="round" className="transition-all duration-700" />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="font-mono text-2xl font-bold" style={{ color: c }}>{hasScore ? s : 'N/A'}</span>
        </div>
      </div>
      <span className="text-xs text-[var(--text-muted)] mt-1">{label}</span>
    </div>
  );
}

function LoadingPlaceholder() {
  return <div className="text-sm text-[var(--text-muted)] animate-pulse py-4 text-center">Loading...</div>;
}

function NoDataMessage({ message }: { message?: string }) {
  return (
    <div className="text-center py-6 text-[var(--text-muted)]">
      <div className="text-2xl mb-2 opacity-30">--</div>
      <div className="text-sm">{message || 'Data not collected for these runs'}</div>
      <div className="text-xs mt-1 opacity-60">Run a new diagnostic analysis to populate this data</div>
    </div>
  );
}

/** Check if a scalar detail has any non-null field values */
function hasAnyData(detail?: CompareDatasetDetail): boolean {
  if (!detail?.fields) return false;
  return detail.fields.some(f => f.run1Value !== null || f.run2Value !== null);
}

/* ================================================================
   SECTION: HEALTH SCORE
   ================================================================ */

function TrendsHealthSection({ manifest, detail }: {
  manifest: CompareManifest;
  detail?: CompareDatasetDetail;
}) {
  const run1Score = manifest.run1.health_score;
  const run2Score = manifest.run2.health_score;
  const delta = manifest.summary.healthScore.delta;
  const hmHasData = hasAnyData(detail);

  const getField = (name: string): CompareScalarField | undefined =>
    detail?.fields?.find(f => f.field === name);

  const categories = [
    { key: 'version_currency_score', label: 'Version Currency' },
    { key: 'system_capacity_score', label: 'System Capacity' },
    { key: 'configuration_score', label: 'Configuration' },
    { key: 'security_isolation_score', label: 'Security & Isolation' },
  ];

  const hasCategoryData = categories.some(({ key }) => {
    const f = getField(key);
    return f && (f.run1Value !== null || f.run2Value !== null);
  });

  return (
    <SectionCard title="Health Score" badge={<ChangeBadge delta={delta} />}>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Score circles */}
        <div className="flex items-center justify-center gap-6">
          <div className="relative">
            <ScoreCircle score={run2Score} label="Before" size={110} />
          </div>
          <div className="flex flex-col items-center">
            <span className="text-lg text-[var(--text-muted)]">→</span>
            <ChangeBadge delta={delta} />
          </div>
          <div className="relative">
            <ScoreCircle score={run1Score} label="After" size={110} />
          </div>
        </div>

        {/* Category scores */}
        <div className="space-y-3">
          <div className="text-xs text-[var(--text-muted)] uppercase tracking-wide mb-2">Category Scores</div>
          {hasCategoryData ? categories.map(({ key, label }) => {
            const field = getField(key);
            const before = typeof field?.run2Value === 'number' ? field.run2Value : null;
            const after = typeof field?.run1Value === 'number' ? field.run1Value : null;
            if (before === null && after === null) return null;
            const bv = before ?? 0;
            const av = after ?? 0;
            const changed = before !== after;
            return (
              <div key={key}>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-[var(--text-secondary)]">{label}</span>
                  <span className="font-mono text-[var(--text-muted)]">
                    {changed ? `${bv.toFixed(0)} → ${av.toFixed(0)}` : av.toFixed(0)}
                  </span>
                </div>
                <div className="relative h-3 rounded-full bg-[var(--bg-glass)] overflow-hidden">
                  {changed && before !== null && (
                    <div className="absolute inset-y-0 rounded-full opacity-40"
                      style={{ width: `${bv}%`, background: 'var(--neon-amber)' }} />
                  )}
                  <div className="absolute inset-y-0 rounded-full"
                    style={{
                      width: `${av}%`,
                      background: av >= 80 ? 'var(--neon-green)' : av >= 50 ? 'var(--neon-amber)' : 'var(--neon-red)',
                      opacity: changed ? 0.9 : 1,
                    }} />
                </div>
              </div>
            );
          }) : (
            <div className="text-xs text-[var(--text-muted)]">Category scores not collected</div>
          )}
        </div>

        {/* Issues trend summary */}
        <div>
          <div className="text-xs text-[var(--text-muted)] uppercase tracking-wide mb-2">Issue Trends</div>
          {!detail ? (
            <div className="text-xs text-[var(--text-muted)]">Loading...</div>
          ) : hmHasData ? (
            <IssueTrends detail={detail} />
          ) : (
            <div className="text-xs text-[var(--text-muted)]">Issue data not collected</div>
          )}
        </div>
      </div>
    </SectionCard>
  );
}

function IssueTrends({ detail }: { detail: CompareDatasetDetail }) {
  // Extract issue-related JSON fields from health metrics
  const getJson = (name: string): Record<string, unknown> | null => {
    const f = detail.fields?.find(ff => ff.field === name);
    if (!f) return null;
    const v = safeJsonParse(f.run1Value);
    return typeof v === 'object' && v !== null ? v as Record<string, unknown> : null;
  };
  const getJsonBefore = (name: string): Record<string, unknown> | null => {
    const f = detail.fields?.find(ff => ff.field === name);
    if (!f) return null;
    const v = safeJsonParse(f.run2Value);
    return typeof v === 'object' && v !== null ? v as Record<string, unknown> : null;
  };

  // Parse general_settings to extract issue counts
  const settingsAfter = getJson('general_settings_json');
  const settingsBefore = getJsonBefore('general_settings_json');

  // Count disabled features as a proxy for issues
  const countIssues = (settings: Record<string, unknown> | null): number => {
    if (!settings) return 0;
    let count = 0;
    for (const val of Object.values(settings)) {
      if (typeof val === 'object' && val !== null && 'value' in (val as Record<string, unknown>)) {
        const v = (val as Record<string, unknown>).value;
        if (v === false || v === 'false') count++;
      }
    }
    return count;
  };

  const issuesBefore = countIssues(settingsBefore);
  const issuesAfter = countIssues(settingsAfter);
  const diff = issuesAfter - issuesBefore;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3 rounded-lg bg-[var(--bg-glass)] px-3 py-2">
        <span className="text-xs text-[var(--text-muted)]">Disabled features</span>
        <span className="font-mono text-sm font-bold text-[var(--text-primary)]">{issuesBefore} → {issuesAfter}</span>
        <ChangeBadge delta={diff} />
      </div>
      {diff < 0 && (
        <div className="text-xs text-[var(--neon-green)] bg-[var(--neon-green)]/5 rounded px-3 py-1.5">
          {Math.abs(diff)} issue{Math.abs(diff) > 1 ? 's' : ''} resolved
        </div>
      )}
      {diff > 0 && (
        <div className="text-xs text-[var(--neon-red)] bg-[var(--neon-red)]/5 rounded px-3 py-1.5">
          {diff} new issue{diff > 1 ? 's' : ''} detected
        </div>
      )}
    </div>
  );
}

/* ================================================================
   SECTION: SYSTEM OVERVIEW
   ================================================================ */

function TrendsSystemSection({ manifest, detail }: {
  manifest: CompareManifest; detail?: CompareDatasetDetail;
}) {
  const getField = (name: string): CompareScalarField | undefined =>
    detail?.fields?.find(f => f.field === name);

  const fields = [
    { key: 'dss_version', label: 'DSS Version', src: 'run' },
    { key: 'python_version', label: 'Python Version', src: 'run' },
    { key: 'cpu_cores', label: 'CPU Cores', src: 'hm' },
    { key: 'memory_total_mb', label: 'Memory', src: 'hm', fmt: (v: unknown) => typeof v === 'number' ? `${(v / 1024).toFixed(1)} GB` : fmtNum(v) },
    { key: 'os_info', label: 'OS', src: 'hm' },
  ];

  return (
    <SectionCard title="System Overview">
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        {fields.map(({ key, label, src, fmt }) => {
          let before: unknown, after: unknown;
          if (src === 'run') {
            before = manifest.run2[key as keyof typeof manifest.run2];
            after = manifest.run1[key as keyof typeof manifest.run1];
          } else {
            const f = getField(key);
            before = f?.run2Value;
            after = f?.run1Value;
          }
          const bStr = fmt ? fmt(before) : fmtNum(before);
          const aStr = fmt ? fmt(after) : fmtNum(after);
          const changed = bStr !== aStr;
          return (
            <div key={key} className={`rounded-lg px-3 py-2 ${changed ? 'bg-[var(--neon-amber)]/5 border border-[var(--neon-amber)]/15' : 'bg-[var(--bg-glass)]'}`}>
              <div className="text-[10px] text-[var(--text-muted)] uppercase tracking-wide mb-1">{label}</div>
              {changed ? (
                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className="font-mono text-xs text-[var(--text-muted)] line-through">{bStr}</span>
                  <span className="text-xs text-[var(--text-muted)]">→</span>
                  <span className="font-mono text-sm text-[var(--neon-amber)] font-bold">{aStr}</span>
                </div>
              ) : (
                <span className="font-mono text-sm text-[var(--text-primary)]">{aStr}</span>
              )}
            </div>
          );
        })}
      </div>
    </SectionCard>
  );
}

/* ================================================================
   SECTION: FILESYSTEM
   ================================================================ */

function TrendsFilesystemSection({ detail }: { detail?: CompareDatasetDetail }) {
  const field = detail?.fields?.find(f => f.field === 'filesystem_mounts_json');
  if (!field) return <SectionCard title="Filesystem"><LoadingPlaceholder /></SectionCard>;
  if (field.run1Value === null && field.run2Value === null) {
    return <SectionCard title="Filesystem"><NoDataMessage /></SectionCard>;
  }

  const mountsBefore = (safeJsonParse(field.run2Value) || []) as Record<string, string>[];
  const mountsAfter = (safeJsonParse(field.run1Value) || []) as Record<string, string>[];

  // Merge mounts by mount point
  const allMounts = new Map<string, { before?: Record<string, string>; after?: Record<string, string> }>();
  for (const m of mountsBefore) {
    const key = m['Mounted on'] || m['mount'] || '';
    allMounts.set(key, { before: m });
  }
  for (const m of mountsAfter) {
    const key = m['Mounted on'] || m['mount'] || '';
    const existing = allMounts.get(key) || {};
    allMounts.set(key, { ...existing, after: m });
  }

  return (
    <SectionCard title="Filesystem" subtitle={`${allMounts.size} mount points`}>
      <div className="space-y-3">
        {Array.from(allMounts.entries()).map(([mount, { before, after }]) => {
          const bPct = parseFloat(before?.['Use%'] || '0');
          const aPct = parseFloat(after?.['Use%'] || '0');
          const changed = Math.abs(bPct - aPct) > 0.5;
          const pctDelta = aPct - bPct;
          return (
            <div key={mount}>
              <div className="flex justify-between text-xs mb-1">
                <span className="text-[var(--text-secondary)] truncate max-w-[300px]">{mount}</span>
                <span className="font-mono text-[var(--text-muted)] flex items-center gap-2">
                  {after?.Size || before?.Size}
                  {changed && <ChangeBadge delta={pctDelta} suffix="%" />}
                </span>
              </div>
              <div className="relative h-4 rounded-full bg-[var(--bg-glass)] overflow-hidden">
                {changed && (
                  <div className="absolute inset-y-0 rounded-full opacity-30"
                    style={{ width: `${bPct}%`, background: 'var(--neon-cyan)' }} />
                )}
                <div className="absolute inset-y-0 rounded-full"
                  style={{
                    width: `${aPct}%`,
                    background: aPct >= 90 ? 'var(--neon-red)' : aPct >= 70 ? 'var(--neon-amber)' : 'var(--neon-green)',
                  }} />
                <div className="absolute inset-0 flex items-center justify-end pr-2">
                  <span className="text-[10px] font-mono text-[var(--text-primary)]">{aPct.toFixed(1)}%</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </SectionCard>
  );
}

/* ================================================================
   SECTION: MEMORY
   ================================================================ */

function TrendsMemorySection({ detail }: { detail?: CompareDatasetDetail }) {
  const getField = (name: string): CompareScalarField | undefined =>
    detail?.fields?.find(f => f.field === name);

  const totalField = getField('memory_total_mb');
  if (!totalField || (totalField.run1Value === null && totalField.run2Value === null)) {
    return <SectionCard title="Memory"><NoDataMessage /></SectionCard>;
  }

  const totalBefore = toNum(totalField.run2Value);
  const totalAfter = toNum(totalField.run1Value);
  const usedBefore = toNum(getField('memory_used_mb')?.run2Value);
  const usedAfter = toNum(getField('memory_used_mb')?.run1Value);
  const availBefore = toNum(getField('memory_available_mb')?.run2Value);
  const availAfter = toNum(getField('memory_available_mb')?.run1Value);

  const pctBefore = totalBefore && usedBefore ? (usedBefore / totalBefore) * 100 : null;
  const pctAfter = totalAfter && usedAfter ? (usedAfter / totalAfter) * 100 : null;

  const memFields = [
    { key: 'backend_heap_mb', label: 'Backend Heap' },
    { key: 'jek_heap_mb', label: 'JEK Heap' },
    { key: 'fek_heap_mb', label: 'FEK Heap' },
    { key: 'open_files_limit', label: 'Open Files Limit' },
  ];

  return (
    <SectionCard title="Memory">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Two memory circles side by side */}
        <div className="flex items-center justify-center gap-8">
          <MemoryCircle total={totalBefore} pct={pctBefore} label="Before" />
          <div className="flex flex-col items-center">
            <span className="text-lg text-[var(--text-muted)]">→</span>
            {pctBefore !== null && pctAfter !== null && <ChangeBadge delta={pctAfter - pctBefore} suffix="%" />}
          </div>
          <MemoryCircle total={totalAfter} pct={pctAfter} label="After" />
        </div>

        {/* Memory analysis numbers side by side */}
        <div className="space-y-2">
          <div className="text-xs text-[var(--text-muted)] uppercase tracking-wide mb-2">Memory Analysis</div>
          <CompareValue label="Total Memory" before={fmtMB(totalBefore)} after={fmtMB(totalAfter)} />
          <CompareValue label="Available" before={fmtMB(availBefore)} after={fmtMB(availAfter)} />
          {memFields.map(({ key, label }) => {
            const f = getField(key);
            if (!f || (f.run1Value === null && f.run2Value === null)) return null;
            return (
              <CompareValue key={key} label={label}
                before={f.run2Value != null ? `${fmtNum(f.run2Value)} MB` : '--'}
                after={f.run1Value != null ? `${fmtNum(f.run1Value)} MB` : '--'} />
            );
          })}
        </div>
      </div>
    </SectionCard>
  );
}

function MemoryCircle({ total, pct, label }: {
  total: number | null; pct: number | null; label: string;
}) {
  const size = 100;
  const r = (size - 10) / 2;
  const circ = 2 * Math.PI * r;
  const hasData = pct !== null && total !== null;
  const offset = hasData ? circ * (1 - pct / 100) : circ;
  const color = !hasData ? 'var(--text-muted)' : pct >= 90 ? 'var(--neon-red)' : pct >= 70 ? 'var(--neon-amber)' : 'var(--neon-green)';

  return (
    <div className="flex flex-col items-center">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="transform -rotate-90">
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--border-glass)" strokeWidth="5" />
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth="5"
            strokeDasharray={circ} strokeDashoffset={offset}
            strokeLinecap="round" className="transition-all duration-700" />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="font-mono text-sm font-bold" style={{ color }}>{hasData ? `${pct.toFixed(0)}%` : 'N/A'}</span>
          <span className="text-[9px] text-[var(--text-muted)]">{hasData ? fmtMB(total) : ''}</span>
        </div>
      </div>
      <span className="text-xs text-[var(--text-muted)] mt-1">{label}</span>
    </div>
  );
}

/* ================================================================
   SECTION: CONNECTIONS
   ================================================================ */

function TrendsConnectionsSection({ detail, connHealthDetail }: {
  detail?: CompareDatasetDetail;
  connHealthDetail?: CompareDatasetDetail;
}) {
  const field = detail?.fields?.find(f => f.field === 'connections_json');
  if (!field || (field.run1Value === null && field.run2Value === null)) {
    return <SectionCard title="Connections"><NoDataMessage /></SectionCard>;
  }

  const parseConns = (val: unknown): Record<string, number> => {
    const parsed = safeJsonParse(val);
    if (!parsed || typeof parsed !== 'object') return {};
    const obj = parsed as Record<string, unknown>;
    const counts = obj.typeCounts as Record<string, number> | undefined;
    if (counts && typeof counts === 'object') return counts;
    // Fallback: count from details array
    const details = obj.details as Array<Record<string, string>> | undefined;
    if (Array.isArray(details)) {
      const result: Record<string, number> = {};
      for (const d of details) { const t = d.type || 'unknown'; result[t] = (result[t] || 0) + 1; }
      return result;
    }
    return {};
  };

  const connsBefore = parseConns(field.run2Value);
  const connsAfter = parseConns(field.run1Value);
  const hasBefore = Object.keys(connsBefore).length > 0;
  const hasAfter = Object.keys(connsAfter).length > 0;
  const totalBefore = hasBefore ? Object.values(connsBefore).reduce((s, v) => s + v, 0) : null;
  const totalAfter = hasAfter ? Object.values(connsAfter).reduce((s, v) => s + v, 0) : null;

  // Merge connection types
  const allTypes = new Set([...Object.keys(connsBefore), ...Object.keys(connsAfter)]);
  const sorted = Array.from(allTypes).sort((a, b) => (connsAfter[b] || 0) - (connsAfter[a] || 0));

  return (
    <SectionCard title="Connections" badge={totalBefore !== null && totalAfter !== null ? <ChangeBadge delta={totalAfter - totalBefore} /> : undefined}>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Two donut-style summaries side by side */}
        <div className="flex items-center justify-center gap-8">
          <div className="flex flex-col items-center">
            <div className="w-20 h-20 rounded-full border-4 flex items-center justify-center"
              style={{ borderColor: 'var(--neon-cyan)' }}>
              <span className="font-mono text-xl font-bold text-[var(--text-primary)]">{totalBefore ?? 'N/A'}</span>
            </div>
            <span className="text-xs text-[var(--text-muted)] mt-1">Before</span>
          </div>
          <div className="flex flex-col items-center">
            <span className="text-lg text-[var(--text-muted)]">→</span>
            {totalBefore !== null && totalAfter !== null && <ChangeBadge delta={totalAfter - totalBefore} />}
          </div>
          <div className="flex flex-col items-center">
            <div className="w-20 h-20 rounded-full border-4 flex items-center justify-center"
              style={{ borderColor: 'var(--neon-purple)' }}>
              <span className="font-mono text-xl font-bold text-[var(--text-primary)]">{totalAfter ?? 'N/A'}</span>
            </div>
            <span className="text-xs text-[var(--text-muted)] mt-1">After</span>
          </div>
        </div>

        {/* Connection type breakdown side by side */}
        <div className="space-y-1.5">
          <div className="text-xs text-[var(--text-muted)] uppercase tracking-wide mb-2">By Type</div>
          {sorted.map((type) => {
            const b = connsBefore[type] || 0;
            const a = connsAfter[type] || 0;
            const changed = b !== a;
            return (
              <div key={type} className={`flex items-center gap-2 text-xs rounded px-2 py-1 ${changed ? 'bg-[var(--neon-amber)]/5' : ''}`}>
                <span className="text-[var(--text-secondary)] flex-1 truncate">{type}</span>
                {changed ? (
                  <>
                    <span className="font-mono text-[var(--text-muted)]">{b}</span>
                    <span className="text-[var(--text-muted)]">→</span>
                    <span className="font-mono text-[var(--text-primary)]">{a}</span>
                    <ChangeBadge delta={a - b} />
                  </>
                ) : (
                  <span className="font-mono text-[var(--text-primary)]">{a}</span>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Connection Health sub-section */}
      {connHealthDetail && <TrendsConnectionHealthSub detail={connHealthDetail} />}
    </SectionCard>
  );
}

function TrendsConnectionHealthSub({ detail }: { detail: CompareDatasetDetail }) {
  const rows = detail.rows ?? [];
  if (rows.length === 0) return null;

  const broken = rows.filter(r => r.status === 'changed' && r.run1?.status === 'fail' && r.run2?.status === 'ok');
  const fixed = rows.filter(r => r.status === 'changed' && r.run1?.status === 'ok' && r.run2?.status === 'fail');
  const newFails = rows.filter(r => r.status === 'added' && (r.run1?.status === 'fail' || r.run2?.status === 'fail'));
  const unchanged = rows.filter(r => r.status === 'unchanged');
  const changedRows = [...broken, ...fixed, ...newFails];

  if (changedRows.length === 0 && unchanged.length > 0) {
    return (
      <div className="mt-4 pt-4 border-t border-[var(--border-glass)]">
        <div className="text-xs text-[var(--text-muted)] uppercase tracking-wide mb-2">Connection Health</div>
        <div className="text-xs text-[var(--text-muted)]">No changes — {unchanged.length} connections unchanged</div>
      </div>
    );
  }

  return (
    <div className="mt-4 pt-4 border-t border-[var(--border-glass)]">
      <div className="text-xs text-[var(--text-muted)] uppercase tracking-wide mb-2">Connection Health Changes</div>
      <div className="space-y-1">
        {broken.map((r) => (
          <div key={r.key.connection_name} className="flex items-center gap-2 text-xs rounded px-2 py-1.5 bg-[var(--neon-red)]/5">
            <span className="text-[var(--neon-red)]">broke</span>
            <span className="text-[var(--text-primary)] font-medium">{r.key.connection_name}</span>
            <span className="text-[var(--text-muted)] truncate flex-1">{String(r.run1?.error_message || '')}</span>
          </div>
        ))}
        {fixed.map((r) => (
          <div key={r.key.connection_name} className="flex items-center gap-2 text-xs rounded px-2 py-1.5 bg-[var(--neon-green)]/5">
            <span className="text-[var(--neon-green)]">fixed</span>
            <span className="text-[var(--text-primary)] font-medium">{r.key.connection_name}</span>
          </div>
        ))}
        {newFails.map((r) => {
          const data = r.run1 ?? r.run2 ?? {};
          return (
            <div key={r.key.connection_name} className="flex items-center gap-2 text-xs rounded px-2 py-1.5 bg-[var(--neon-amber)]/5">
              <span className="text-[var(--neon-amber)]">{r.status}</span>
              <span className="text-[var(--text-primary)] font-medium">{r.key.connection_name}</span>
              <span className="text-[var(--text-muted)] truncate flex-1">{String(data.error_message || '')}</span>
            </div>
          );
        })}
      </div>
      {unchanged.length > 0 && (
        <div className="text-xs text-[var(--text-muted)] mt-2">+ {unchanged.length} unchanged connections</div>
      )}
    </div>
  );
}

/* ================================================================
   SECTION: RUNTIME CONFIG
   ================================================================ */

function TrendsRuntimeSection({ detail }: { detail?: CompareDatasetDetail }) {
  const [showUnchanged, setShowUnchanged] = useState(false);
  const field = detail?.fields?.find(f => f.field === 'general_settings_json');
  if (!field) return <SectionCard title="Runtime Config"><LoadingPlaceholder /></SectionCard>;
  if (field.run1Value === null && field.run2Value === null) {
    return <SectionCard title="Runtime Config"><NoDataMessage /></SectionCard>;
  }

  const before = (safeJsonParse(field.run2Value) || {}) as Record<string, Record<string, unknown>>;
  const after = (safeJsonParse(field.run1Value) || {}) as Record<string, Record<string, unknown>>;

  // Group by settings category (table name)
  const allCategories = new Set([...Object.keys(before), ...Object.keys(after)]);
  const categorized: Record<string, Array<{ key: string; b: unknown; a: unknown; changed: boolean }>> = {};

  for (const cat of allCategories) {
    const bCat = before[cat] || {};
    const aCat = after[cat] || {};
    if (typeof bCat !== 'object' || typeof aCat !== 'object') continue;
    const allKeys = new Set([...Object.keys(bCat), ...Object.keys(aCat)]);
    const entries: Array<{ key: string; b: unknown; a: unknown; changed: boolean }> = [];
    for (const k of allKeys) {
      const bv = (bCat as Record<string, unknown>)[k];
      const av = (aCat as Record<string, unknown>)[k];
      entries.push({ key: k, b: bv, a: av, changed: String(bv) !== String(av) });
    }
    if (entries.length > 0) categorized[cat] = entries;
  }

  const changedCategories = Object.entries(categorized).filter(([, entries]) => entries.some(e => e.changed));
  const unchangedCategories = Object.entries(categorized).filter(([, entries]) => !entries.some(e => e.changed));

  return (
    <SectionCard title="Runtime Config"
      subtitle={`${changedCategories.length} changed, ${unchangedCategories.length} unchanged`}>
      {changedCategories.length === 0 ? (
        <div className="text-sm text-[var(--text-muted)]">No configuration changes detected</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {changedCategories.map(([cat, entries]) => (
            <SettingsTable key={cat} category={cat} entries={entries} defaultShowUnchanged={false} />
          ))}
        </div>
      )}
      {unchangedCategories.length > 0 && (
        <div className="mt-3">
          <button onClick={() => setShowUnchanged(!showUnchanged)}
            className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] font-mono px-2 py-1 rounded border border-[var(--border-glass)] hover:bg-[var(--bg-glass-hover)]">
            {showUnchanged ? '▼' : '▶'} Show {unchangedCategories.length} unchanged categories
          </button>
          {showUnchanged && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-3 opacity-60">
              {unchangedCategories.map(([cat, entries]) => (
                <SettingsTable key={cat} category={cat} entries={entries} defaultShowUnchanged={true} />
              ))}
            </div>
          )}
        </div>
      )}
    </SectionCard>
  );
}

function SettingsTable({ category, entries, defaultShowUnchanged }: {
  category: string;
  entries: Array<{ key: string; b: unknown; a: unknown; changed: boolean }>;
  defaultShowUnchanged: boolean;
}) {
  const changed = entries.filter(e => e.changed);
  const unchanged = entries.filter(e => !e.changed);

  return (
    <div className="glass-card overflow-hidden">
      <div className="px-3 py-2 border-b border-[var(--border-glass)] bg-[var(--bg-glass)]">
        <span className="text-xs font-semibold text-[var(--text-secondary)]">{category}</span>
        {changed.length > 0 && (
          <span className="ml-2 text-[10px] font-mono text-[var(--neon-amber)]">{changed.length} changed</span>
        )}
      </div>
      <div className="max-h-48 overflow-y-auto">
        <table className="w-full text-xs">
          <tbody>
            {changed.map(({ key, b, a }) => (
              <tr key={key} className="border-b border-[var(--border-glass)] bg-[var(--neon-amber)]/5">
                <td className="py-1 px-2 text-[var(--text-secondary)] font-mono truncate max-w-[120px]">{key}</td>
                <td className="py-1 px-2 text-[var(--text-muted)] font-mono line-through">{fmtNum(b)}</td>
                <td className="py-1 px-2 text-xs text-[var(--text-muted)]">→</td>
                <td className="py-1 px-2 text-[var(--neon-amber)] font-mono font-bold">{fmtNum(a)}</td>
              </tr>
            ))}
            {(defaultShowUnchanged ? unchanged : []).map(({ key, a }) => (
              <tr key={key} className="border-b border-[var(--border-glass)]">
                <td className="py-1 px-2 text-[var(--text-muted)] font-mono truncate max-w-[120px]">{key}</td>
                <td colSpan={3} className="py-1 px-2 text-[var(--text-primary)] font-mono">{fmtNum(a)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ================================================================
   SECTION: PLUGINS (part of runtime)
   ================================================================ */

function TrendsPluginsSection({ detail }: { detail?: CompareDatasetDetail }) {
  const [showUnchanged, setShowUnchanged] = useState(false);
  const rows = detail?.rows ?? [];

  if (rows.length === 0 && !detail) return null;

  const added = rows.filter(r => r.status === 'added');
  const removed = rows.filter(r => r.status === 'removed');
  const changed = rows.filter(r => r.status === 'changed');
  const unchanged = rows.filter(r => r.status === 'unchanged');

  if (added.length === 0 && removed.length === 0 && changed.length === 0) {
    return (
      <SectionCard title="Plugins" subtitle={`${unchanged.length} plugins, no changes`}>
        <div className="text-sm text-[var(--text-muted)]">No plugin changes detected</div>
      </SectionCard>
    );
  }

  return (
    <SectionCard title="Plugins"
      badge={<>
        {added.length > 0 && <span className="text-[10px] font-mono text-[var(--neon-green)]">+{added.length}</span>}
        {removed.length > 0 && <span className="text-[10px] font-mono text-[var(--neon-red)]">-{removed.length}</span>}
        {changed.length > 0 && <span className="text-[10px] font-mono text-[var(--neon-amber)]">~{changed.length}</span>}
      </>}>
      <div className="space-y-1">
        {added.map((r) => {
          const d = r.run1 ?? {};
          return (
            <div key={r.key.plugin_id} className="flex items-center gap-2 text-xs rounded px-2 py-1.5 bg-[var(--neon-green)]/5">
              <span className="text-[10px] font-mono text-[var(--neon-green)] w-12">added</span>
              <span className="text-[var(--text-primary)] font-medium">{String(d.label || r.key.plugin_id)}</span>
              <span className="text-[var(--text-muted)] font-mono">{String(d.version || '')}</span>
            </div>
          );
        })}
        {removed.map((r) => {
          const d = r.run2 ?? {};
          return (
            <div key={r.key.plugin_id} className="flex items-center gap-2 text-xs rounded px-2 py-1.5 bg-[var(--neon-red)]/5">
              <span className="text-[10px] font-mono text-[var(--neon-red)] w-12">removed</span>
              <span className="text-[var(--text-primary)] font-medium">{String(d.label || r.key.plugin_id)}</span>
              <span className="text-[var(--text-muted)] font-mono">{String(d.version || '')}</span>
            </div>
          );
        })}
        {changed.map((r) => (
          <div key={r.key.plugin_id} className="flex items-center gap-2 text-xs rounded px-2 py-1.5 bg-[var(--neon-amber)]/5">
            <span className="text-[10px] font-mono text-[var(--neon-amber)] w-12">updated</span>
            <span className="text-[var(--text-primary)] font-medium">{String(r.run1?.label || r.key.plugin_id)}</span>
            <span className="text-[var(--text-muted)] font-mono line-through">{String(r.run2?.version || '')}</span>
            <span className="text-[var(--text-muted)]">→</span>
            <span className="text-[var(--neon-amber)] font-mono font-bold">{String(r.run1?.version || '')}</span>
          </div>
        ))}
      </div>
      {unchanged.length > 0 && (
        <div className="mt-2">
          <button onClick={() => setShowUnchanged(!showUnchanged)}
            className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] font-mono px-2 py-1 rounded border border-[var(--border-glass)]">
            {showUnchanged ? '▼' : '▶'} {unchanged.length} unchanged plugins
          </button>
          {showUnchanged && (
            <div className="mt-1 space-y-0.5 opacity-50">
              {unchanged.map((r) => {
                const d = r.run1 ?? r.run2 ?? {};
                return (
                  <div key={r.key.plugin_id} className="flex items-center gap-2 text-xs px-2 py-1">
                    <span className="text-[var(--text-primary)]">{String(d.label || r.key.plugin_id)}</span>
                    <span className="text-[var(--text-muted)] font-mono">{String(d.version || '')}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </SectionCard>
  );
}

/* ================================================================
   SECTION: PROJECTS
   ================================================================ */

function TrendsProjectsSection({ projectDetail, footprintDetail }: {
  projectDetail?: CompareDatasetDetail;
  footprintDetail?: CompareDatasetDetail;
}) {
  return (
    <>
      <KeyedTableSection title="Projects" detail={projectDetail}
        nameKey="project_key" extraColumns={['name', 'owner_login']} />
      {footprintDetail && <TrendsFootprintSection detail={footprintDetail} />}
    </>
  );
}

function TrendsFootprintSection({ detail }: { detail: CompareDatasetDetail }) {
  const field = detail.fields?.find(f => f.field === 'project_footprint_json');
  if (!field) return null;

  const before = (safeJsonParse(field.run2Value) || []) as Array<Record<string, unknown>>;
  const after = (safeJsonParse(field.run1Value) || []) as Array<Record<string, unknown>>;

  // Merge by project key
  const merged = new Map<string, { b?: Record<string, unknown>; a?: Record<string, unknown> }>();
  for (const p of before) {
    const k = String(p.projectKey || p.project_key || '');
    if (k) merged.set(k, { b: p });
  }
  for (const p of after) {
    const k = String(p.projectKey || p.project_key || '');
    if (k) merged.set(k, { ...merged.get(k), a: p });
  }

  // Sort by biggest size delta
  const sorted = Array.from(merged.entries()).sort((x, y) => {
    const dX = Math.abs((toNum(x[1].a?.sizeBytes) ?? 0) - (toNum(x[1].b?.sizeBytes) ?? 0));
    const dY = Math.abs((toNum(y[1].a?.sizeBytes) ?? 0) - (toNum(y[1].b?.sizeBytes) ?? 0));
    return dY - dX;
  });

  if (sorted.length === 0) return null;

  return (
    <SectionCard title="Project Footprint" subtitle={`${sorted.length} projects`}>
      <div className="max-h-64 overflow-y-auto">
        <table className="w-full text-xs font-mono">
          <thead className="sticky top-0 bg-[var(--bg-surface)] z-10">
            <tr className="border-b border-[var(--border-glass)]">
              <th className="py-1.5 px-2 text-left text-[var(--text-muted)] font-normal">Project</th>
              <th className="py-1.5 px-2 text-right text-[var(--text-muted)] font-normal">Before</th>
              <th className="py-1.5 px-2 text-right text-[var(--text-muted)] font-normal">After</th>
              <th className="py-1.5 px-2 text-right text-[var(--text-muted)] font-normal">Delta</th>
            </tr>
          </thead>
          <tbody>
            {sorted.slice(0, 50).map(([key, { b, a }]) => {
              const bSize = toNum(b?.sizeBytes);
              const aSize = toNum(a?.sizeBytes);
              const delta = (aSize ?? 0) - (bSize ?? 0);
              return (
                <tr key={key} className={`border-b border-[var(--border-glass)] ${delta !== 0 ? 'bg-[var(--neon-amber)]/5' : ''}`}>
                  <td className="py-1 px-2 text-[var(--text-primary)] truncate max-w-[200px]">{key}</td>
                  <td className="py-1 px-2 text-right text-[var(--text-muted)]">{fmtBytes(bSize)}</td>
                  <td className="py-1 px-2 text-right text-[var(--text-primary)]">{fmtBytes(aSize)}</td>
                  <td className="py-1 px-2 text-right">
                    {bSize !== null && aSize !== null && delta !== 0 ? (
                      <span style={{ color: deltaColor(delta) }}>{deltaSign(delta)}{fmtBytes(Math.abs(delta))}</span>
                    ) : (
                      <span className="text-[var(--text-muted)]">--</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </SectionCard>
  );
}

/* ================================================================
   SECTION: CODE ENVIRONMENTS
   ================================================================ */

function TrendsCodeEnvsSection({ detail }: { detail?: CompareDatasetDetail }) {
  const field = detail?.fields?.find(f => f.field === 'code_envs_json');
  if (!field) return <SectionCard title="Code Environments"><LoadingPlaceholder /></SectionCard>;
  if (field.run1Value === null && field.run2Value === null) {
    return <SectionCard title="Code Environments"><NoDataMessage /></SectionCard>;
  }

  const before = safeJsonParse(field.run2Value) as Record<string, unknown> | null;
  const after = safeJsonParse(field.run1Value) as Record<string, unknown> | null;

  const bEnvs = (before?.codeEnvs || []) as Array<Record<string, unknown>>;
  const aEnvs = (after?.codeEnvs || []) as Array<Record<string, unknown>>;

  // Index by name
  const bMap = new Map(bEnvs.map(e => [String(e.envName || e.name || ''), e]));
  const aMap = new Map(aEnvs.map(e => [String(e.envName || e.name || ''), e]));
  const allNames = new Set([...bMap.keys(), ...aMap.keys()]);

  const added: Array<Record<string, unknown>> = [];
  const removed: Array<Record<string, unknown>> = [];
  const changed: Array<{ name: string; b: Record<string, unknown>; a: Record<string, unknown> }> = [];
  const unchanged: string[] = [];

  for (const name of allNames) {
    if (!name) continue;
    const b = bMap.get(name);
    const a = aMap.get(name);
    if (!b && a) { added.push(a); continue; }
    if (b && !a) { removed.push(b); continue; }
    if (b && a) {
      const bStr = JSON.stringify(b);
      const aStr = JSON.stringify(a);
      if (bStr !== aStr) { changed.push({ name, b, a }); } else { unchanged.push(name); }
    }
  }

  const hasChanges = added.length > 0 || removed.length > 0 || changed.length > 0;

  return (
    <SectionCard title="Code Environments"
      subtitle={`${allNames.size} environments`}
      badge={hasChanges ? <>
        {added.length > 0 && <span className="text-[10px] font-mono text-[var(--neon-green)]">+{added.length}</span>}
        {removed.length > 0 && <span className="text-[10px] font-mono text-[var(--neon-red)]">-{removed.length}</span>}
        {changed.length > 0 && <span className="text-[10px] font-mono text-[var(--neon-amber)]">~{changed.length}</span>}
      </> : undefined}>
      {!hasChanges ? (
        <div className="text-sm text-[var(--text-muted)]">No code environment changes detected</div>
      ) : (
        <div className="space-y-1">
          {added.map((e) => (
            <div key={String(e.envName || e.name)} className="flex items-center gap-2 text-xs rounded px-2 py-1.5 bg-[var(--neon-green)]/5">
              <span className="text-[10px] font-mono text-[var(--neon-green)] w-14">added</span>
              <span className="text-[var(--text-primary)] font-medium">{String(e.envName || e.name)}</span>
              <span className="text-[var(--text-muted)]">{String(e.envLang || '')} {String(e.envVersion || '')}</span>
            </div>
          ))}
          {removed.map((e) => (
            <div key={String(e.envName || e.name)} className="flex items-center gap-2 text-xs rounded px-2 py-1.5 bg-[var(--neon-red)]/5">
              <span className="text-[10px] font-mono text-[var(--neon-red)] w-14">removed</span>
              <span className="text-[var(--text-primary)] font-medium">{String(e.envName || e.name)}</span>
              <span className="text-[var(--text-muted)]">{String(e.envLang || '')} {String(e.envVersion || '')}</span>
            </div>
          ))}
          {changed.map(({ name, b, a }) => (
            <div key={name} className="flex items-center gap-2 text-xs rounded px-2 py-1.5 bg-[var(--neon-amber)]/5">
              <span className="text-[10px] font-mono text-[var(--neon-amber)] w-14">changed</span>
              <span className="text-[var(--text-primary)] font-medium">{name}</span>
              {String(b.envVersion) !== String(a.envVersion) && (
                <span className="text-[var(--text-muted)]">{String(b.envVersion)} → {String(a.envVersion)}</span>
              )}
            </div>
          ))}
        </div>
      )}
      {unchanged.length > 0 && (
        <div className="text-xs text-[var(--text-muted)] mt-2">+ {unchanged.length} unchanged environments</div>
      )}
    </SectionCard>
  );
}

/* ================================================================
   SECTION: DB HEALTH
   ================================================================ */

function TrendsDbHealthSection({ summaryDetail, tableDetail }: {
  summaryDetail?: CompareDatasetDetail;
  tableDetail?: CompareDatasetDetail;
}) {
  const field = summaryDetail?.fields?.find(f => f.field === 'db_health_json');
  const before = (safeJsonParse(field?.run2Value) || {}) as Record<string, unknown>;
  const after = (safeJsonParse(field?.run1Value) || {}) as Record<string, unknown>;

  const hasSummary = Object.keys(before).length > 0 || Object.keys(after).length > 0;
  if (!hasSummary && !tableDetail) return null;

  return (
    <SectionCard title="DB Health">
      {/* Overview cards side by side */}
      {hasSummary && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
          <CompareValue label="Database Size" before={before.db_size || '--'} after={after.db_size || '--'} />
          <CompareValue label="Tables" before={before.table_count} after={after.table_count} />
          <CompareValue label="PG Version" before={before.pg_version || '--'} after={after.pg_version || '--'} />
          <CompareValue label="Dead Tuples" before={before.total_dead_tuples} after={after.total_dead_tuples} />
        </div>
      )}

      {/* Per-table breakdown */}
      {tableDetail && tableDetail.rows && tableDetail.rows.length > 0 && (
        <DbHealthTableDiff detail={tableDetail} />
      )}
    </SectionCard>
  );
}

function DbHealthTableDiff({ detail }: { detail: CompareDatasetDetail }) {
  const rows = detail.rows ?? [];
  const changed = rows.filter(r => r.status !== 'unchanged');
  const unchanged = rows.filter(r => r.status === 'unchanged');
  const [showUnchanged, setShowUnchanged] = useState(false);

  // Sort changed by biggest dead tuple increase
  const sorted = [...changed].sort((a, b) => {
    const aDelta = Math.abs((toNum(a.run1?.dead_tuples) ?? 0) - (toNum(a.run2?.dead_tuples) ?? 0));
    const bDelta = Math.abs((toNum(b.run1?.dead_tuples) ?? 0) - (toNum(b.run2?.dead_tuples) ?? 0));
    return bDelta - aDelta;
  });

  return (
    <div>
      <div className="text-xs text-[var(--text-muted)] uppercase tracking-wide mb-2">Table Breakdown</div>
      {sorted.length > 0 ? (
        <div className="max-h-64 overflow-y-auto">
          <table className="w-full text-xs font-mono">
            <thead className="sticky top-0 bg-[var(--bg-surface)] z-10">
              <tr className="border-b border-[var(--border-glass)]">
                <th className="py-1.5 px-2 text-left text-[var(--text-muted)] font-normal">Status</th>
                <th className="py-1.5 px-2 text-left text-[var(--text-muted)] font-normal">Table</th>
                <th className="py-1.5 px-2 text-right text-[var(--text-muted)] font-normal">Size</th>
                <th className="py-1.5 px-2 text-right text-[var(--text-muted)] font-normal">Rows</th>
                <th className="py-1.5 px-2 text-right text-[var(--text-muted)] font-normal">Dead</th>
                <th className="py-1.5 px-2 text-right text-[var(--text-muted)] font-normal">Bloat%</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r) => {
                const d = r.run1 ?? r.run2 ?? {};
                const statusColor = r.status === 'added' ? 'var(--neon-green)' : r.status === 'removed' ? 'var(--neon-red)' : 'var(--neon-amber)';
                return (
                  <tr key={r.key.table_name} className="border-b border-[var(--border-glass)]">
                    <td className="py-1 px-2">
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full" style={{ color: statusColor, border: `1px solid color-mix(in srgb, ${statusColor} 40%, transparent)` }}>
                        {r.status}
                      </span>
                    </td>
                    <td className="py-1 px-2 text-[var(--text-primary)] truncate max-w-[200px]">{r.key.table_name}</td>
                    <td className="py-1 px-2 text-right">{fmtBytes(toNum(d.table_size))}</td>
                    <td className="py-1 px-2 text-right">{fmtNum(d.row_count)}</td>
                    <td className="py-1 px-2 text-right">{fmtNum(d.dead_tuples)}</td>
                    <td className="py-1 px-2 text-right">{typeof d.bloat_pct === 'number' ? fmtPct(d.bloat_pct) : '--'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="text-sm text-[var(--text-muted)]">No table changes detected</div>
      )}
      {unchanged.length > 0 && (
        <button onClick={() => setShowUnchanged(!showUnchanged)}
          className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] font-mono px-2 py-1 mt-2 rounded border border-[var(--border-glass)]">
          {showUnchanged ? '▼' : '▶'} {unchanged.length} unchanged tables
        </button>
      )}
    </div>
  );
}

/* ================================================================
   GENERIC KEYED TABLE SECTION (for projects, users, etc.)
   ================================================================ */

function KeyedTableSection({ title, detail, nameKey, extraColumns }: {
  title: string; detail?: CompareDatasetDetail;
  nameKey: string; extraColumns?: string[];
}) {
  const [showUnchanged, setShowUnchanged] = useState(false);

  if (!detail) return <SectionCard title={title}><LoadingPlaceholder /></SectionCard>;
  const rows = detail.rows ?? [];
  const added = rows.filter(r => r.status === 'added');
  const removed = rows.filter(r => r.status === 'removed');
  const changed = rows.filter(r => r.status === 'changed');
  const unchanged = rows.filter(r => r.status === 'unchanged');

  const hasChanges = added.length > 0 || removed.length > 0 || changed.length > 0;

  return (
    <SectionCard title={title}
      subtitle={`${rows.length} total`}
      badge={hasChanges ? <>
        {added.length > 0 && <span className="text-[10px] font-mono text-[var(--neon-green)]">+{added.length}</span>}
        {removed.length > 0 && <span className="text-[10px] font-mono text-[var(--neon-red)]">-{removed.length}</span>}
        {changed.length > 0 && <span className="text-[10px] font-mono text-[var(--neon-amber)]">~{changed.length}</span>}
      </> : undefined}>
      {!hasChanges ? (
        <div className="text-sm text-[var(--text-muted)]">No changes detected</div>
      ) : (
        <div className="space-y-1">
          {added.map((r) => {
            const d = r.run1 ?? {};
            return (
              <div key={r.key[nameKey]} className="flex items-center gap-2 text-xs rounded px-2 py-1.5 bg-[var(--neon-green)]/5">
                <span className="text-[10px] font-mono text-[var(--neon-green)] w-14">added</span>
                <span className="text-[var(--text-primary)] font-medium">{r.key[nameKey]}</span>
                {extraColumns?.map(c => <span key={c} className="text-[var(--text-muted)]">{String(d[c] || '')}</span>)}
              </div>
            );
          })}
          {removed.map((r) => {
            const d = r.run2 ?? {};
            return (
              <div key={r.key[nameKey]} className="flex items-center gap-2 text-xs rounded px-2 py-1.5 bg-[var(--neon-red)]/5">
                <span className="text-[10px] font-mono text-[var(--neon-red)] w-14">removed</span>
                <span className="text-[var(--text-primary)] font-medium">{r.key[nameKey]}</span>
                {extraColumns?.map(c => <span key={c} className="text-[var(--text-muted)]">{String(d[c] || '')}</span>)}
              </div>
            );
          })}
          {changed.map((r) => (
            <div key={r.key[nameKey]} className="flex items-center gap-2 text-xs rounded px-2 py-1.5 bg-[var(--neon-amber)]/5">
              <span className="text-[10px] font-mono text-[var(--neon-amber)] w-14">changed</span>
              <span className="text-[var(--text-primary)] font-medium">{r.key[nameKey]}</span>
              <span className="text-[var(--text-muted)]">{r.changes.join(', ')}</span>
            </div>
          ))}
        </div>
      )}
      {unchanged.length > 0 && (
        <div className="mt-2">
          <button onClick={() => setShowUnchanged(!showUnchanged)}
            className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] font-mono px-2 py-1 rounded border border-[var(--border-glass)]">
            {showUnchanged ? '▼' : '▶'} {unchanged.length} unchanged
          </button>
        </div>
      )}
    </SectionCard>
  );
}

/* ================================================================
   HEADER + SUMMARY
   ================================================================ */

function CompareHeader({ runs, run1Id, run2Id, manifest, selectRun1, selectRun2 }: {
  runs: TrendsRun[];
  run1Id: number | null;
  run2Id: number | null;
  manifest: CompareManifest | null;
  selectRun1: (id: number) => void;
  selectRun2: (id: number) => void;
}) {
  return (
    <motion.div
      className="flex flex-wrap items-center justify-between gap-3"
      initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}
    >
      <div>
        <h1 className="font-bold text-xl text-[var(--text-primary)]">Trends</h1>
        {manifest && (
          <p className="text-xs text-[var(--text-muted)]">
            Comparing {formatDate(manifest.run2.run_at)} → {formatDate(manifest.run1.run_at)}
          </p>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <span className="text-sm text-[var(--text-muted)]">Before:</span>
          <select
            className="text-sm font-mono bg-[var(--bg-surface)] border border-[var(--border-default)] rounded-lg px-3 py-2 text-[var(--text-primary)] min-w-[260px]"
            value={run2Id ?? ''}
            onChange={(e) => e.target.value && selectRun2(Number(e.target.value))}
          >
            <option value="">Select run...</option>
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                #{r.run_id} — {formatDate(r.run_at)} — Score: {r.health_score ?? '?'}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-sm text-[var(--text-muted)]">After:</span>
          <select
            className="text-sm font-mono bg-[var(--bg-surface)] border border-[var(--border-default)] rounded-lg px-3 py-2 text-[var(--text-primary)] min-w-[260px]"
            value={run1Id ?? ''}
            onChange={(e) => e.target.value && selectRun1(Number(e.target.value))}
          >
            <option value="">Select run...</option>
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                #{r.run_id} — {formatDate(r.run_at)} — Score: {r.health_score ?? '?'}
              </option>
            ))}
          </select>
        </div>
      </div>
    </motion.div>
  );
}

function SummaryBand({ manifest }: { manifest: CompareManifest }) {
  const s = manifest.summary;
  const chips: { label: string; data: CompareSummaryDelta }[] = [
    { label: 'Health', data: s.healthScore },
    { label: 'Users', data: s.userCount },
    { label: 'Projects', data: s.projectCount },
    { label: 'Plugins', data: s.pluginCount },
    { label: 'Connections', data: s.connectionCount },
    { label: 'Code Envs', data: s.codeEnvCount },
  ];

  return (
    <motion.div
      className="sticky top-0 z-10 glass-card border-[var(--border-glow)] shadow-[var(--glow-sm)] px-4 py-3"
      initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }}
    >
      <div className="flex flex-wrap gap-4 items-center">
        {chips.map(({ label, data }) => {
          const color = deltaColor(data.delta);
          return (
            <div key={label} className="flex items-center gap-2">
              <span className="text-sm text-[var(--text-muted)]">{label}</span>
              <span className="font-mono text-base font-bold text-[var(--text-primary)]">{fmtNum(data.run1)}</span>
              {data.delta !== null && data.delta !== 0 && (
                <span className="text-xs font-mono px-1.5 py-0.5 rounded-full"
                  style={{ color, border: `1px solid color-mix(in srgb, ${color} 35%, transparent)` }}>
                  {deltaSign(data.delta)}{fmtNum(data.delta)}
                </span>
              )}
            </div>
          );
        })}
      </div>
      {manifest.coverageWarnings.length > 0 && (
        <div className="mt-2 space-y-1">
          {manifest.coverageWarnings.map((w, i) => (
            <div key={i} className="text-xs text-[var(--neon-amber)] bg-[var(--neon-amber)]/5 rounded px-3 py-1.5 border border-[var(--neon-amber)]/15">
              {w.message}
            </div>
          ))}
        </div>
      )}
    </motion.div>
  );
}

/* ================================================================
   MAIN PAGE
   ================================================================ */

/** Datasets we auto-load for the section-based UI */
const AUTO_LOAD_DATASETS = [
  'run_health_metrics',   // health, system, filesystem, memory, connections, runtime, code envs, footprint
  'runs',                 // run-level summary (dss_version, python_version)
  'run_plugins',          // plugins section
  'project_snapshots',    // projects section
  'run_connection_health', // connection health section (V9)
  'run_db_health',        // DB health tables (V9)
];

export function TrendsPage() {
  const { runs, run1Id, run2Id, manifest, loading, error, selectRun1, selectRun2 } = useTrendsCompare();
  const { cache: detailCache, load: loadDetail } = useDatasetDetail(run1Id, run2Id);

  // Auto-load datasets when manifest arrives
  const manifestRef = useRef<CompareManifest | null>(null);
  useEffect(() => {
    if (manifest && manifest !== manifestRef.current) {
      manifestRef.current = manifest;
      for (const dsId of AUTO_LOAD_DATASETS) {
        const ds = manifest.datasets.find(d => d.datasetId === dsId);
        if (ds && ds.availableInRun1 && ds.availableInRun2) {
          loadDetail(dsId, 'all');
        }
      }
    }
  }, [manifest, loadDetail]);

  // Convenience accessors
  const hmDetail = detailCache['run_health_metrics'];
  const pluginDetail = detailCache['run_plugins'];
  const projectDetail = detailCache['project_snapshots'];
  const connHealthDetail = detailCache['run_connection_health'];
  const dbHealthDetail = detailCache['run_db_health'];

  // Empty state
  if (!loading && runs.length === 0 && !error) {
    return (
      <div className="w-full max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 py-4">
        <motion.div className="flex flex-col items-center justify-center py-20 rounded-xl border border-[var(--border-default)]"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
          <div className="text-4xl mb-4 opacity-40">~</div>
          <div className="text-[var(--text-secondary)] text-lg font-medium">No tracking runs yet</div>
          <div className="text-[var(--text-muted)] text-sm mt-1">Run a diagnostics collection to start building trend data.</div>
        </motion.div>
      </div>
    );
  }

  if (!loading && runs.length === 1 && !error) {
    return (
      <div className="w-full max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 py-4">
        <motion.div className="flex flex-col items-center justify-center py-20 rounded-xl border border-[var(--border-default)]"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
          <div className="text-4xl mb-4 opacity-40">1</div>
          <div className="text-[var(--text-secondary)] text-lg font-medium">Only one run available</div>
          <div className="text-[var(--text-muted)] text-sm mt-1">Run another diagnostics collection to enable comparison.</div>
        </motion.div>
      </div>
    );
  }

  return (
    <div className="w-full max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 py-4 space-y-4">
      <CompareHeader runs={runs} run1Id={run1Id} run2Id={run2Id}
        manifest={manifest} selectRun1={selectRun1} selectRun2={selectRun2} />

      {error && (
        <motion.div className="text-sm text-[var(--neon-red)] bg-[var(--neon-red)]/10 rounded-lg px-4 py-3 border border-[var(--neon-red)]/20"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
          {error}
        </motion.div>
      )}

      {loading && (
        <div className="space-y-4">
          {[1, 2, 3].map(i => (
            <div key={i} className="glass-card animate-pulse px-4 py-6">
              <div className="h-4 w-32 rounded bg-[var(--bg-glass-hover)] mb-3" />
              <div className="h-20 rounded bg-[var(--bg-glass-hover)]" />
            </div>
          ))}
        </div>
      )}

      {manifest && (
        <>
          {/* Summary band */}
          <SummaryBand manifest={manifest} />

          {/* ── OVERVIEW ── */}
          <SectionDivider label="Overview" />
          <TrendsHealthSection manifest={manifest} detail={hmDetail} />

          {/* ── SYSTEM ── */}
          <SectionDivider label="System" />
          <TrendsSystemSection manifest={manifest} detail={hmDetail} />
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <TrendsFilesystemSection detail={hmDetail} />
            <TrendsMemorySection detail={hmDetail} />
          </div>

          {/* ── MONITORING ── */}
          <SectionDivider label="Monitoring" />
          <TrendsConnectionsSection detail={hmDetail} connHealthDetail={connHealthDetail} />
          <TrendsRuntimeSection detail={hmDetail} />
          <TrendsPluginsSection detail={pluginDetail} />

          {/* ── PROJECTS ── */}
          <SectionDivider label="Projects" />
          <TrendsProjectsSection projectDetail={projectDetail} footprintDetail={hmDetail} />

          {/* ── CODE ENVIRONMENTS ── */}
          <SectionDivider label="Code Environments" />
          <TrendsCodeEnvsSection detail={hmDetail} />

          {/* ── DB HEALTH ── */}
          <SectionDivider label="DB Health" />
          <TrendsDbHealthSection summaryDetail={hmDetail} tableDetail={dbHealthDetail} />

          {/* Footer */}
          <motion.div className="text-xs text-[var(--text-muted)] text-center pt-4 pb-2"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.5 }}>
            Comparing run #{manifest.run2.run_id} ({formatDate(manifest.run2.run_at)})
            → run #{manifest.run1.run_id} ({formatDate(manifest.run1.run_at)})
          </motion.div>
        </>
      )}
    </div>
  );
}
