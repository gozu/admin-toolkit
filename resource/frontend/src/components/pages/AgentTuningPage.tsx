import { useCallback, useEffect, useMemo, useState } from 'react';
import { fetchJson } from '../../utils/api';
import { InfoDot } from '../common/InfoDot';
import { hostBaseUrl } from '../../utils/agentLinks';
import { ModelPicker } from '../ModelPicker';
import { reportLlmsStore } from '../../state/reportLlmsStore';
import type { LlmOption } from '../../types';

/**
 * Agent Tuning — customize the agent's prompts and model, versioned in a
 * Dataiku dataset (one column per prompt type plus an llm_override setting
 * column, one row per save; the newest row is the active version, an empty
 * cell means "built-in default"). Follows the prompt-registry playbook:
 * immutable version history with author/time/note, restore = load an old
 * version into the editors and save it as a new row.
 */

interface PromptTypeInfo {
  key: string;
  label: string;
  description: string;
  placeholders: string[];
  default: string;
  override: string | null;
}

interface VersionInfo {
  savedAt: string;
  author: string;
  note: string;
  customized: string[];
  values: Record<string, string>;
  llmOverride: string;
}

interface TuningState {
  available: boolean;
  reason?: string;
  datasetName: string;
  project: string;
  connection: string;
  promptTypes: PromptTypeInfo[];
  versions: VersionInfo[];
  llmOverride: string;
  pluginDefaultLlmId: string;
}

const COLUMN = 'w-full max-w-[64rem] mx-auto px-4';

function effective(pt: PromptTypeInfo): string {
  return pt.override ?? pt.default;
}

function formatWhen(iso: string): string {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? iso : new Date(t).toLocaleString();
}

function PlaceholderChips({ placeholders }: { placeholders: string[] }) {
  if (placeholders.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1">
      <span className="text-[10px] text-[var(--text-muted)]">Runtime placeholders (keep verbatim):</span>
      {placeholders.map((p) => (
        <code
          key={p}
          className="rounded bg-[var(--bg-surface)] border border-[var(--border-default)] px-1 py-0.5 text-[10px] text-[var(--text-secondary)]"
        >
          {p}
        </code>
      ))}
    </div>
  );
}

function PromptCard({
  pt,
  draft,
  onChange,
}: {
  pt: PromptTypeInfo;
  draft: string;
  onChange: (value: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const customized = draft.trim() !== pt.default.trim();
  const dirty = draft !== effective(pt);
  return (
    <div className="glass-card p-0 overflow-hidden">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 px-4 py-3 text-left hover:bg-[var(--bg-hover)] transition-colors"
      >
        <span className="text-[10px] text-[var(--accent)]">{expanded ? '▾' : '▸'}</span>
        <span className="text-sm font-semibold text-[var(--text-primary)]">{pt.label}</span>
        {customized && (
          <span className="rounded-full border border-[var(--accent)]/40 bg-[var(--accent-muted)] px-2 py-0.5 text-[10px] font-semibold text-[var(--accent)]">
            customized
          </span>
        )}
        {dirty && (
          <span className="rounded-full border border-[var(--neon-yellow)]/40 px-2 py-0.5 text-[10px] font-semibold text-[var(--neon-yellow)]">
            unsaved
          </span>
        )}
        <span className="ml-auto text-[10px] text-[var(--text-muted)]">
          {draft.length.toLocaleString()} chars
        </span>
      </button>
      {expanded && (
        <div className="space-y-2 border-t border-[var(--border-default)] px-4 py-3">
          <p className="text-xs text-[var(--text-secondary)] leading-relaxed">{pt.description}</p>
          <PlaceholderChips placeholders={pt.placeholders} />
          <textarea
            value={draft}
            onChange={(e) => onChange(e.target.value)}
            rows={14}
            spellCheck={false}
            className="w-full resize-y rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)] px-3 py-2 font-mono text-xs leading-relaxed text-[var(--text-primary)] focus:border-[var(--accent)] focus:outline-none"
          />
          <div className="flex items-center gap-2">
            {customized && (
              <button
                onClick={() => onChange(pt.default)}
                className="rounded-md border border-[var(--border-default)] px-2.5 py-1 text-[11px] text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-hover)]"
              >
                Reset to built-in default
              </button>
            )}
            {dirty && (
              <button
                onClick={() => onChange(effective(pt))}
                className="rounded-md border border-[var(--border-default)] px-2.5 py-1 text-[11px] text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-hover)]"
              >
                Discard edits
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ModelOverrideCard({
  savedOverride,
  draft,
  pluginDefaultLlmId,
  onChange,
}: {
  savedOverride: string;
  draft: string;
  pluginDefaultLlmId: string;
  onChange: (id: string) => void;
}) {
  const { llms, loading, loaded, error } = reportLlmsStore.use();
  useEffect(() => {
    void reportLlmsStore.load();
  }, []);
  const customized = draft !== '';
  const dirty = draft !== savedOverride;
  const missing = customized && loaded && !error && !llms.some((l) => l.id === draft);
  // Keep a not-in-catalog override visible (and re-selectable) in the picker
  // instead of silently showing the placeholder.
  const options = useMemo<LlmOption[]>(
    () =>
      missing
        ? [...llms, { id: draft, label: draft, type: '', connection: 'not in catalog', model: draft }]
        : llms,
    [llms, missing, draft],
  );
  return (
    <div className="glass-card p-4 space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold text-[var(--text-primary)]">Model</span>
        {customized && (
          <span className="rounded-full border border-[var(--accent)]/40 bg-[var(--accent-muted)] px-2 py-0.5 text-[10px] font-semibold text-[var(--accent)]">
            customized
          </span>
        )}
        {dirty && (
          <span className="rounded-full border border-[var(--neon-yellow)]/40 px-2 py-0.5 text-[10px] font-semibold text-[var(--neon-yellow)]">
            unsaved
          </span>
        )}
        {missing && (
          <span
            className="rounded-full border border-[var(--danger)]/40 px-2 py-0.5 text-[10px] font-semibold text-[var(--danger)]"
            title="The saved id is not in this instance's LLM catalog — agent turns will fail until you pick an existing model or clear the override."
          >
            not in LLM catalog
          </span>
        )}
      </div>
      <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
        Override the LLM all three agents run on. The override is versioned with the prompts
        (one save = one snapshot) and wins over each agent&apos;s own <code className="text-[11px]">llm_id</code> and
        the plugin&apos;s <code className="text-[11px]">default_llm_id</code>.
      </p>
      <div className="flex items-center gap-2">
        <div className="flex-1 min-w-0">
          {loading && !loaded ? (
            <div className="rounded-md border border-[var(--border-default)] bg-[var(--bg-elevated)] px-3 py-2 text-sm text-[var(--text-tertiary)]">
              Loading models…
            </div>
          ) : (
            <ModelPicker
              llms={options}
              selectedId={draft}
              onChange={onChange}
              placeholder="No override — use the configured model"
              className="w-full rounded-md border border-[var(--border-default)] bg-[var(--bg-elevated)] text-[var(--text-primary)] px-3 py-2 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-[var(--accent)]"
            />
          )}
        </div>
        {customized && (
          <button
            onClick={() => onChange('')}
            className="shrink-0 rounded-md border border-[var(--border-default)] px-2.5 py-1.5 text-[11px] text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-hover)]"
          >
            Clear override
          </button>
        )}
      </div>
      <p className="text-[11px] text-[var(--text-muted)]">
        {customized ? (
          <>
            Agents will use <code className="text-[10px]">{draft}</code> (Agent Tuning override).
          </>
        ) : (
          <>
            No override — each agent uses its own <code className="text-[10px]">llm_id</code> if set,
            else the plugin default
            {pluginDefaultLlmId ? (
              <>
                {' '}
                <code className="text-[10px]">{pluginDefaultLlmId}</code>
              </>
            ) : (
              ' (currently not set)'
            )}
            .
          </>
        )}
      </p>
      {error && (
        <p className="text-[11px] text-[var(--text-muted)]">
          Could not load the LLM catalog ({error}) — you can still clear the override.
        </p>
      )}
    </div>
  );
}

export function AgentTuningPage() {
  const [state, setState] = useState<TuningState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [draftOverride, setDraftOverride] = useState('');
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedFlash, setSavedFlash] = useState(false);

  const applyState = useCallback((data: TuningState) => {
    setState(data);
    const next: Record<string, string> = {};
    for (const pt of data.promptTypes || []) next[pt.key] = effective(pt);
    setDrafts(next);
    setDraftOverride(data.llmOverride || '');
  }, []);

  useEffect(() => {
    fetchJson<TuningState>('/api/agents/tuning')
      .then((data) => {
        if (data.available) applyState(data);
        else setLoadError(data.reason || 'Agent tuning is unavailable on this host.');
      })
      .catch((err) => setLoadError(String(err)));
  }, [applyState]);

  const dirtyKeys = useMemo(() => {
    if (!state) return [];
    return state.promptTypes.filter((pt) => (drafts[pt.key] ?? '') !== effective(pt)).map((pt) => pt.key);
  }, [state, drafts]);
  const overrideDirty = state !== null && draftOverride !== (state.llmOverride || '');
  const dirtyCount = dirtyKeys.length + (overrideDirty ? 1 : 0);

  const save = useCallback(() => {
    if (!state || saving) return;
    setSaving(true);
    setSaveError(null);
    fetchJson<TuningState>('/api/agents/tuning/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note, values: drafts, settings: { llm_override: draftOverride } }),
    })
      .then((data) => {
        applyState(data);
        setNote('');
        setSavedFlash(true);
        setTimeout(() => setSavedFlash(false), 4000);
      })
      .catch((err) => {
        const locked = (err as { body?: { error?: string } }).body?.error === 'advanced-locked';
        setSaveError(
          locked
            ? 'Advanced actions are locked — unlock them (toolbar pill) and retry.'
            : String(err),
        );
      })
      .finally(() => setSaving(false));
  }, [state, saving, note, drafts, draftOverride, applyState]);

  const restore = useCallback(
    (version: VersionInfo) => {
      if (!state) return;
      const next: Record<string, string> = {};
      for (const pt of state.promptTypes) {
        const value = version.values[pt.key] || '';
        next[pt.key] = value.trim() ? value : pt.default;
      }
      setDrafts(next);
      setDraftOverride(version.llmOverride || '');
      setNote(`restore ${formatWhen(version.savedAt)}`);
      window.scrollTo({ top: 0 });
    },
    [state],
  );

  if (loadError) {
    return (
      <div className="w-full flex-1 py-6">
        <div className={COLUMN}>
          <div className="glass-card p-6 max-w-lg space-y-2">
            <h3 className="text-sm font-semibold text-[var(--text-primary)]">Agent tuning unavailable</h3>
            <p className="text-sm text-[var(--text-secondary)] leading-relaxed">{loadError}</p>
          </div>
        </div>
      </div>
    );
  }

  if (!state) {
    return (
      <div className="flex-1 flex items-center justify-center py-20">
        <div className="w-6 h-6 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  const activeVersion = state.versions[0];

  return (
    <div className="w-full flex-1 min-h-0 py-4 space-y-3 overflow-y-auto">
      <div className={`${COLUMN} space-y-3`}>
        {/* Header + guidance */}
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">Agent Tuning</h2>
          <InfoDot eduId="agent.unified" />
          <span className="text-xs text-[var(--text-tertiary)]">
            prompts + model · versioned in dataset{' '}
            <a
              href={`${hostBaseUrl(undefined)}/projects/${state.project}/datasets/${state.datasetName}/explore/`}
              target="_blank"
              rel="noreferrer"
              className="text-[var(--accent)] hover:underline"
            >
              {state.project}.{state.datasetName} ↗
            </a>
          </span>
        </div>
        <div className="glass-card p-4 space-y-1.5 border-l-2 border-l-[var(--accent)]">
          <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
            Tune how the agent thinks by editing its prompts and picking its model. Every save
            appends one row to the version dataset — one column per prompt type, plus the model
            override — with your author id, a note and a timestamp; the newest row is what the
            agent uses. Nothing is ever overwritten: restoring an older version just saves it
            again as the newest row.
          </p>
          <ul className="text-xs text-[var(--text-secondary)] leading-relaxed list-disc pl-4 space-y-0.5">
            <li>
              Changes take effect on the next agent turn (the agents refresh their prompts and
              model about once a minute) — no restart needed.
            </li>
            <li>
              Keep the <code className="text-[11px]">{'{placeholder}'}</code> tokens verbatim — the
              runtime substitutes live values (rubrics, action catalogs, limits) into them.
            </li>
            <li>
              The model override here wins over the per-agent and plugin-settings model choice;
              tool allowlists and execution gates still live in the DSS plugin settings
              (Plugins → Admin Toolkit → Settings).
            </li>
            <li>Test a change right after saving: run a sample prompt on the Agents page.</li>
          </ul>
        </div>

        {/* Model override */}
        <ModelOverrideCard
          savedOverride={state.llmOverride || ''}
          draft={draftOverride}
          pluginDefaultLlmId={state.pluginDefaultLlmId || ''}
          onChange={setDraftOverride}
        />

        {/* Editors */}
        <div className="space-y-2">
          {state.promptTypes.map((pt) => (
            <PromptCard
              key={pt.key}
              pt={pt}
              draft={drafts[pt.key] ?? ''}
              onChange={(value) => setDrafts((d) => ({ ...d, [pt.key]: value }))}
            />
          ))}
        </div>

        {/* Save bar */}
        {(dirtyCount > 0 || savedFlash || saveError) && (
          <div className="glass-card p-3 space-y-2 border-l-2 border-l-[var(--neon-yellow)]">
            {dirtyCount > 0 ? (
              <>
                <div className="text-xs text-[var(--text-secondary)]">
                  Unsaved changes:{' '}
                  {[
                    dirtyKeys.length > 0 ? `${dirtyKeys.length} prompt${dirtyKeys.length > 1 ? 's' : ''}` : null,
                    overrideDirty ? 'model override' : null,
                  ]
                    .filter(Boolean)
                    .join(' + ')}
                </div>
                <div className="flex items-center gap-2">
                  <input
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="What changed and why? (version note)"
                    className="flex-1 rounded-md border border-[var(--border-default)] bg-[var(--bg-surface)] px-2.5 py-1.5 text-xs text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:border-[var(--accent)] focus:outline-none"
                  />
                  <button
                    onClick={save}
                    disabled={saving}
                    className="rounded-lg bg-[var(--accent)] px-3.5 py-1.5 text-xs font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-60"
                  >
                    {saving ? 'Saving…' : 'Save new version'}
                  </button>
                </div>
              </>
            ) : savedFlash ? (
              <div className="text-xs text-[var(--text-secondary)]">
                ✓ Version saved — it is now the active version.
              </div>
            ) : null}
            {saveError && <p className="text-xs text-[var(--danger)]">{saveError}</p>}
          </div>
        )}

        {/* Version history */}
        <div className="glass-card p-4 space-y-2">
          <div className="flex items-center gap-2">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
              Version history
            </h3>
            <span className="text-[10px] text-[var(--text-muted)]">
              {state.versions.length === 0
                ? 'no saved versions — the built-in defaults are active'
                : `${state.versions.length} most recent, newest first`}
            </span>
          </div>
          {state.versions.map((version, i) => (
            <div
              key={`${version.savedAt}-${i}`}
              className={`flex items-center gap-2 rounded-md border px-2.5 py-1.5 ${
                version === activeVersion
                  ? 'border-[var(--accent)]/40 bg-[var(--accent-muted)]'
                  : 'border-[var(--border-default)] bg-[var(--bg-surface)]'
              }`}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-xs text-[var(--text-primary)]">
                  <span>{formatWhen(version.savedAt)}</span>
                  {version === activeVersion && (
                    <span className="rounded-full border border-[var(--accent)]/40 px-1.5 py-0 text-[10px] font-semibold text-[var(--accent)]">
                      active
                    </span>
                  )}
                  <span className="text-[var(--text-muted)]">{version.author}</span>
                </div>
                <div className="truncate text-[10px] text-[var(--text-muted)]">
                  {version.note || 'no note'} ·{' '}
                  {version.customized.length > 0
                    ? `customized: ${version.customized.join(', ')}`
                    : 'all defaults'}
                  {version.llmOverride ? ` · model: ${version.llmOverride}` : ''}
                </div>
              </div>
              <button
                onClick={() => restore(version)}
                className="shrink-0 rounded-md border border-[var(--border-default)] px-2.5 py-1 text-[11px] text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-hover)]"
                title="Load this version into the editors — saving then makes it the active version"
              >
                Load
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
