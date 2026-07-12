import { useEffect, useState } from 'react';
import {
  agentActionGatesStore,
  loadActionGates,
  toggleActionGate,
  type ActionRow,
  type SensorRow,
} from '../../state/agentActionGatesStore';
import { useRedState } from '../../state/redUnlockStore';
import { UnlockModal } from '../UnlockModal';

/**
 * Agent Permissions — the per-action enablement catalog. Every capability the
 * agents have is listed here: read-only sensor tools (checked by default)
 * and the actuator actions grouped read/write vs execute (unchecked and
 * therefore refused until an admin enables them). Toggles are advanced-gated
 * server-side and reach running agent kernels within ~30s — no recycle.
 */

const PLUGIN_SETTINGS_URL = '/plugins/admin-toolkit/settings/';

const RISK_DOT: Record<string, string> = {
  red: 'bg-[var(--danger)]',
  amber: 'bg-[var(--neon-amber)]',
  green: 'bg-[var(--accent)]',
};

function Chip({ children }: { children: string }) {
  return (
    <span className="rounded border border-[var(--border-default)] bg-[var(--bg-surface)] px-1.5 py-0.5 text-[10px] text-[var(--text-muted)]">
      {children}
    </span>
  );
}

function GateRow({
  name,
  enabled,
  risk,
  detail,
  chips,
  saving,
  onToggle,
}: {
  name: string;
  enabled: boolean;
  risk?: string;
  detail: string;
  chips?: string[];
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
        checked={enabled}
        disabled={saving}
        onChange={(e) => onToggle(e.target.checked)}
        className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--accent)]"
      />
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="flex flex-wrap items-center gap-2">
          {risk && <span className={`h-2 w-2 shrink-0 rounded-full ${RISK_DOT[risk] ?? RISK_DOT.amber}`} />}
          <code className="text-xs font-semibold text-[var(--text-primary)]">{name}</code>
          {!enabled && (
            <span className="text-[10px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
              disabled
            </span>
          )}
          {chips?.map((c) => <Chip key={c}>{c}</Chip>)}
        </div>
        <p className="text-[11px] leading-relaxed text-[var(--text-muted)] break-words">{detail}</p>
      </div>
    </label>
  );
}

function SectionCard({
  title,
  subtitle,
  enabledCount,
  total,
  children,
}: {
  title: string;
  subtitle: string;
  enabledCount: number;
  total: number;
  children: React.ReactNode;
}) {
  return (
    <section className="glass-card p-4 space-y-2">
      <div className="flex items-baseline gap-2">
        <h3 className="text-sm font-semibold text-[var(--text-primary)]">{title}</h3>
        <span className="text-xs text-[var(--text-tertiary)]">
          {enabledCount}/{total} enabled
        </span>
      </div>
      <p className="text-xs text-[var(--text-muted)]">{subtitle}</p>
      <div className="-mx-1 divide-y divide-[var(--border-default)]/40">{children}</div>
    </section>
  );
}

export function AgentSettingsPage() {
  const { sensors, actions, loading, loaded, saving, error } = agentActionGatesStore.use();
  const { authed: unlocked } = useRedState();
  const [showUnlock, setShowUnlock] = useState(false);
  const [pending, setPending] = useState<{ name: string; enabled: boolean } | null>(null);

  useEffect(() => {
    if (!loaded) void loadActionGates();
  }, [loaded]);

  const requestToggle = (name: string, enabled: boolean) => {
    if (!unlocked) {
      setPending({ name, enabled });
      setShowUnlock(true);
      return;
    }
    void toggleActionGate(name, enabled).catch(() => undefined);
  };

  const readWrite = actions.filter((a: ActionRow) => a.mode === 'read/write');
  const execute = actions.filter((a: ActionRow) => a.mode === 'execute');

  if (loading && !loaded) {
    return (
      <div className="flex-1 flex items-center justify-center py-20">
        <div className="w-6 h-6 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="w-full flex-1 min-h-0 py-4 overflow-y-auto">
      <div className="w-full max-w-[64rem] mx-auto px-4 space-y-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">Agent Permissions</h2>
          <span className="text-xs text-[var(--text-tertiary)]">
            per-action enablement · {sensors.length + actions.length} capabilities
          </span>
        </div>

        <div className="glass-card p-4 space-y-1.5 border-l-2 border-l-[var(--accent)]">
          <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
            Everything the agents can do is listed below. Read-only tools are enabled by
            default; every read/write and execute action is <strong>off</strong> until you
            enable it here — a disabled action is refused at plan time and again at execute
            time, for chat, checklists and the autonomous triage tier alike. Changes apply to
            running agents within ~30 seconds. Executing also still requires the{' '}
            <a
              href={PLUGIN_SETTINGS_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[var(--accent)] hover:underline"
            >
              agentic-actions master switch ↗
            </a>{' '}
            and per-plan human approval — this page only decides which actions exist for the
            agents at all.
          </p>
        </div>

        {error && (
          <div className="glass-card p-3 border-l-2 border-l-[var(--danger)]">
            <p className="text-xs text-[var(--danger)]">{error}</p>
          </div>
        )}

        <SectionCard
          title="Read-only tools"
          subtitle="Sensors — inspect health, config, cost, storage, logs. No side effects; enabled by default."
          enabledCount={sensors.filter((s: SensorRow) => s.enabled).length}
          total={sensors.length}
        >
          {sensors.map((s: SensorRow) => (
            <GateRow
              key={s.name}
              name={s.name}
              enabled={s.enabled}
              detail={s.description}
              saving={saving === s.name}
              onToggle={(v) => requestToggle(s.name, v)}
            />
          ))}
        </SectionCard>

        <SectionCard
          title="Read / write actions"
          subtitle="Configuration mutations — drift-guarded, most land in the restorable settings history. Disabled by default."
          enabledCount={readWrite.filter((a) => a.enabled).length}
          total={readWrite.length}
        >
          {readWrite.map((a) => (
            <GateRow
              key={a.action}
              name={a.action}
              enabled={a.enabled}
              risk={a.risk}
              detail={a.shape}
              chips={[...(a.batchable ? ['batchable'] : []), ...(a.localOnly ? ['local-only'] : [])]}
              saving={saving === a.action}
              onToggle={(v) => requestToggle(a.action, v)}
            />
          ))}
        </SectionCard>

        <SectionCard
          title="Execute actions"
          subtitle="Run, stop, clean, delete, send — the plan → approve → confirm-token flow still applies to every one. Disabled by default."
          enabledCount={execute.filter((a) => a.enabled).length}
          total={execute.length}
        >
          {execute.map((a) => (
            <GateRow
              key={a.action}
              name={a.action}
              enabled={a.enabled}
              risk={a.risk}
              detail={a.shape}
              chips={[...(a.batchable ? ['batchable'] : []), ...(a.localOnly ? ['local-only'] : [])]}
              saving={saving === a.action}
              onToggle={(v) => requestToggle(a.action, v)}
            />
          ))}
        </SectionCard>
      </div>

      <UnlockModal
        isOpen={showUnlock}
        onClose={() => {
          setShowUnlock(false);
          setPending(null);
        }}
        onUnlocked={() => {
          setShowUnlock(false);
          if (pending) {
            void toggleActionGate(pending.name, pending.enabled).catch(() => undefined);
            setPending(null);
          }
        }}
      />
    </div>
  );
}
