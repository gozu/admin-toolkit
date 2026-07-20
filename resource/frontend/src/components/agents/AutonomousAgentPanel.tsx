import { useEffect } from 'react';
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
 * that pauses the whole tier (grants preserved), safety caps, remote-host
 * scope, schedule status and the branded test report. Save failures surface
 * as toasts (the store reverts optimistic state); prerequisites render as a
 * setup checklist, not as alarms.
 */

const PLUGIN_SETTINGS_URL = '/plugins/admin-toolkit/settings/';

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

function StatusChip({ label, tone }: { label: string; tone: 'ok' | 'warn' | 'muted' }) {
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
  onToggle,
}: {
  row: TriageActionRow;
  saving: boolean;
  onToggle: (enabled: boolean) => void;
}) {
  return (
    <label
      className={`flex items-start gap-3 rounded-lg border border-transparent px-3 py-2 transition-colors hover:bg-[var(--bg-hover)] ${
        saving ? 'opacity-60' : 'cursor-pointer'
      }`}
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
              blocked — also enable it in the action list below
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

/** Numeric cap input: commits on blur or Enter; invalid input reverts to the
 *  saved value instead of silently doing nothing. */
function CapInput({
  value,
  saving,
  onCommit,
}: {
  value: number;
  saving: boolean;
  onCommit: (v: number) => void;
}) {
  return (
    <input
      type="number"
      min={1}
      defaultValue={value}
      key={value}
      disabled={saving}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
      }}
      onBlur={(e) => {
        const v = Number(e.target.value);
        if (Number.isFinite(v) && v >= 1) {
          if (v !== value) onCommit(v);
        } else {
          e.target.value = String(value);
        }
      }}
      className="w-16 rounded border border-[var(--border-default)] bg-[var(--bg-surface)] px-1.5 py-0.5 text-xs text-[var(--text-primary)] focus:border-[var(--accent)] focus:outline-none disabled:opacity-50"
    />
  );
}

export function AutonomousAgentPanel({
  requireUnlock,
  onOpenSettings,
}: {
  requireUnlock: (apply: () => void) => void;
  onOpenSettings?: () => void;
}) {
  const { data, loading, loaded, saving, testSending, provisioning, error } =
    triageSettingsStore.use();

  useEffect(() => {
    if (!loaded) void loadTriageSettings();
  }, [loaded]);

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

  const optedCount = data.actions.filter((a) => a.optedIn).length;
  const total = data.actions.length;
  const active = data.enabled && optedCount > 0;

  const apply = (update: Parameters<typeof updateTriageSettings>[0], tag: string) =>
    requireUnlock(() => void updateTriageSettings(update, tag).catch(() => undefined));

  const scenario = data.scenario;

  // Prerequisites the agent needs before anything can run — a setup
  // checklist, not an incident. Chips are reserved for post-setup state.
  const setupSteps: { label: string; action?: () => void; href?: string }[] = [];
  if (!data.killSwitch)
    setupSteps.push({ label: 'Turn on the Master kill-switch', href: PLUGIN_SETTINGS_URL });
  if (!data.masterPassword)
    setupSteps.push({ label: 'Set a master password', href: PLUGIN_SETTINGS_URL });
  if (!data.delivery.recipient)
    setupSteps.push({ label: 'Set a report recipient', action: onOpenSettings });
  if (!scenario.provisioned)
    setupSteps.push({
      label: 'Set up the daily schedule',
      action: () => requireUnlock(() => void provisionTriageSchedule().catch(() => undefined)),
    });

  const chips: { label: string; tone: 'ok' | 'warn' | 'muted' }[] = [];
  if (scenario.provisioned) {
    chips.push(
      !scenario.active
        ? { label: 'schedule inactive', tone: 'warn' }
        : {
            label: scenario.hour != null ? `daily ${String(scenario.hour).padStart(2, '0')}:00` : 'scheduled daily',
            tone: 'ok',
          },
    );
  }
  chips.push({
    label: fmtLastRun(scenario.lastRun),
    tone:
      scenario.lastRun?.outcome === 'SUCCESS' ? 'ok' : scenario.lastRun ? 'warn' : 'muted',
  });

  return (
    <section className="glass-card p-4 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-[var(--text-primary)]">Autonomous daily agent</h3>
        <span
          className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
            active
              ? 'bg-[var(--accent)]/15 text-[var(--accent)]'
              : 'bg-[var(--bg-surface)] text-[var(--text-muted)]'
          }`}
        >
          {active ? 'active' : data.enabled ? 'idle — nothing allowed yet' : 'paused'}
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          {chips.map((c) => (
            <StatusChip key={c.label} label={c.label} tone={c.tone} />
          ))}
        </div>
      </div>

      <p className="text-xs text-[var(--text-muted)] leading-relaxed">
        Every night the triage agent scores the whole fleet, emails the health report
        {data.delivery.recipient ? (
          <>
            {' '}
            to <strong className="text-[var(--text-secondary)]">{data.delivery.recipient}</strong>
          </>
        ) : null}
        , and — only for the actions you allow below — fixes findings on its own. Each
        autonomous fix is planned, token-signed and audited exactly like a human-approved
        action.
      </p>

      {setupSteps.length > 0 && (
        <div className="rounded-lg border border-[var(--neon-amber)]/30 bg-[var(--neon-amber)]/5 px-3 py-2">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-[var(--neon-amber)] pb-1">
            To go live
          </p>
          <ol className="space-y-0.5 text-xs text-[var(--text-secondary)]">
            {setupSteps.map((step, i) => (
              <li key={step.label}>
                {i + 1}.{' '}
                {step.href ? (
                  <a
                    href={step.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[var(--accent)] hover:underline"
                  >
                    {step.label} ↗
                  </a>
                ) : step.action ? (
                  <button
                    type="button"
                    onClick={step.action}
                    className="text-[var(--accent)] hover:underline"
                  >
                    {step.label}
                  </button>
                ) : (
                  step.label
                )}
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* master switch + bulk controls */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-[var(--border-default)]/60 bg-[var(--bg-surface)]/60 px-3 py-2">
        <label className={`flex items-center gap-2.5 ${saving === '__master__' ? 'opacity-60' : 'cursor-pointer'}`}>
          <input
            type="checkbox"
            checked={data.enabled}
            disabled={saving === '__master__'}
            onChange={(e) => apply({ enabled: e.target.checked }, '__master__')}
            className="h-4 w-4 accent-[var(--accent)]"
          />
          <span className="text-xs font-semibold text-[var(--text-primary)]">
            Autonomous actions
            <span className="ml-2 text-[10px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
              {data.enabled ? `${optedCount}/${total} allowed` : 'paused'}
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
            Allow all
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
            Revoke all
          </Button>
        </div>
      </div>

      {!data.enabled && (
        <p className="rounded-lg border border-[var(--neon-amber)]/30 bg-[var(--neon-amber)]/5 px-3 py-2 text-[11px] text-[var(--text-secondary)]">
          Paused — grants below are saved, but nothing will run tonight.
        </p>
      )}

      {/* per-action grants (stay interactive while paused: grants are kept) */}
      <div className="-mx-1 divide-y divide-[var(--border-default)]/40">
        {data.actions.map((row) => (
          <AutoActionRow
            key={row.action}
            row={row}
            saving={saving === row.action || saving === '__bulk__'}
            onToggle={(v) => apply({ optIn: { [row.action]: v } }, row.action)}
          />
        ))}
      </div>

      {/* caps + scope */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg border border-[var(--border-default)]/40 bg-[var(--bg-surface)]/40 px-3 py-2">
        <span className="flex items-center gap-2 text-[11px] text-[var(--text-muted)]">
          Delete at most
          <CapInput
            value={data.caps.maxGb}
            saving={saving === '__caps__'}
            onCommit={(v) => apply({ maxGb: v }, '__caps__')}
          />
          GB and
          <CapInput
            value={data.caps.maxObjects}
            saving={saving === '__caps__'}
            onCommit={(v) => apply({ maxObjects: v }, '__caps__')}
          />
          objects per night
          {saving === '__caps__' && <Spinner size="w-3 h-3" color="border-[var(--accent)]" />}
        </span>
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
        <span className="ml-auto flex items-center gap-2 text-[10px] text-[var(--text-tertiary)]">
          Risk:
          <span className="flex items-center gap-1">
            <span className={`h-2 w-2 rounded-full ${RISK_DOT.low}`} /> low
          </span>
          <span className="flex items-center gap-1">
            <span className={`h-2 w-2 rounded-full ${RISK_DOT.medium}`} /> medium
          </span>
        </span>
      </div>

      {/* footer actions */}
      <div className="flex flex-wrap items-center gap-2 pt-0.5">
        <Button
          variant="ghost"
          className="border border-[var(--accent)]/40 bg-[var(--accent)]/10 text-[var(--accent)] hover:bg-[var(--accent)]/20 disabled:opacity-50 disabled:cursor-not-allowed"
          disabled={testSending || !data.delivery.recipient}
          title={data.delivery.recipient ? undefined : 'Set a report recipient first (step above)'}
          onClick={() => void sendTestDigest().catch(() => undefined)}
        >
          {testSending ? 'Sending…' : 'Send test report'}
        </Button>
        {scenario.provisioned && !scenario.active ? (
          <Button
            variant="ghost"
            className="border border-[var(--border-default)] disabled:opacity-50 disabled:cursor-not-allowed"
            disabled={provisioning}
            onClick={() => requireUnlock(() => void provisionTriageSchedule().catch(() => undefined))}
          >
            {provisioning ? 'Provisioning…' : 'Repair schedule'}
          </Button>
        ) : null}
        <span className="text-[11px] text-[var(--text-tertiary)]">
          Test reports use sample data; nightly runs use real fleet data.
        </span>
      </div>
    </section>
  );
}
