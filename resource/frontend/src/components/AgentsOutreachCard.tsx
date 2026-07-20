import { useEffect, useState } from 'react';
import { fetchJson, ApiRequestError } from '../utils/api';
import { useRedState } from '../state/redUnlockStore';
import { UnlockModal } from './UnlockModal';

interface AgentKnobValues {
  agent_runtime: string;
  outreach_mail_channel: string;
  host_allowlist: string;
  verify_tls: boolean;
  http_timeout_s: number;
  heavy_timeout_s: number;
  default_llm_id: string;
  triage_score_threshold: number;
  triage_mail_channel: string;
  triage_recipient: string;
  auto_remediate_actions: string;
  auto_remediate_max_gb: number;
  auto_remediate_max_objects: number;
  python_run_timeout_seconds: number;
  log_cleanup_min_age_days: number;
  settings_set_blocked_extra: string;
}

interface ChoiceItem {
  id: string;
  label: string;
}

interface AgentKnobsResponse {
  ok: boolean;
  values: AgentKnobValues;
  mailChannels: ChoiceItem[];
  llms: ChoiceItem[];
}

interface SaveResponse {
  ok: boolean;
  values: AgentKnobValues;
  error?: string;
}

function SubHeading({ children }: { children: React.ReactNode }) {
  return <h4 className="text-sm font-semibold text-[var(--text-primary)] pt-1">{children}</h4>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-[var(--text-secondary)]">{label}</span>
      {children}
    </label>
  );
}

function ChoiceSelect({
  value,
  choices,
  emptyLabel,
  onChange,
}: {
  value: string;
  choices: ChoiceItem[];
  emptyLabel: string;
  onChange: (id: string) => void;
}) {
  const known = choices.some((c) => c.id === value);
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className="input-glass w-full">
      <option value="">{emptyLabel}</option>
      {choices.map((c) => (
        <option key={c.id} value={c.id}>
          {c.label}
        </option>
      ))}
      {value && !known && <option value={value}>{value} (not found)</option>}
    </select>
  );
}

/**
 * Settings card managing the agent + outreach plugin params that moved off the
 * DSS plugin-settings screen (they stay declared there, hidden, so DSS keeps
 * the values). Reads/writes the LOCAL instance's plugin config — saving is
 * advanced-gated like every other mutating settings surface.
 */
export function AgentsOutreachCard({
  onOpenPermissions,
}: {
  onOpenPermissions?: () => void;
}) {
  const [values, setValues] = useState<AgentKnobValues | null>(null);
  const [saved, setSaved] = useState<AgentKnobValues | null>(null);
  const [mailChannels, setMailChannels] = useState<ChoiceItem[]>([]);
  const [llms, setLlms] = useState<ChoiceItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState(false);

  const { authed: unlocked } = useRedState();
  const [showUnlock, setShowUnlock] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchJson<AgentKnobsResponse>('/api/settings/agents')
      .then((res) => {
        if (cancelled) return;
        setValues(res.values);
        setSaved(res.values);
        setMailChannels(res.mailChannels);
        setLlms(res.llms);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ApiRequestError ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const set = <K extends keyof AgentKnobValues>(key: K, v: AgentKnobValues[K]) => {
    setSavedMsg(false);
    setValues((prev) => (prev ? { ...prev, [key]: v } : prev));
  };

  const dirty = !!values && !!saved && JSON.stringify(values) !== JSON.stringify(saved);

  const doSave = async () => {
    if (!values) return;
    setSaving(true);
    setError(null);
    try {
      const res = await fetchJson<SaveResponse>('/api/settings/agents/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ values }),
      });
      setValues(res.values);
      setSaved(res.values);
      setSavedMsg(true);
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const requestSave = () => {
    if (!unlocked) {
      setShowUnlock(true);
      return;
    }
    void doSave();
  };

  return (
    <section className="glass-card p-4 space-y-3">
      <div>
        <h3 className="text-lg font-semibold text-[var(--text-primary)]">Agents &amp; Outreach</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Server-side configuration for outreach email and the plugin agents, stored in plugin
          settings. Triage and outreach changes apply on the next sweep/send; timeouts, allowlist
          and LLM apply when agent kernels next start.
        </p>
      </div>

      {error && <p className="text-sm text-[var(--neon-red)]">{error}</p>}
      {!values && !error && <p className="text-sm text-[var(--text-muted)]">Loading…</p>}

      {values && (
        <div className="space-y-3 max-w-3xl">
          <Field label="Agent runtime">
            <div className="inline-flex rounded-lg border border-[var(--border-default)] overflow-hidden">
              {(
                [
                  ['native', 'Native (in-process)'],
                  ['dataiku', 'Dataiku agent kernel'],
                ] as const
              ).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => set('agent_runtime', id)}
                  className={`px-3 py-1.5 text-xs font-medium transition-colors ${
                    values.agent_runtime === id
                      ? 'bg-[var(--accent)]/20 text-[var(--accent)]'
                      : 'text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </Field>
          <p className="text-xs text-[var(--text-muted)] -mt-1">
            Native runs the agent loop inside the toolkit backend — instant start, parallel tools,
            no kernel recycles, works without provisioned instances. The Dataiku kernel relay
            remains for remote hosts and as a fallback.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Outreach mail channel">
              <ChoiceSelect
                value={values.outreach_mail_channel}
                choices={mailChannels}
                emptyLabel="(auto — first mail channel)"
                onChange={(id) => set('outreach_mail_channel', id)}
              />
            </Field>
            <Field label="Default LLM (agents)">
              <ChoiceSelect
                value={values.default_llm_id}
                choices={llms}
                emptyLabel="(none — set per agent)"
                onChange={(id) => set('default_llm_id', id)}
              />
            </Field>
          </div>

          <SubHeading>Backend connection</SubHeading>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 items-end">
            <Field label="HTTP timeout (s)">
              <input
                type="number"
                className="input-glass w-full"
                value={values.http_timeout_s}
                onChange={(e) => set('http_timeout_s', Number(e.target.value))}
              />
            </Field>
            <Field label="Heavy-scan timeout (s)">
              <input
                type="number"
                className="input-glass w-full"
                value={values.heavy_timeout_s}
                onChange={(e) => set('heavy_timeout_s', Number(e.target.value))}
              />
            </Field>
            <Field label="Host allowlist">
              <input
                type="text"
                className="input-glass w-full"
                placeholder="all hosts"
                value={values.host_allowlist}
                onChange={(e) => set('host_allowlist', e.target.value)}
              />
            </Field>
            <label className="flex items-center gap-2 cursor-pointer pb-2">
              <input
                type="checkbox"
                checked={values.verify_tls}
                onChange={(e) => set('verify_tls', e.target.checked)}
                className="h-4 w-4 accent-[var(--accent)]"
              />
              <span className="text-xs font-medium text-[var(--text-secondary)]">Verify TLS</span>
            </label>
          </div>
          <p className="text-xs text-[var(--text-muted)]">
            Allowlist: comma-separated host ids agents may target; empty = all configured hosts.
          </p>

          <SubHeading>Daily triage</SubHeading>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <Field label="Score threshold">
              <input
                type="number"
                className="input-glass w-full"
                value={values.triage_score_threshold}
                onChange={(e) => set('triage_score_threshold', Number(e.target.value))}
              />
            </Field>
            <Field label="Mail channel">
              <ChoiceSelect
                value={values.triage_mail_channel}
                choices={mailChannels}
                emptyLabel="(auto — first mail channel)"
                onChange={(id) => set('triage_mail_channel', id)}
              />
            </Field>
            <Field label="Digest recipient">
              <input
                type="text"
                className="input-glass w-full"
                placeholder="admin@example.com"
                value={values.triage_recipient}
                onChange={(e) => set('triage_recipient', e.target.value)}
              />
            </Field>
          </div>

          <SubHeading>Auto-remediation</SubHeading>
          <p className="text-xs text-[var(--text-muted)]">
            Which actions the daily agent may run autonomously — plus its safety caps and
            remote-host scope — moved to{' '}
            <button
              type="button"
              onClick={() => onOpenPermissions?.()}
              className="text-[var(--accent)] hover:underline"
            >
              Agents → Permissions → Autonomous daily agent
            </button>
            , next to the rest of the agent capability grants.
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <Field label="Power-Up timeout (s)">
              <input
                type="number"
                className="input-glass w-full"
                value={values.python_run_timeout_seconds}
                onChange={(e) => set('python_run_timeout_seconds', Number(e.target.value))}
              />
            </Field>
            <Field label="Log cleanup min age (days)">
              <input
                type="number"
                className="input-glass w-full"
                value={values.log_cleanup_min_age_days}
                onChange={(e) => set('log_cleanup_min_age_days', Number(e.target.value))}
              />
            </Field>
          </div>
          <Field label="Extra blocked settings paths (CSV)">
            <input
              type="text"
              className="input-glass w-full"
              placeholder="e.g. security., ldapSettings."
              value={values.settings_set_blocked_extra}
              onChange={(e) => set('settings_set_blocked_extra', e.target.value)}
            />
          </Field>

          <div className="flex items-center gap-3 pt-1">
            <button
              type="button"
              onClick={requestSave}
              disabled={saving || !dirty}
              className="px-3 py-1.5 rounded bg-[var(--accent)]/20 text-[var(--accent)] hover:bg-[var(--accent)]/30 text-sm transition-colors disabled:opacity-50"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
            {savedMsg && !dirty && (
              <span className="text-sm text-[var(--text-secondary)]">Saved.</span>
            )}
            {!unlocked && dirty && (
              <span className="text-xs text-[var(--text-muted)]">
                Saving prompts for the master password.
              </span>
            )}
          </div>
        </div>
      )}

      <UnlockModal
        isOpen={showUnlock}
        onClose={() => setShowUnlock(false)}
        onUnlocked={() => {
          setShowUnlock(false);
          void doSave();
        }}
      />
    </section>
  );
}
