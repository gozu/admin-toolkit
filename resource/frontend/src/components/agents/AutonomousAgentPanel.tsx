import { useEffect, useRef } from 'react';
import {
  loadTriageSettings,
  provisionTriageSchedule,
  sendTestDigest,
  triageSettingsStore,
  updateTriageSettings,
  type TriageActionRow,
} from '../../state/triageSettingsStore';
import { Button } from '../common/Button';
import { Spinner } from '../common/Spinner';

/**
 * Permissions → "Autonomous daily agent" — the 24h triage sweep's capability
 * panel. Everything the agent may do WITHOUT a human in the loop is granted
 * here: per-action opt-ins over the auto-eligible catalog, one master switch
 * that pauses the whole tier (selection preserved), safety caps, remote-host
 * scope, schedule status and the branded test report.
 */

const RISK_DOT: Record<string, string> = {
  high: 'bg-[var(--danger)]',
  medium: 'bg-[var(--neon-amber)]',
  low: 'bg-[var(--accent)]',
};

function fmtLastRun(lastRun: { outcome: string | null; start: number | null } | null): string {
  if (!lastRun || !lastRun.start) return 'never ran';
  const d = new Date(lastRun.start);
  const when = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  const time = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  return `${(lastRun.outcome || 'ran').toLowerCase()} · ${when} ${time}`;
}

function StatusChip({
  label,
  tone,
}: {
  label: string;
  tone: 'ok' | 'warn' | 'muted';
}) {
  const cls =
    tone === 'ok'
      ? 'border-[var(--accent)]/40 text-[var(--accent)]'
      : tone === 'warn'
        ? 'border-[var(--neon-amber)]/50 text-[var(--neon-amber)]'
        : 'border-[var(--border-default)] text-[var(--text-muted)]';
  return (
    <span className={`rounded-full border bg-[var(--bg-surface)]/60 px-2 py-0.5 text-[10px] font-medium ${cls}`}>
      {label}
    </span>
  );
}

function AutoActionRow({
  row,
  saving,
  disabled,
  onToggle,
}: {
  row: TriageActionRow;
  saving: boolean;
  disabled: boolean;
  onToggle: (enabled: boolean) => void;
}) {
  return (
    <label
      className={`flex items-start gap-3 rounded-lg border border-transparent px-3 py-2 transition-colors hover:bg-[var(--bg-hover)] ${
        saving ? 'opacity-60' : 'cursor-pointer'
      } ${disabled ? 'opacity-50' : ''}`}
    >
      <input
        type="checkbox"
        checked={row.optedIn}
        disabled={saving}
        onChange={(e) => onToggle(e.target.checked)}
        className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--accent)]"
      />
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`h-2 w-2 shrink-0 rounded-full ${RISK_DOT[row.risk] ?? RISK_DOT.medium}`} />
          <code className="text-xs font-semibold text-[var(--text-primary)]">{row.action}</code>
          {row.optedIn && !row.gateEnabled && (
            <span className="text-[10px] font-medium uppercase tracking-wide text-[var(--neon-amber)]">
              blocked — action disabled above
            </span>
          )}
          {row.localOnly && (
            <span className="rounded border border-[var(--border-default)] bg-[var(--bg-surface)] px-1.5 py-0.5 text-[10px] text-[var(--text-muted)]">
              local-only
            </span>
          )}
        </div>
        <p className="text-[11px] leading-relaxed text-[var(--text-muted)] break-words">
          {row.description}
          <span className="text-[var(--text-tertiary)]"> Triggers: {row.findings.join(', ')}.</span>
        </p>
      </div>
    </label>
  );
}

export function AutonomousAgentPanel({
  requireUnlock,
}: {
  requireUnlock: (apply: () => void) => void;
}) {
  const { data, loading, loaded, saving, testSending, provisioning, error } =
    triageSettingsStore.use();
  const masterRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!loaded) void loadTriageSettings();
  }, [loaded]);

  const optedCount = data?.actions.filter((a) => a.optedIn).length ?? 0;
  const total = data?.actions.length ?? 0;
  const active = Boolean(data?.enabled) && optedCount > 0;

  useEffect(() => {
    if (masterRef.current)
      masterRef.current.indeterminate = Boolean(data?.enabled) && optedCount === 0;
  }, [data?.enabled, optedCount]);

  if (loading && !loaded) {
    return (
      <section className="glass-card flex items-center justify-center p-8">
        <Spinner size="w-5 h-5" color="border-[var(--accent)]" />
      </section>
    );
  }
  if (!data) {
    return error ? (
      <section className="glass-card p-3 border-l-2 border-l-[var(--danger)]">
        <p className="text-xs text-[var(--danger)]">{error}</p>
      </section>
    ) : null;
  }

  const apply = (update: Parameters<typeof updateTriageSettings>[0], tag: string) =>
    requireUnlock(() => void updateTriageSettings(update, tag).catch(() => undefined));

  const scenario = data.scenario;
  const scheduleChip: { label: string; tone: 'ok' | 'warn' | 'muted' } = !scenario.provisioned
    ? { label: 'no schedule', tone: 'warn' }
    : !scenario.active
      ? { label: 'schedule inactive', tone: 'warn' }
      : {
          label: `daily ${String(scenario.hour ?? 7).padStart(2, '0')}:00`,
          tone: 'ok',
        };

  return (
    <section className="glass-card p-4 space-y-3 border-l-2 border-l-[var(--accent)]">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-[var(--text-primary)]">Autonomous daily agent</h3>
        <span
          className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
            active
              ? 'bg-[var(--accent)]/15 text-[var(--accent)]'
              : 'bg-[var(--bg-surface)] text-[var(--text-muted)]'
          }`}
        >
          {active ? 'active' : data.enabled ? 'idle — nothing opted in' : 'paused'}
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <StatusChip label={scheduleChip.label} tone={scheduleChip.tone} />
          <StatusChip
            label={fmtLastRun(scenario.lastRun)}
            tone={scenario.lastRun?.outcome === 'SUCCESS' ? 'ok' : 'muted'}
          />
          {!data.killSwitch && <StatusChip label="kill-switch off" tone="warn" />}
          {!data.masterPassword && <StatusChip label="no master password" tone="warn" />}
        </div>
      </div>

      <p className="text-xs text-[var(--text-muted)] leading-relaxed">
        Every night the triage agent scores the whole fleet, emails the branded health report, and
        — only for the actions you grant below — fixes findings on its own through the same
        plan → confirm-token → audit pipeline as a human-approved action.
        {data.delivery.recipient ? (
          <>
            {' '}
            Reports go to <strong className="text-[var(--text-secondary)]">{data.delivery.recipient}</strong>.
          </>
        ) : (
          <span className="text-[var(--neon-amber)]"> No digest recipient is configured yet — set one in Settings → Agents &amp; Outreach.</span>
        )}
      </p>

      {/* master switch + bulk controls */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-[var(--border-default)]/60 bg-[var(--bg-surface)]/60 px-3 py-2">
        <label className={`flex items-center gap-2.5 ${saving === '__master__' ? 'opacity-60' : 'cursor-pointer'}`}>
          <input
            ref={masterRef}
            type="checkbox"
            checked={data.enabled}
            disabled={saving === '__master__'}
            onChange={(e) => apply({ enabled: e.target.checked }, '__master__')}
            className="h-4 w-4 accent-[var(--accent)]"
          />
          <span className="text-xs font-semibold text-[var(--text-primary)]">
            Autonomous actions
            <span className="ml-2 text-[10px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
              {data.enabled ? `${optedCount}/${total} granted` : 'all paused'}
            </span>
          </span>
        </label>
        <span className="text-[11px] text-[var(--text-muted)]">
          — one switch pauses everything; your per-action grants are kept.
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <Button
            variant="ghost"
            disabled={saving === '__bulk__' || optedCount === total}
            onClick={() =>
              apply(
                { optIn: Object.fromEntries(data.actions.map((a) => [a.action, true])) },
                '__bulk__',
              )
            }
          >
            Enable all
          </Button>
          <Button
            variant="ghost"
            disabled={saving === '__bulk__' || optedCount === 0}
            onClick={() =>
              apply(
                { optIn: Object.fromEntries(data.actions.map((a) => [a.action, false])) },
                '__bulk__',
              )
            }
          >
            Disable all
          </Button>
        </div>
      </div>

      {/* per-action grants */}
      <div className={`-mx-1 divide-y divide-[var(--border-default)]/40 ${data.enabled ? '' : 'opacity-60'}`}>
        {data.actions.map((row) => (
          <AutoActionRow
            key={row.action}
            row={row}
            saving={saving === row.action || saving === '__bulk__'}
            disabled={!data.enabled}
            onToggle={(v) => apply({ optIn: { [row.action]: v } }, row.action)}
          />
        ))}
      </div>

      {/* caps + scope */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg border border-[var(--border-default)]/40 bg-[var(--bg-surface)]/40 px-3 py-2">
        <label className="flex items-center gap-2 text-[11px] text-[var(--text-muted)]">
          Nightly budget
          <input
            type="number"
            min={1}
            defaultValue={data.caps.maxGb}
            key={`gb-${data.caps.maxGb}`}
            onBlur={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v) && v >= 1 && v !== data.caps.maxGb)
                apply({ maxGb: v }, '__caps__');
            }}
            className="w-16 rounded border border-[var(--border-default)] bg-[var(--bg-surface)] px-1.5 py-0.5 text-xs text-[var(--text-primary)] focus:border-[var(--accent)] focus:outline-none"
          />
          GB deleted max
        </label>
        <label className="flex items-center gap-2 text-[11px] text-[var(--text-muted)]">
          <input
            type="number"
            min={1}
            defaultValue={data.caps.maxObjects}
            key={`obj-${data.caps.maxObjects}`}
            onBlur={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v) && v >= 1 && v !== data.caps.maxObjects)
                apply({ maxObjects: v }, '__caps__');
            }}
            className="w-16 rounded border border-[var(--border-default)] bg-[var(--bg-surface)] px-1.5 py-0.5 text-xs text-[var(--text-primary)] focus:border-[var(--accent)] focus:outline-none"
          />
          objects max
        </label>
        <label className="flex cursor-pointer items-center gap-2 text-[11px] text-[var(--text-muted)]">
          <input
            type="checkbox"
            checked={data.remoteHosts}
            disabled={saving === '__remote__'}
            onChange={(e) => apply({ remoteHosts: e.target.checked }, '__remote__')}
            className="h-3.5 w-3.5 accent-[var(--accent)]"
          />
          Also fix remote hosts
          <span className="text-[var(--text-tertiary)]">(local-only actions stay local)</span>
        </label>
        {saving === '__caps__' && <Spinner size="w-3 h-3" color="border-[var(--accent)]" />}
      </div>

      {/* footer actions */}
      <div className="flex flex-wrap items-center gap-2 pt-0.5">
        <Button
          variant="ghost"
          className="border border-[var(--accent)]/40 bg-[var(--accent)]/10 text-[var(--accent)] hover:bg-[var(--accent)]/20 disabled:opacity-50 disabled:cursor-not-allowed"
          disabled={testSending || !data.delivery.recipient}
          onClick={() => void sendTestDigest().catch(() => undefined)}
        >
          {testSending ? 'Sending…' : 'Send test report'}
        </Button>
        {!scenario.provisioned || !scenario.active ? (
          <Button
            variant="ghost"
            className="border border-[var(--border-default)] disabled:opacity-50 disabled:cursor-not-allowed"
            disabled={provisioning}
            onClick={() => requireUnlock(() => void provisionTriageSchedule().catch(() => undefined))}
          >
            {provisioning ? 'Provisioning…' : scenario.provisioned ? 'Repair schedule' : 'Set up daily schedule'}
          </Button>
        ) : null}
        <span className="text-[11px] text-[var(--text-tertiary)]">
          The report email uses sample data on test sends; nightly runs use real fleet data.
        </span>
      </div>
    </section>
  );
}
