import { useEffect, useRef, useState } from 'react';
import {
  agentActionGatesStore,
  loadActionGates,
  toggleActionGate,
  toggleAutonomous,
  toggleGatesBulk,
  type ActionRow,
  type SensorRow,
} from '../../state/agentActionGatesStore';
import { loadTriageSettings } from '../../state/triageSettingsStore';
import { useDiag } from '../../context/DiagContext';
import { useRedState } from '../../state/redUnlockStore';
import { UnlockModal } from '../UnlockModal';
import { Modal } from '../Modal';
import { Button } from '../common/Button';
import { Spinner } from '../common/Spinner';
import { AutonomousAgentPanel } from '../agents/AutonomousAgentPanel';

/**
 * Agent Permissions — the per-capability catalog. Every capability the agents
 * have is listed once, in four sections (Read-only / Write / Execute /
 * Power-Up), each row carrying TWO checkboxes: Enabled (the agent may use it
 * at all) and Auto (the nightly autonomous agent may run it without a human).
 * Server invariants: Auto ⇒ Enabled, and python-run can never be Auto.
 * Toggles are advanced-gated server-side and reach running agent kernels
 * within ~30s — no recycle.
 */

const PLUGIN_SETTINGS_URL = '/plugins/admin-toolkit/settings/';

const RISK_DOT: Record<string, string> = {
  red: 'bg-[var(--danger)]',
  amber: 'bg-[var(--neon-amber)]',
  green: 'bg-[var(--accent)]',
};

// One shared grid keeps the header labels and every row's cells aligned —
// a real table: capability | description | Enabled | Auto, one row each.
const ROW_GRID = 'grid grid-cols-[13.5rem_minmax(0,1fr)_4.5rem_4.5rem] gap-x-4';

const HEAD_CELL = 'text-[11px] font-semibold uppercase tracking-wider text-[var(--text-tertiary)]';

// Descriptions longer than this clamp to two lines and expand on click — the
// table stays scannable while the full tool doc stays one click away.
const CLAMP_CHARS = 180;

function Chip({ children }: { children: string }) {
  return (
    <span className="rounded border border-[var(--border-default)] bg-[var(--bg-surface)] px-1.5 py-0.5 text-[11px] leading-none text-[var(--text-muted)]">
      {children}
    </span>
  );
}

function ColumnHeadRow() {
  return (
    <div className={`${ROW_GRID} border-b border-[var(--border-default)]/60 px-3 pt-1 pb-1.5`}>
      <span className={HEAD_CELL}>Capability</span>
      <span className={HEAD_CELL}>What it does</span>
      <span className={`${HEAD_CELL} justify-self-center`}>Enabled</span>
      <span className={`${HEAD_CELL} justify-self-center`}>Auto</span>
    </div>
  );
}

/** Description cell: long docs clamp to two lines and toggle open on click. */
function DetailCell({ detail, note }: { detail: string; note?: string }) {
  const [expanded, setExpanded] = useState(false);
  const clampable = detail.length > CLAMP_CHARS;
  return (
    <div className="min-w-0 self-center">
      <p
        className={`text-[13px] leading-snug text-[var(--text-muted)] break-words ${
          clampable ? 'cursor-pointer' : ''
        } ${clampable && !expanded ? 'line-clamp-2' : ''}`}
        title={clampable ? (expanded ? 'Click to collapse' : 'Click to show the full description') : undefined}
        onClick={clampable ? () => setExpanded((v) => !v) : undefined}
      >
        {detail}
      </p>
      {note && <p className="mt-0.5 text-xs text-[var(--text-tertiary)]">{note}</p>}
    </div>
  );
}

function GateRow({
  name,
  enabled,
  autonomous,
  autoCapable,
  risk,
  detail,
  chips,
  saving,
  onToggleEnabled,
  onToggleAuto,
}: {
  name: string;
  enabled: boolean;
  autonomous: boolean;
  autoCapable: boolean;
  risk?: string;
  detail: string;
  chips?: string[];
  saving: boolean;
  onToggleEnabled: (enabled: boolean) => void;
  onToggleAuto: (allowed: boolean) => void;
}) {
  return (
    <div
      className={`${ROW_GRID} px-3 py-2.5 transition-colors hover:bg-[var(--bg-hover)] ${
        saving ? 'opacity-60' : ''
      }`}
    >
      <div className="min-w-0 flex flex-wrap items-center gap-x-2 gap-y-1 self-center">
        {risk && <span className={`h-2 w-2 shrink-0 rounded-full ${RISK_DOT[risk] ?? RISK_DOT.amber}`} />}
        <code className="text-[13px] font-semibold text-[var(--text-primary)] break-words">
          {name}
        </code>
        {chips?.map((c) => <Chip key={c}>{c}</Chip>)}
      </div>
      <DetailCell
        detail={detail}
        note={
          autoCapable
            ? undefined
            : 'Autonomous mode unavailable — manual per-run code acknowledgment always required.'
        }
      />
      <input
        type="checkbox"
        aria-label={`${name} enabled`}
        checked={enabled}
        disabled={saving}
        onChange={(e) => onToggleEnabled(e.target.checked)}
        className="h-[17px] w-[17px] justify-self-center self-center accent-[var(--accent)] cursor-pointer disabled:cursor-default"
      />
      {autoCapable ? (
        <input
          type="checkbox"
          aria-label={`${name} autonomous`}
          checked={autonomous}
          disabled={saving}
          onChange={(e) => onToggleAuto(e.target.checked)}
          className="h-[17px] w-[17px] justify-self-center self-center accent-[var(--accent)] cursor-pointer disabled:cursor-default"
        />
      ) : (
        <input
          type="checkbox"
          aria-label={`${name} autonomous (unavailable)`}
          checked={false}
          disabled
          title="python-run can never run autonomously — every run requires a human 'I have read this code' acknowledgment."
          className="h-[17px] w-[17px] justify-self-center self-center accent-[var(--accent)] opacity-30 cursor-not-allowed"
        />
      )}
    </div>
  );
}

/** The sensors' master row: two indeterminate checkboxes — Enabled flips all
 *  sensors in one request, Auto grants/revokes autonomy over all of them.
 *  Per-sensor rows below stay individually toggleable. */
function MasterReadRow({
  enabledCount,
  autoCount,
  total,
  saving,
  onToggleEnabled,
  onToggleAuto,
}: {
  enabledCount: number;
  autoCount: number;
  total: number;
  saving: boolean;
  onToggleEnabled: (enabled: boolean) => void;
  onToggleAuto: (allowed: boolean) => void;
}) {
  const enabledRef = useRef<HTMLInputElement>(null);
  const autoRef = useRef<HTMLInputElement>(null);
  const allOn = enabledCount === total && total > 0;
  const allAuto = autoCount === total && total > 0;
  useEffect(() => {
    if (enabledRef.current) enabledRef.current.indeterminate = enabledCount > 0 && !allOn;
    if (autoRef.current) autoRef.current.indeterminate = autoCount > 0 && !allAuto;
  }, [enabledCount, allOn, autoCount, allAuto]);
  return (
    <div
      className={`${ROW_GRID} rounded-lg border border-[var(--border-default)]/60 bg-[var(--bg-surface)]/60 px-3 py-2.5 transition-colors hover:bg-[var(--bg-hover)] ${
        saving ? 'opacity-60' : ''
      }`}
    >
      <div className="min-w-0 flex flex-wrap items-center gap-x-2 gap-y-1 self-center">
        <span className="text-[13px] font-semibold text-[var(--text-primary)]">
          Read access — all toolkit data
        </span>
        {!allOn && enabledCount > 0 && (
          <span className="text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
            partial
          </span>
        )}
        {enabledCount === 0 && (
          <span className="text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
            disabled
          </span>
        )}
      </div>
      <p className="min-w-0 self-center text-[13px] leading-snug text-[var(--text-muted)] break-words">
        Everything the toolkit surfaces — health, config, cost, storage, logs, churn, audits —
        is readable by the agents. Each column&apos;s checkbox flips all sensors at once.
      </p>
      <input
        ref={enabledRef}
        type="checkbox"
        aria-label="all sensors enabled"
        checked={allOn}
        disabled={saving}
        onChange={(e) => onToggleEnabled(e.target.checked)}
        className="h-[17px] w-[17px] justify-self-center self-center accent-[var(--accent)] cursor-pointer disabled:cursor-default"
      />
      <input
        ref={autoRef}
        type="checkbox"
        aria-label="all sensors autonomous"
        checked={allAuto}
        disabled={saving}
        onChange={(e) => onToggleAuto(e.target.checked)}
        className="h-[17px] w-[17px] justify-self-center self-center accent-[var(--accent)] cursor-pointer disabled:cursor-default"
      />
    </div>
  );
}

function SectionCard({
  title,
  subtitle,
  enabledCount,
  total,
  autoBulk,
  children,
}: {
  title: string;
  subtitle: string;
  enabledCount: number;
  total: number;
  autoBulk?: {
    allowed: number;
    capable: number;
    saving: boolean;
    onAll: () => void;
    onNone: () => void;
  };
  children: React.ReactNode;
}) {
  return (
    <section className="glass-card p-4 space-y-2">
      <div className="flex flex-wrap items-baseline gap-2">
        <h3 className="text-[15px] font-semibold text-[var(--text-primary)]">{title}</h3>
        <span className="text-xs text-[var(--text-tertiary)]">
          {enabledCount}/{total} enabled
        </span>
        {autoBulk && (
          <span className="ml-auto flex items-center gap-2 text-xs text-[var(--text-tertiary)]">
            {autoBulk.allowed}/{autoBulk.capable} auto
            <button
              type="button"
              disabled={autoBulk.saving || autoBulk.allowed === autoBulk.capable}
              onClick={autoBulk.onAll}
              className="text-[var(--accent)] hover:underline disabled:opacity-40 disabled:no-underline"
            >
              Allow all auto
            </button>
            <button
              type="button"
              disabled={autoBulk.saving || autoBulk.allowed === 0}
              onClick={autoBulk.onNone}
              className="text-[var(--accent)] hover:underline disabled:opacity-40 disabled:no-underline"
            >
              Revoke all auto
            </button>
          </span>
        )}
      </div>
      <p className="text-[13px] leading-snug text-[var(--text-muted)]">{subtitle}</p>
      <div className="-mx-1 pt-1">
        <ColumnHeadRow />
        <div className="divide-y divide-[var(--border-default)]/40">{children}</div>
      </div>
    </section>
  );
}

type PendingToggle = { kind: 'gates' | 'auto'; names: string[]; value: boolean };

export function AgentSettingsPage() {
  const { sensors, actions, loading, loaded, saving, error } = agentActionGatesStore.use();
  const { setActivePage } = useDiag();
  const { authed: unlocked } = useRedState();
  const [showUnlock, setShowUnlock] = useState(false);
  const [pending, setPending] = useState<PendingToggle | null>(null);
  const [pendingAction, setPendingAction] = useState<(() => void) | null>(null);
  const [powerUpConfirm, setPowerUpConfirm] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  useEffect(() => {
    if (!loaded) void loadActionGates();
  }, [loaded]);

  // Every successful write refreshes the autonomous panel's allowed-count.
  const applyToggle = ({ kind, names, value }: PendingToggle) => {
    const op =
      kind === 'auto'
        ? toggleAutonomous(names, value)
        : names.length === 1
          ? toggleActionGate(names[0], value)
          : toggleGatesBulk(names, value);
    void op.then(() => void loadTriageSettings()).catch(() => undefined);
  };

  const requestToggle = (toggle: PendingToggle) => {
    if (!unlocked) {
      setPending(toggle);
      setShowUnlock(true);
      return;
    }
    applyToggle(toggle);
  };

  // Unlock gate shared with the autonomous-agent panel: any panel write goes
  // through the same red-unlock modal as a gate toggle.
  const requireUnlock = (apply: () => void) => {
    if (!unlocked) {
      setPendingAction(() => apply);
      setShowUnlock(true);
      return;
    }
    apply();
  };

  // Live capability filter over name + description/shape — with ~60 rows,
  // finding one gate by scrolling is the page's slowest interaction.
  const needle = query.trim().toLowerCase();
  const matchesSensor = (s: SensorRow) =>
    !needle || s.name.toLowerCase().includes(needle) || s.description.toLowerCase().includes(needle);
  const matchesAction = (a: ActionRow) =>
    !needle || a.action.toLowerCase().includes(needle) || a.shape.toLowerCase().includes(needle);

  const visibleSensors = sensors.filter(matchesSensor);
  const readWrite = actions.filter((a: ActionRow) => a.mode === 'read/write' && matchesAction(a));
  const execute = actions.filter(
    (a: ActionRow) => a.mode === 'execute' && a.action !== 'python-run' && matchesAction(a),
  );
  const powerUp = actions.filter((a: ActionRow) => a.action === 'python-run' && matchesAction(a));
  const nothingMatches =
    needle && visibleSensors.length + readWrite.length + powerUp.length + execute.length === 0;

  const autoBulkFor = (rows: ActionRow[]) => {
    const capable = rows.filter((a) => a.autoCapable);
    return {
      allowed: capable.filter((a) => a.autonomous).length,
      capable: capable.length,
      saving: saving === '__bulk-auto__',
      onAll: () =>
        requestToggle({ kind: 'auto', names: capable.map((a) => a.action), value: true }),
      onNone: () =>
        requestToggle({ kind: 'auto', names: capable.map((a) => a.action), value: false }),
    };
  };

  const actionChips = (a: ActionRow) => [
    ...(a.batchable ? ['batchable'] : []),
    ...(a.localOnly ? ['local-only'] : []),
  ];

  const actionGateRow = (a: ActionRow) => (
    <GateRow
      key={a.action}
      name={a.action}
      enabled={a.enabled}
      autonomous={a.autonomous}
      autoCapable={a.autoCapable}
      risk={a.risk}
      detail={a.shape}
      chips={actionChips(a)}
      saving={saving === a.action || saving === '__bulk-auto__'}
      onToggleEnabled={(v) => requestToggle({ kind: 'gates', names: [a.action], value: v })}
      onToggleAuto={(v) => requestToggle({ kind: 'auto', names: [a.action], value: v })}
    />
  );

  if (loading && !loaded) {
    return (
      <div className="flex-1 flex items-center justify-center py-20">
        <Spinner size="w-6 h-6" color="border-[var(--accent)]" />
      </div>
    );
  }

  return (
    <div className="w-full flex-1 min-h-0 py-4 overflow-y-auto">
      <div className="w-full px-4 space-y-3">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-semibold text-[var(--text-primary)]">Agent Permissions</h2>
          <span className="text-[13px] text-[var(--text-tertiary)]">
            per-action enablement · {sensors.length + actions.length} capabilities
          </span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter capabilities…"
            className="ml-auto w-56 rounded-md border border-[var(--border-default)] bg-[var(--bg-surface)] px-2.5 py-1.5 text-[13px] text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:border-[var(--accent)] focus:outline-none"
          />
        </div>

        <div className="glass-card p-4 space-y-1.5 border-l-2 border-l-[var(--accent)]">
          <p className="text-[13px] text-[var(--text-secondary)] leading-relaxed">
            Everything the agents can do is listed below with two checkboxes per capability:{' '}
            <strong>Enabled</strong> lets agents use it at all (with per-plan human approval in
            chat); <strong>Auto</strong> additionally lets the nightly autonomous agent plan and
            run it without a human in the loop. Auto implies Enabled; disabling a capability
            revokes its Auto grant. A disabled action is refused at plan time and again at
            execute time. Changes apply to running agents within ~30 seconds. The{' '}
            <a
              href={PLUGIN_SETTINGS_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[var(--accent)] hover:underline"
            >
              Master kill-switch ↗
            </a>{' '}
            (on by default) still sits above everything — an admin can shut all agentic actions
            off there with one click.
          </p>
        </div>

        {error && (
          <div className="glass-card p-3 border-l-2 border-l-[var(--danger)]">
            <p className="text-xs text-[var(--danger)]">{error}</p>
          </div>
        )}

        {!needle && (
          <AutonomousAgentPanel
            requireUnlock={requireUnlock}
            onOpenSettings={() => setActivePage('settings')}
          />
        )}

        {nothingMatches && (
          <div className="glass-card p-6 text-center text-[13px] text-[var(--text-muted)]">
            No capabilities match “{query.trim()}”.
          </div>
        )}

        <div id="permission-catalog" className="space-y-3">
          {visibleSensors.length > 0 && (
            <SectionCard
              title="Read-only tools"
              subtitle="Sensors — inspect health, config, cost, storage, logs. No side effects; enabled and autonomous by default (the nightly agent reads to decide)."
              enabledCount={sensors.filter((s: SensorRow) => s.enabled).length}
              total={sensors.length}
            >
              {!needle && (
                <MasterReadRow
                  enabledCount={sensors.filter((s: SensorRow) => s.enabled).length}
                  autoCount={sensors.filter((s: SensorRow) => s.autonomous).length}
                  total={sensors.length}
                  saving={saving === '__bulk__' || saving === '__bulk-auto__'}
                  onToggleEnabled={(v) =>
                    requestToggle({
                      kind: 'gates',
                      names: sensors.map((s: SensorRow) => s.name),
                      value: v,
                    })
                  }
                  onToggleAuto={(v) =>
                    requestToggle({
                      kind: 'auto',
                      names: sensors.map((s: SensorRow) => s.name),
                      value: v,
                    })
                  }
                />
              )}
              {visibleSensors.map((s: SensorRow) => (
                <GateRow
                  key={s.name}
                  name={s.name}
                  enabled={s.enabled}
                  autonomous={s.autonomous}
                  autoCapable
                  detail={s.description}
                  saving={saving === s.name || saving === '__bulk__' || saving === '__bulk-auto__'}
                  onToggleEnabled={(v) =>
                    requestToggle({ kind: 'gates', names: [s.name], value: v })
                  }
                  onToggleAuto={(v) => requestToggle({ kind: 'auto', names: [s.name], value: v })}
                />
              ))}
            </SectionCard>
          )}

          {readWrite.length > 0 && (
            <SectionCard
              title="Write tools"
              subtitle="Configuration mutations — drift-guarded, most land in the restorable settings history. Disabled by default; Auto lets the nightly agent apply them unattended."
              enabledCount={readWrite.filter((a) => a.enabled).length}
              total={readWrite.length}
              autoBulk={autoBulkFor(readWrite)}
            >
              {readWrite.map(actionGateRow)}
            </SectionCard>
          )}

          {execute.length > 0 && (
            <SectionCard
              title="Execute tools"
              subtitle="Run, stop, clean, delete, send — the plan → approve → confirm-token flow still applies in chat; Auto lets the nightly agent run them unattended. Disabled by default."
              enabledCount={execute.filter((a) => a.enabled).length}
              total={execute.length}
              autoBulk={autoBulkFor(execute)}
            >
              {execute.map(actionGateRow)}
            </SectionCard>
          )}

          {powerUp.length > 0 && (
            <SectionCard
              title="Power-Up (dangerous)"
              subtitle="Agent-authored Python — scripts run with the toolkit's admin credentials. On top of this gate, EVERY run requires a per-run 'I have read this code' acknowledgment on the plan card; excluded from batch approvals and permanently excluded from autonomous execution."
              enabledCount={powerUp.filter((a) => a.enabled).length}
              total={powerUp.length}
            >
              {powerUp.map((a) => (
                <GateRow
                  key={a.action}
                  name={a.action}
                  enabled={a.enabled}
                  autonomous={false}
                  autoCapable={false}
                  risk={a.risk}
                  detail={a.shape}
                  chips={a.localOnly ? ['local-only'] : []}
                  saving={saving === a.action}
                  onToggleEnabled={(v) =>
                    v
                      ? setPowerUpConfirm(a.action)
                      : requestToggle({ kind: 'gates', names: [a.action], value: false })
                  }
                  onToggleAuto={() => undefined}
                />
              ))}
            </SectionCard>
          )}
        </div>
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
                if (powerUpConfirm)
                  requestToggle({ kind: 'gates', names: [powerUpConfirm], value: true });
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
          setPendingAction(null);
        }}
        onUnlocked={() => {
          setShowUnlock(false);
          if (pending) {
            applyToggle(pending);
            setPending(null);
          }
          if (pendingAction) {
            pendingAction();
            setPendingAction(null);
          }
        }}
      />
    </div>
  );
}
