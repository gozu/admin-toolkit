import { memo, useEffect, useMemo, useSyncExternalStore } from 'react';
import type { HealthScore, Lifecycle, LlmAuditSummary, PageId } from '../../../types';
import type { ConnectionInsightsRow } from '../../../utils/connectionInsights';
import { formatAuto, formatSizeGb } from '../../../utils/formatters';
import { containerExecsScan } from '../../../state/containerExecsStore';
import { codeEnvComparisonScan } from '../../../state/codeEnvComparisonStore';
import { dbHealthConnectionsStore } from '../../../state/dbHealthConnectionsStore';
import { k8sInsightsScan } from '../../../state/k8sInsightsStore';
import {
  getProcessMetrics,
  startProcessMetricsScan,
  subscribeProcessMetrics,
} from '../../../state/processMetrics';
import { getSqlPushdownScan, subscribeSqlPushdownScan } from '../../../state/sqlPushdownScan';
import {
  BarRow,
  BigStat,
  CountChip,
  Dot,
  HeatStrip,
  MicroDonut,
  MiniTreemap,
  ScoreRing,
  SegmentBar,
  UsageBar,
  type TreemapItem,
} from './microViz';
import { CATEGORICAL_COLORS, type Tone } from './tokens';
import { TileShell } from './TileShell';
import {
  footprintTone,
  selectTopCpu,
  type CodeEnvsVm,
  type ConnHealthVm,
  type ConnTypesVm,
  type ConnUsageVm,
  type MemoryVm,
  type MountVm,
  type ProjectsVm,
  type SanityVm,
  type UsersVm,
} from './selectors';

// One component per wall tile, ~selector → microviz inside a TileShell.
// Streaming stores are subscribed *inside* their tile so an SSE tick repaints
// one tile, not the whole wall.

interface BaseTileProps {
  lifecycle: Lifecycle;
  onNavigate: (page: PageId) => void;
}

const HEALTH_STATUS_COLOR: Record<HealthScore['status'], string> = {
  healthy: 'var(--neon-green)',
  warning: 'var(--neon-amber)',
  critical: 'var(--neon-red)',
};

const SEVERITY_TONE: Record<string, Tone> = {
  critical: 'crit',
  warning: 'warn',
  info: 'info',
  good: 'ok',
};

// ── HEALTH (3×2) ──────────────────────────────────────────────────────────
export const HealthTile = memo(function HealthTile({
  lifecycle,
  onNavigate,
  health,
  dssVersion,
  lastRestartTime,
}: BaseTileProps & {
  health: HealthScore;
  dssVersion?: string;
  lastRestartTime?: string;
}) {
  const topIssues = health.issues
    .filter((i) => i.severity === 'critical' || i.severity === 'warning')
    .slice(0, 3);
  return (
    <TileShell title="Health" area="health" target="summary" accent="system" lifecycle={lifecycle} onNavigate={onNavigate}>
      <div className="flex h-full min-h-0 flex-col gap-1.5">
        <div className="flex min-h-0 flex-1 items-center gap-3">
          <ScoreRing
            score={health.overall}
            color={HEALTH_STATUS_COLOR[health.status]}
            label={health.status}
          />
          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            <div className="flex flex-wrap gap-1.5">
              <CountChip label="crit" count={health.criticalCount} tone={health.criticalCount ? 'crit' : 'neutral'} />
              <CountChip label="warn" count={health.warningCount} tone={health.warningCount ? 'warn' : 'neutral'} />
              <CountChip label="info" count={health.infoCount} tone="neutral" />
            </div>
            <div className="flex min-h-0 flex-col gap-1 overflow-hidden">
              {topIssues.map((issue) => (
                <div key={issue.id} className="flex min-w-0 items-center gap-1.5">
                  <Dot tone={SEVERITY_TONE[issue.severity] ?? 'neutral'} />
                  <span className="truncate text-[11px] text-[var(--text-secondary)]">{issue.title}</span>
                </div>
              ))}
              {topIssues.length === 0 && (
                <span className="text-[11px] text-[var(--text-tertiary)]">No critical or warning issues</span>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center justify-between gap-2 font-mono text-[10px] text-[var(--text-tertiary)]">
          <span className="truncate">DSS {dssVersion ?? '—'}</span>
          {lastRestartTime && <span className="truncate">up since {lastRestartTime}</span>}
        </div>
      </div>
    </TileShell>
  );
});

// ── FILESYSTEM (3×2) ──────────────────────────────────────────────────────
export const FilesystemTile = memo(function FilesystemTile({
  lifecycle,
  onNavigate,
  mounts,
  treemap,
}: BaseTileProps & { mounts: MountVm[]; treemap: TreemapItem[] }) {
  return (
    <TileShell title="Filesystem" area="fs" target="filesystem" accent="system" lifecycle={lifecycle} onNavigate={onNavigate} hasData={mounts.length > 0 || treemap.length > 0}>
      <div className="flex h-full min-h-0 flex-col gap-1.5">
        <div className="flex flex-col gap-1">
          {mounts.map((m) => (
            <div key={m.mount} className="flex items-center gap-2">
              <span className="w-24 flex-shrink-0 truncate font-mono text-[10px] text-[var(--text-secondary)]">{m.mount}</span>
              <div className="min-w-0 flex-1">
                <UsageBar pct={m.usePct} tone={m.usePct > 90 ? 'crit' : m.usePct > 75 ? 'warn' : 'info'} />
              </div>
              <span className="w-20 flex-shrink-0 text-right font-mono text-[9px] text-[var(--text-tertiary)]">
                {m.usePct}% of {m.size}
              </span>
            </div>
          ))}
        </div>
        <div className="min-h-0 flex-1">
          {treemap.length > 0 ? (
            <MiniTreemap items={treemap} />
          ) : (
            <span className="text-[10px] italic text-[var(--text-tertiary)]">directory scan pending…</span>
          )}
        </div>
      </div>
    </TileShell>
  );
});

// ── MEMORY (2×1) ──────────────────────────────────────────────────────────
export const MemoryTile = memo(function MemoryTile({
  lifecycle,
  onNavigate,
  mem,
}: BaseTileProps & { mem: MemoryVm | null }) {
  return (
    <TileShell title="Memory" area="mem" target="memory" accent="system" lifecycle={lifecycle} onNavigate={onNavigate} hasData={mem != null}>
      {mem ? (
        <div className="flex h-full flex-col justify-center gap-1.5">
          <BigStat
            value={mem.usedLabel}
            sub={`/ ${mem.totalLabel}`}
            label="memory used"
            tone={mem.usedPct > 90 ? 'crit' : undefined}
          />
          <SegmentBar
            segments={[
              { value: mem.usedMb, color: 'var(--accent)', title: `used ${mem.usedLabel}` },
              { value: mem.buffMb, color: 'var(--neon-amber-dim)', title: 'buff/cache' },
              { value: mem.freeMb, color: 'transparent', title: 'free' },
            ]}
          />
          {mem.swapLabel && (
            <span className="font-mono text-[9px] text-[var(--text-tertiary)]">swap {mem.swapLabel}</span>
          )}
        </div>
      ) : (
        <span className="text-[11px] text-[var(--text-tertiary)]">No data</span>
      )}
    </TileShell>
  );
});

// ── CPU (2×1) ─────────────────────────────────────────────────────────────
export const CpuTile = memo(function CpuTile({
  lifecycle,
  onNavigate,
  cores,
}: BaseTileProps & { cores?: string }) {
  // Idempotent kick of the shared process-metrics scan (the CPU page's own
  // bootstrap pattern); the warmup queue usually got there first.
  useEffect(() => {
    startProcessMetricsScan();
  }, []);
  const state = useSyncExternalStore(subscribeProcessMetrics, getProcessMetrics, getProcessMetrics);
  const top = useMemo(() => selectTopCpu(state.processes), [state.processes]);
  return (
    <TileShell title="CPU" area="cpu" target="cpu" accent="system" lifecycle={lifecycle} onNavigate={onNavigate} hasData={top.length > 0}
      titleRight={cores ? <span className="font-mono text-[9px] text-[var(--text-tertiary)]">{cores} cores</span> : undefined}
    >
      <div className="flex h-full flex-col justify-center gap-1">
        {top.map((p, i) => (
          <BarRow key={`${p.label}-${i}`} label={p.label} value={`${p.cpu.toFixed(1)}%`} pct={p.cpu} tone={p.cpu > 80 ? 'warn' : 'info'} />
        ))}
        {top.length === 0 && <span className="text-[11px] text-[var(--text-tertiary)]">No process data</span>}
      </div>
    </TileShell>
  );
});

// ── CONNECTIONS INVENTORY (2×2) ───────────────────────────────────────────
export const ConnInventoryTile = memo(function ConnInventoryTile({
  lifecycle,
  onNavigate,
  vm,
  onTypeClick,
}: BaseTileProps & { vm: ConnTypesVm; onTypeClick: (type: string) => void }) {
  const segments = vm.entries.map(([type, count], i) => ({
    value: count,
    color: CATEGORICAL_COLORS[i % CATEGORICAL_COLORS.length],
    title: `${type}: ${count}`,
  }));
  if (vm.otherCount > 0) {
    segments.push({ value: vm.otherCount, color: 'var(--text-tertiary)', title: `other: ${vm.otherCount}` });
  }
  return (
    <TileShell title="Connections" area="coninv" target="connections-inventory" accent="connections" lifecycle={lifecycle} onNavigate={onNavigate} hasData={vm.total > 0}>
      <div className="flex h-full min-h-0 flex-col items-center justify-center gap-2">
        <MicroDonut segments={segments} center={vm.total} centerLabel="conns" size={78} />
        <div className="flex w-full min-w-0 flex-col gap-0.5">
          {vm.entries.map(([type, count], i) => (
            <button
              key={type}
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onTypeClick(type);
              }}
              className="-mx-0.5 flex min-w-0 items-center gap-1.5 rounded px-0.5 transition-colors hover:bg-[var(--bg-hover)]"
            >
              <span
                className="h-1.5 w-1.5 flex-shrink-0 rounded-full"
                style={{ background: CATEGORICAL_COLORS[i % CATEGORICAL_COLORS.length] }}
              />
              <span className="min-w-0 flex-1 truncate text-left text-[10px] text-[var(--text-secondary)]">{type}</span>
              <span className="font-mono text-[10px] text-[var(--text-primary)]">{count}</span>
            </button>
          ))}
          {vm.otherCount > 0 && (
            <div className="flex items-center gap-1.5 px-0.5 text-[10px] text-[var(--text-tertiary)]">
              <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-[var(--text-tertiary)] opacity-40" />
              <span className="flex-1 text-left">other</span>
              <span className="font-mono">{vm.otherCount}</span>
            </div>
          )}
        </div>
      </div>
    </TileShell>
  );
});

// ── CONNECTIONS HEALTH (2×1) ──────────────────────────────────────────────
export const ConnHealthTile = memo(function ConnHealthTile({
  lifecycle,
  onNavigate,
  vm,
}: BaseTileProps & { vm: ConnHealthVm }) {
  return (
    <TileShell title="Conn Health" area="conhlt" target="connections-health" accent="connections" lifecycle={lifecycle} onNavigate={onNavigate} hasData={vm.cells.length > 0}>
      <div className="flex h-full flex-col justify-center gap-1.5">
        <div className="flex flex-wrap gap-1.5">
          <CountChip label="ok" count={vm.ok} tone={vm.ok ? 'ok' : 'neutral'} />
          <CountChip label="fail" count={vm.fail} tone={vm.fail ? 'crit' : 'neutral'} />
          <CountChip label="skip" count={vm.skipped} tone="neutral" />
        </div>
        <HeatStrip cells={vm.cells} max={22} />
      </div>
    </TileShell>
  );
});

// ── CONNECTIONS USAGE (2×1) ───────────────────────────────────────────────
export const ConnUsageTile = memo(function ConnUsageTile({
  lifecycle,
  onNavigate,
  vm,
}: BaseTileProps & { vm: ConnUsageVm }) {
  return (
    <TileShell title="Conn Usage" area="conuse" target="connections-usage" accent="connections" lifecycle={lifecycle} onNavigate={onNavigate} hasData={(vm.scanned ?? 0) > 0}>
      <div className="flex h-full flex-col justify-center gap-1.5">
        <div className="flex items-end gap-4">
          <BigStat value={vm.datasetCount} label="datasets" />
          <BigStat value={vm.llmRecipeCount} label="llm recipes" />
        </div>
        {vm.total != null && vm.total > 0 && (
          <div className="flex items-center gap-2">
            <div className="min-w-0 flex-1">
              <UsageBar pct={vm.coveragePct} tone="info" />
            </div>
            <span className="flex-shrink-0 font-mono text-[9px] text-[var(--text-tertiary)]">
              {vm.scanned ?? 0}/{vm.total} scanned
            </span>
          </div>
        )}
      </div>
    </TileShell>
  );
});

// ── PROJECTS (4×2) ────────────────────────────────────────────────────────
export const ProjectsTile = memo(function ProjectsTile({
  lifecycle,
  onNavigate,
  vm,
}: BaseTileProps & { vm: ProjectsVm }) {
  return (
    <TileShell title="Projects" area="proj" target="projects" accent="projects" lifecycle={lifecycle} onNavigate={onNavigate} hasData={vm.count > 0}>
      <div className="flex h-full min-h-0 flex-col gap-2">
        <div className="flex items-end gap-5">
          <BigStat value={vm.count} label="projects" />
          <BigStat value={formatAuto(vm.totalBytes)} label="total footprint" />
          <BigStat value={`${vm.avgGb.toFixed(1)} GB`} label="avg / project" />
        </div>
        <div className="flex min-h-0 flex-1 flex-col justify-evenly gap-1">
          {vm.top.map((r) => (
            <BarRow
              key={r.projectKey}
              label={r.projectKey}
              value={formatSizeGb(r.totalBytes)}
              pct={vm.maxBytes ? ((r.totalBytes || 0) / vm.maxBytes) * 100 : 0}
              tone={footprintTone(r.projectSizeHealth)}
            />
          ))}
        </div>
      </div>
    </TileShell>
  );
});

// ── USERS (3×2) ───────────────────────────────────────────────────────────
export const UsersTile = memo(function UsersTile({
  lifecycle,
  onNavigate,
  vm,
  onOwnerClick,
}: BaseTileProps & { vm: UsersVm; onOwnerClick: (login: string) => void }) {
  const maxOwner = vm.topOwners[0]?.[1] || 1;
  return (
    <TileShell title="Users" area="users" target="users" accent="projects" lifecycle={lifecycle} onNavigate={onNavigate} hasData={vm.total > 0}>
      <div className="flex h-full min-h-0 flex-col gap-1.5">
        <div className="flex items-end gap-4">
          <BigStat value={vm.total} label="users" />
          <BigStat value={vm.enabled} label="enabled" />
        </div>
        <SegmentBar
          segments={vm.profiles.map(([profile, count], i) => ({
            value: count,
            color: CATEGORICAL_COLORS[i % CATEGORICAL_COLORS.length],
            title: `${profile}: ${count}`,
          }))}
        />
        <div className="flex flex-wrap gap-1">
          {vm.profiles.slice(0, 3).map(([profile, count]) => (
            <CountChip key={profile} label={profile.toLowerCase().replace(/_/g, ' ')} count={count} tone="neutral" />
          ))}
        </div>
        <div className="flex min-h-0 flex-1 flex-col justify-evenly gap-1">
          {vm.topOwners.length > 0 && (
            <span className="text-[9px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">top owners</span>
          )}
          {vm.topOwners.map(([login, count]) => (
            <BarRow
              key={login}
              label={login}
              value={`${count}`}
              pct={(count / maxOwner) * 100}
              tone="ok"
              onClick={() => onOwnerClick(login)}
            />
          ))}
        </div>
      </div>
    </TileShell>
  );
});

// ── CONNECTION INSIGHTS (3×2) ─────────────────────────────────────────────
export const ConnInsightsTile = memo(function ConnInsightsTile({
  lifecycle,
  onNavigate,
  rows,
}: BaseTileProps & { rows: ConnectionInsightsRow[] }) {
  const top = useMemo(
    () =>
      [...rows]
        .sort((a, b) => b.projectCount - a.projectCount || b.datasetCount - a.datasetCount)
        .slice(0, 6),
    [rows],
  );
  const max = top[0]?.projectCount || 1;
  const auditTone = (r: ConnectionInsightsRow): Tone =>
    r.auditSeverity === 'critical' ? 'crit' : r.auditSeverity === 'warning' ? 'warn' : r.auditSeverity === 'info' ? 'info' : 'neutral';
  const healthTone = (r: ConnectionInsightsRow): Tone =>
    r.healthStatus === 'ok' ? 'ok' : r.healthStatus === 'fail' ? 'crit' : 'neutral';
  return (
    <TileShell
      title="Conn Insights"
      area="conins"
      target="connections-insights"
      accent="connections"
      lifecycle={lifecycle}
      onNavigate={onNavigate}
      hasData={rows.length > 0}
      titleRight={<span className="font-mono text-[9px] text-[var(--text-tertiary)]">audit · health</span>}
    >
      <div className="flex h-full min-h-0 flex-col justify-center gap-1.5">
        {top.map((r) => (
          <div key={r.name} className="flex min-w-0 items-center gap-1.5">
            <span className="w-24 flex-shrink-0 truncate text-[10px] text-[var(--text-secondary)]">{r.name}</span>
            <div className="min-w-0 flex-1">
              <UsageBar pct={(r.projectCount / max) * 100} tone="info" />
            </div>
            <span className="w-6 flex-shrink-0 text-right font-mono text-[9px] text-[var(--text-tertiary)]">
              {r.projectCount}
            </span>
            <Dot tone={auditTone(r)} title={r.auditSeverity ? `audit: ${r.auditSeverity}` : 'audit: clean'} />
            <Dot tone={healthTone(r)} title={r.healthStatus ? `health: ${r.healthStatus}` : 'health: untested'} />
          </div>
        ))}
        {top.length === 0 && <span className="text-[11px] text-[var(--text-tertiary)]">No connections</span>}
      </div>
    </TileShell>
  );
});

// ── PLUGINS (2×1) ─────────────────────────────────────────────────────────
export const PluginsTile = memo(function PluginsTile({
  lifecycle,
  onNavigate,
  count,
  pending,
}: BaseTileProps & { count: number; pending: boolean }) {
  return (
    <TileShell title="Plugins" area="plug" target="plugins-installed" accent="hygiene" lifecycle={lifecycle} onNavigate={onNavigate} hasData={count > 0}>
      <div className="flex h-full items-center justify-between gap-2">
        <BigStat value={count} label="installed" />
        {pending && <CountChip label="usage scan" count="…" tone="warn" pulse />}
      </div>
    </TileShell>
  );
});

// ── CODE ENVS (2×1) ───────────────────────────────────────────────────────
export const CodeEnvsTile = memo(function CodeEnvsTile({
  lifecycle,
  onNavigate,
  vm,
}: BaseTileProps & { vm: CodeEnvsVm }) {
  return (
    <TileShell title="Code Envs" area="cenv" target="code-envs-cleaner" accent="hygiene" lifecycle={lifecycle} onNavigate={onNavigate} hasData={vm.count > 0}>
      <div className="flex h-full flex-col justify-center gap-1.5">
        <BigStat
          value={vm.count}
          label="code envs"
          sub={vm.totalSizeBytes > 0 ? formatAuto(vm.totalSizeBytes) : undefined}
        />
        <div className="flex flex-wrap gap-1">
          {vm.pyVersions.map(([version, count]) => (
            <CountChip key={version} label={version} count={count} tone="info" />
          ))}
        </div>
      </div>
    </TileShell>
  );
});

// ── K8S INSIGHTS (3×1) ────────────────────────────────────────────────────
export const K8sTile = memo(function K8sTile({ lifecycle, onNavigate }: BaseTileProps) {
  const { data } = k8sInsightsScan.use();
  return (
    <TileShell
      title="K8s Insights"
      area="k8s"
      target="k8s-insights"
      accent="compute"
      lifecycle={lifecycle}
      onNavigate={onNavigate}
      idleText="No scan yet — open to scan"
      hasData={data != null}
    >
      {data && data.ok ? (
        <div className="flex h-full items-center gap-5">
          <BigStat value={data.findingsCount} label="findings" tone={data.findingsCount ? 'warn' : 'ok'} />
          <BigStat value={data.podSummary?.total ?? 0} label="pods" />
          {data.costSnapshot?.currentMonthly != null && (
            <BigStat value={`$${Math.round(data.costSnapshot.currentMonthly)}`} label="est / month" />
          )}
        </div>
      ) : data ? (
        <div className="flex h-full items-center text-[11px] text-[var(--neon-red)]">
          <span className="line-clamp-2">{data.error || 'Scan failed'}</span>
        </div>
      ) : (
        <span className="text-[11px] text-[var(--text-tertiary)]">No data</span>
      )}
    </TileShell>
  );
});

// ── CONTAINER EXECS (3×1) ─────────────────────────────────────────────────
export const ContainerExecsTile = memo(function ContainerExecsTile({ lifecycle, onNavigate }: BaseTileProps) {
  const { data } = containerExecsScan.use();
  const summary = data?.summary;
  const topModes = useMemo(
    () => Object.entries(summary?.byMode || {}).sort((a, b) => b[1] - a[1]).slice(0, 2),
    [summary],
  );
  return (
    <TileShell title="Container Execs" area="cex" target="container-execs" accent="compute" lifecycle={lifecycle} onNavigate={onNavigate} hasData={summary != null}>
      {summary ? (
        <div className="flex h-full min-w-0 items-center gap-4">
          <BigStat value={summary.configCount} label="configs" />
          <BigStat value={summary.usageCount} label="usages" />
          <div className="flex min-w-0 flex-1 flex-wrap justify-end gap-1">
            {topModes.map(([mode, count]) => (
              <CountChip key={mode} label={mode.toLowerCase()} count={count} tone="neutral" />
            ))}
          </div>
        </div>
      ) : (
        <span className="text-[11px] text-[var(--text-tertiary)]">No data</span>
      )}
    </TileShell>
  );
});

// ── LLM AUDIT (3×1) ───────────────────────────────────────────────────────
export const LlmAuditTile = memo(function LlmAuditTile({
  lifecycle,
  onNavigate,
  summary,
}: BaseTileProps & { summary: LlmAuditSummary | undefined }) {
  const obsolete = summary?.countsByStatus?.obsolete ?? 0;
  const ripoff = summary?.countsByStatus?.ripoff ?? 0;
  return (
    <TileShell title="Model Audit" area="llm" target="llm-audit" accent="compute" lifecycle={lifecycle} onNavigate={onNavigate} hasData={summary != null}>
      {summary ? (
        <div className="flex h-full items-center gap-4">
          <BigStat value={summary.llmsTotal} label="llms in use" />
          <div className="flex flex-wrap gap-1.5">
            <CountChip label="obsolete" count={obsolete} tone={obsolete ? 'warn' : 'neutral'} />
            <CountChip label="ripoff" count={ripoff} tone={ripoff ? 'crit' : 'neutral'} />
          </div>
        </div>
      ) : (
        <span className="text-[11px] text-[var(--text-tertiary)]">No data</span>
      )}
    </TileShell>
  );
});

// ── PROJECT COMPUTE / SQL PUSHDOWN (3×1) ──────────────────────────────────
export const ProjectComputeTile = memo(function ProjectComputeTile({ lifecycle, onNavigate }: BaseTileProps) {
  const state = useSyncExternalStore(subscribeSqlPushdownScan, getSqlPushdownScan, getSqlPushdownScan);
  const recipes = useMemo(
    () => state.ownerGroups.reduce((s, g) => s + g.totalRecipes, 0),
    [state.ownerGroups],
  );
  return (
    <TileShell title="Project Compute" area="pcomp" target="project-compute" accent="compute" lifecycle={lifecycle} onNavigate={onNavigate} hasData={recipes > 0}>
      <div className="flex h-full min-w-0 items-center gap-4">
        <BigStat value={recipes} label="pushdown findings" tone={recipes ? 'warn' : 'ok'} />
        <BigStat value={state.ownerGroups.length} label="owners" />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <UsageBar pct={state.total ? ((state.scanned ?? 0) / state.total) * 100 : 0} tone="info" />
          <span className="text-right font-mono text-[9px] text-[var(--text-tertiary)]">
            {state.scanned ?? 0}/{state.total ?? '—'} projects
          </span>
        </div>
      </div>
    </TileShell>
  );
});

// ── LOG ERRORS (3×1) ──────────────────────────────────────────────────────
export const LogsTile = memo(function LogsTile({
  lifecycle,
  onNavigate,
  unique,
  snippet,
}: BaseTileProps & { unique: number; snippet?: string }) {
  return (
    <TileShell title="Log Errors" area="logs" target="logs" accent="hygiene" lifecycle={lifecycle} onNavigate={onNavigate} hasData={unique > 0 || snippet != null}>
      <div className="flex h-full min-w-0 items-center gap-4">
        <BigStat value={unique} label="unique errors" tone={unique > 0 ? 'warn' : 'ok'} />
        {snippet && (
          <code className="line-clamp-3 min-w-0 flex-1 font-mono text-[9px] leading-snug text-[var(--text-tertiary)]">
            {snippet}
          </code>
        )}
      </div>
    </TileShell>
  );
});

// ── SANITY CHECK (3×1) ────────────────────────────────────────────────────
export const SanityTile = memo(function SanityTile({
  lifecycle,
  onNavigate,
  vm,
}: BaseTileProps & { vm: SanityVm }) {
  return (
    <TileShell title="Sanity Check" area="sanity" target="sanity-check" accent="hygiene" lifecycle={lifecycle} onNavigate={onNavigate} hasData={vm.total > 0}>
      <div className="flex h-full items-center gap-4">
        <BigStat value={vm.total} label="messages" tone={vm.maxTone} />
        <div className="flex flex-wrap gap-1.5">
          <CountChip label="err" count={vm.errors} tone={vm.errors ? 'crit' : 'neutral'} />
          <CountChip label="warn" count={vm.warnings} tone={vm.warnings ? 'warn' : 'neutral'} />
          <CountChip label="info" count={vm.infos} tone="neutral" />
        </div>
      </div>
    </TileShell>
  );
});

// ── DB HEALTH (3×1) ───────────────────────────────────────────────────────
export const DbHealthTile = memo(function DbHealthTile({ lifecycle, onNavigate }: BaseTileProps) {
  const state = dbHealthConnectionsStore.use();
  const configured = state.configuredConnection;
  const detail = configured ? state.detailsByConnection[configured] : undefined;
  return (
    <TileShell
      title="DB Health"
      area="dbh"
      target="db-health"
      accent="hygiene"
      lifecycle={lifecycle}
      onNavigate={onNavigate}
      idleText="Not loaded yet — open to inspect"
      emptyText="No Postgres connection configured"
      hasData={configured != null}
    >
      <div className="flex h-full min-w-0 items-center gap-4">
        <BigStat value={state.connections.length} label="pg connections" />
        {detail?.overview && (
          <>
            <BigStat value={detail.overview.dbSize} label={configured ?? 'runtime db'} />
            <BigStat value={detail.overview.tableCount} label="tables" />
          </>
        )}
      </div>
    </TileShell>
  );
});

// ── CODE ENV COMPARISON (3×1) ─────────────────────────────────────────────
export const EnvCompareTile = memo(function EnvCompareTile({
  lifecycle,
  onNavigate,
  skippedEnvCount,
}: BaseTileProps & { skippedEnvCount?: number }) {
  const { data } = codeEnvComparisonScan.use();
  return (
    <TileShell title="Env Comparison" area="envcmp" target="code-envs-comparison" accent="hygiene" lifecycle={lifecycle} onNavigate={onNavigate} hasData={data != null}>
      {data ? (
        <div className="flex h-full min-w-0 items-center gap-4">
          <BigStat value={data.analyzedCount} label="envs compared" />
          <div className="flex min-w-0 flex-wrap gap-1.5">
            <CountChip label="identical" count={data.green.length} tone={data.green.length ? 'warn' : 'neutral'} />
            <CountChip label="near-dup" count={data.blue.length} tone={data.blue.length ? 'info' : 'neutral'} />
            <CountChip label="py mix" count={data.purple.length} tone="neutral" />
            {skippedEnvCount != null && skippedEnvCount > 0 && (
              <CountChip label="skipped" count={skippedEnvCount} tone="neutral" />
            )}
          </div>
        </div>
      ) : (
        <span className="text-[11px] text-[var(--text-tertiary)]">No data</span>
      )}
    </TileShell>
  );
});
