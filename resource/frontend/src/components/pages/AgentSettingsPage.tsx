import { useEffect, useRef, useState } from 'react';
import {
  agentActionGatesStore,
  loadActionGates,
  toggleActionGate,
  toggleGatesBulk,
  type ActionRow,
  type SensorRow,
} from '../../state/agentActionGatesStore';
import { useRedState } from '../../state/redUnlockStore';
import { UnlockModal } from '../UnlockModal';
import { Modal } from '../Modal';
import { Button } from '../common/Button';

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

/** The "Read access — all toolkit data" master switch: checked when every
 *  sensor is on, indeterminate when only some are. One click flips them all
 *  in a single request; per-sensor rows below stay individually toggleable. */
function MasterReadRow({
  enabledCount,
  total,
  saving,
  onToggle,
}: {
  enabledCount: number;
  total: number;
  saving: boolean;
  onToggle: (enabled: boolean) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const allOn = enabledCount === total && total > 0;
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = enabledCount > 0 && !allOn;
  }, [enabledCount, allOn]);
  return (
    <label
      className={`flex items-start gap-3 rounded-lg border border-[var(--border-default)]/60 bg-[var(--bg-surface)]/60 px-3 py-2 transition-colors hover:bg-[var(--bg-hover)] ${
        saving ? 'opacity-60' : 'cursor-pointer'
      }`}
    >
      <input
        ref={ref}
        type="checkbox"
        checked={allOn}
        disabled={saving}
        onChange={(e) => onToggle(e.target.checked)}
        className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--accent)]"
      />
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold text-[var(--text-primary)]">
            Read access — all toolkit data
          </span>
          {!allOn && enabledCount > 0 && (
            <span className="text-[10px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
              partial
            </span>
          )}
          {enabledCount === 0 && (
            <span className="text-[10px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
              disabled
            </span>
          )}
        </div>
        <p className="text-[11px] leading-relaxed text-[var(--text-muted)] break-words">
          Everything the toolkit surfaces — health, config, cost, storage, logs, churn, audits —
          is readable by the agents. Flip this to grant or revoke all sensors at once.
        </p>
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
  const [pending, setPending] = useState<{ names: string[]; enabled: boolean } | null>(null);
  const [powerUpConfirm, setPowerUpConfirm] = useState<string | null>(null);

  useEffect(() => {
    if (!loaded) void loadActionGates();
  }, [loaded]);

  const applyToggle = (names: string[], enabled: boolean) => {
    if (names.length === 1) void toggleActionGate(names[0], enabled).catch(() => undefined);
    else void toggleGatesBulk(names, enabled).catch(() => undefined);
  };

  const requestToggle = (names: string[], enabled: boolean) => {
    if (!unlocked) {
      setPending({ names, enabled });
      setShowUnlock(true);
      return;
    }
    applyToggle(names, enabled);
  };

  const readWrite = actions.filter((a: ActionRow) => a.mode === 'read/write');
  const powerUp = actions.filter((a: ActionRow) => a.action === 'python-run');
  const execute = actions.filter(
    (a: ActionRow) => a.mode === 'execute' && a.action !== 'python-run',
  );

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
              Master kill-switch ↗
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
          <MasterReadRow
            enabledCount={sensors.filter((s: SensorRow) => s.enabled).length}
            total={sensors.length}
            saving={saving === '__bulk__'}
            onToggle={(v) =>
              requestToggle(
                sensors.map((s: SensorRow) => s.name),
                v,
              )
            }
          />
          {sensors.map((s: SensorRow) => (
            <GateRow
              key={s.name}
              name={s.name}
              enabled={s.enabled}
              detail={s.description}
              saving={saving === s.name || saving === '__bulk__'}
              onToggle={(v) => requestToggle([s.name], v)}
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
              onToggle={(v) => requestToggle([a.action], v)}
            />
          ))}
        </SectionCard>

        {powerUp.length > 0 && (
          <SectionCard
            title="Power-Up (dangerous)"
            subtitle="Agent-authored Python — scripts run with the toolkit's admin credentials. On top of this gate, EVERY run requires a per-run 'I have read this code' acknowledgment on the plan card; excluded from batch approvals and auto-remediation."
            enabledCount={powerUp.filter((a) => a.enabled).length}
            total={powerUp.length}
          >
            {powerUp.map((a) => (
              <GateRow
                key={a.action}
                name={a.action}
                enabled={a.enabled}
                risk={a.risk}
                detail={a.shape}
                chips={a.localOnly ? ['local-only'] : []}
                saving={saving === a.action}
                onToggle={(v) => (v ? setPowerUpConfirm(a.action) : requestToggle([a.action], false))}
              />
            ))}
          </SectionCard>
        )}

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
              onToggle={(v) => requestToggle([a.action], v)}
            />
          ))}
        </SectionCard>
      </div>

      <Modal
        isOpen={powerUpConfirm !== null}
        onClose={() => setPowerUpConfirm(null)}
        title="Enable Power-Up?"
        footer={
          <div className="flex items-center justify-end gap-2">
            <Button variant="modalCancel" onClick={() => setPowerUpConfirm(null)}>
              Cancel
            </Button>
            <Button
              variant="modalDanger"
              onClick={() => {
                if (powerUpConfirm) requestToggle([powerUpConfirm], true);
                setPowerUpConfirm(null);
              }}
            >
              Enable anyway
            </Button>
          </div>
        }
      >
        <p className="text-sm text-[var(--text-secondary)] leading-relaxed">
          LLMs can make mistakes. Power-Up scripts run with the toolkit&apos;s{' '}
          <strong>admin credentials</strong> on the DSS host. Every run will still show you the
          exact code and require an explicit &quot;I have read this code&quot; acknowledgment
          before it executes. Enable anyway?
        </p>
      </Modal>

      <UnlockModal
        isOpen={showUnlock}
        onClose={() => {
          setShowUnlock(false);
          setPending(null);
        }}
        onUnlocked={() => {
          setShowUnlock(false);
          if (pending) {
            applyToggle(pending.names, pending.enabled);
            setPending(null);
          }
        }}
      />
    </div>
  );
}
