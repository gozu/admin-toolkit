import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { fetchJson } from '../../utils/api';
import { InfoDot } from '../common/InfoDot';
import { MessageView } from '../agents/MessageView';
import { PromptLibrary } from '../agents/PromptLibrary';
import { PendingApprovalsBar } from '../agents/PendingApprovalsBar';
import { AuditTimeline } from '../agents/AuditTimeline';
import { SettingsHistoryCard } from '../agents/SettingsHistoryCard';
import {
  PROMPT_GROUPS,
  filterPaletteEntries,
  groupForRole,
  type CatalogGroup,
  type PaletteEntry,
} from '../../utils/agentPromptCatalog';
import { hostBaseUrl } from '../../utils/agentLinks';
import { dssUrls } from '../../utils/codeEnvUsageLinks';
import { getActiveHostId } from '../../state/hostStore';
import { AgentOrb, type OrbState } from '../agents/AgentOrb';
import { OrbPlanets } from '../agents/OrbPlanets';
import { ChatHistoryDrawer } from '../agents/ChatHistoryDrawer';
import { ComposerPalette } from '../agents/ComposerPalette';
import { Spinner } from '../common/Spinner';
import {
  abortAgentTurn,
  agentsChatStore,
  approvePlans,
  clearAllConversations,
  deriveTitle,
  ensureChatBootstrapped,
  provisionAgents,
  provisionTraceExplorer,
  rejectPlans,
  retryLastTurn,
  selectAgent,
  sendAgentMessage,
  submitActionItemsToActuator,
  type ActionItemData,
  type AgentInfo,
  type ConversationMeta,
  type PlanCardData,
  type PresetMeta,
  type ProvisionResult,
} from '../../state/agentsChatStore';

interface AgentsListResponse {
  available: boolean;
  agents: AgentInfo[];
  reason?: string;
  projectKey: string;
}

/** ONE provisioned generalist since 4c — prefer it by name so a host mid-
 * migration (stale specialist instances still listed) never grabs a retired
 * one. Library-prompt roles are pure FLAVOR now: they shape the prompt text,
 * never the routing. */
function findAgent(agents: AgentInfo[]): AgentInfo | undefined {
  return agents.find((a) => /admin agent|generalist/i.test(a.name)) || agents[0];
}

/** First prompt of each section, then seconds, until `count` — a spread of
 * samples across the group's themes rather than one section's list. */
function samplePrompts(group: CatalogGroup, count: number) {
  const out: { id: string; label: string; prompt: string }[] = [];
  for (let depth = 0; out.length < count; depth++) {
    let added = false;
    for (const section of group.sections) {
      const p = section.prompts[depth];
      if (p && out.length < count) {
        out.push(p);
        added = true;
      }
    }
    if (!added) break;
  }
  return out;
}

// Shared fluid column: near full width, capped at 1400px. Header, transcript,
// composer, and audit block all use it so the page reads as one column.
const COLUMN = 'w-full max-w-[87.5rem] mx-auto px-4';

// A typed-but-unsent draft survives navigation and reloads.
const DRAFT_STORAGE_KEY = 'admin-toolkit:agentDraft';

function readStoredDraft(): string {
  try {
    return globalThis.localStorage?.getItem(DRAFT_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function BookIcon() {
  return (
    <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.25c-2-1.5-4.5-2-7-2v13.5c2.5 0 5 .5 7 2 2-1.5 4.5-2 7-2V4.25c-2.5 0-5 .5-7 2zM12 6.25v13.5" />
    </svg>
  );
}

export function AgentsPage() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [unavailableReason, setUnavailableReason] = useState<string | null>(null);
  const [loadingAgents, setLoadingAgents] = useState(true);
  const [draft, setDraft] = useState(readStoredDraft);
  const [paletteDismissed, setPaletteDismissed] = useState(false);
  const [paletteIndex, setPaletteIndex] = useState(0);
  const [atBottom, setAtBottom] = useState(true);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [provisioning, setProvisioning] = useState(false);
  const [provisionResult, setProvisionResult] = useState<ProvisionResult | null>(null);
  const [provisionError, setProvisionError] = useState<string | null>(null);
  // The steps card serves both one-click setups — this picks its wording.
  const [provisionKind, setProvisionKind] = useState<'agents' | 'trace-explorer'>('trace-explorer');
  const [focusAuditId, setFocusAuditId] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const stickToBottomRef = useRef(true);

  const chatState = agentsChatStore.use();
  const selectedId = chatState.selectedAgentId;
  const activeConvId = selectedId ? chatState.activeConvIdByAgent[selectedId] : undefined;
  const conversation = activeConvId ? chatState.conversations[activeConvId] : undefined;
  const messages = useMemo(() => conversation?.messages ?? [], [conversation]);
  // Any agent (visible or hidden role) with an active non-empty conversation —
  // "New conversation" resets the whole session, so it shows whenever any exists.
  const hasAnyChat = useMemo(
    () =>
      Object.values(chatState.activeConvIdByAgent).some(
        (convId) => (chatState.conversations[convId]?.messages.length ?? 0) > 0,
      ),
    [chatState.activeConvIdByAgent, chatState.conversations],
  );
  const streaming = conversation?.streaming ?? false;

  // Unsent drafts survive navigation + reload (cleared on send).
  useEffect(() => {
    try {
      if (draft) globalThis.localStorage?.setItem(DRAFT_STORAGE_KEY, draft);
      else globalThis.localStorage?.removeItem(DRAFT_STORAGE_KEY);
    } catch {
      // storage unavailable — draft just won't survive
    }
  }, [draft]);

  const agent = useMemo(() => findAgent(agents), [agents]);
  // A conversation whose agent no longer exists (a retired pre-4c specialist)
  // stays readable but cannot continue — its kernel is gone.
  const conversationOrphaned = Boolean(
    conversation && agents.length > 0 && !agents.some((a) => a.id === conversation.agentId),
  );

  // Deep link to the conversation agent's DSS config screen — powers the
  // gate-hint callout when an execute is refused for allow_red_actions=false.
  const agentConfigUrl = useMemo(() => {
    const gateAgent = agents.find((a) => a.id === conversation?.agentId) || agent;
    if (!gateAgent) return undefined;
    return dssUrls.agentConfig(
      gateAgent.projectKey ?? 'ADMINTOOLKIT',
      gateAgent.id,
      gateAgent.activeVersion ?? 'v1',
    );
  }, [agents, conversation, agent]);

  // Chat persistence config + trace-explorer status, once per host — the
  // session epoch (host switch / in-app Refresh) resets `persistence.loaded`,
  // which re-triggers this effect against the new host.
  useEffect(() => {
    if (!chatState.persistence.loaded) void ensureChatBootstrapped();
  }, [chatState.persistence.loaded]);

  const loadAgents = useCallback(
    () =>
      fetchJson<AgentsListResponse>('/api/agents')
        .then((data) => {
          setAgents(data.agents);
          setUnavailableReason(
            data.available ? null : data.reason || 'Agents plugin not provisioned',
          );
          if (data.agents.length > 0) {
            const current = agentsChatStore.get().selectedAgentId;
            if (!current || !data.agents.some((a) => a.id === current)) {
              const preferred = findAgent(data.agents);
              if (preferred) selectAgent(preferred.id);
            }
          }
        })
        .catch((err) => setUnavailableReason(String(err))),
    [],
  );

  useEffect(() => {
    void loadAgents().finally(() => setLoadingAgents(false));
  }, [loadAgents]);

  // Tick for plan-expiry countdowns and the streaming elapsed counter — only
  // while an undecided plan is visible or a turn is in flight.
  const hasPendingPlan = useMemo(
    () =>
      messages.some((msg) =>
        msg.segments.some((seg) => seg.type === 'plan' && !seg.plan.decision && seg.plan.expiresAt > now),
      ),
    [messages, now],
  );
  useEffect(() => {
    if (!hasPendingPlan && !streaming) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [hasPendingPlan, streaming]);

  // Live turn feedback: the tool currently running (last running activity
  // item of the streaming reply) plus wall-clock elapsed.
  const runningTool = useMemo(() => {
    if (!streaming) return null;
    const last = messages[messages.length - 1];
    if (!last || last.role !== 'assistant') return null;
    for (let i = last.segments.length - 1; i >= 0; i--) {
      const seg = last.segments[i];
      if (seg.type !== 'activity') continue;
      const running = seg.items.filter((it) => it.running);
      if (running.length > 0) return running[running.length - 1].name;
    }
    return null;
  }, [messages, streaming]);
  const elapsedSec =
    streaming && conversation?.streamStartedAt
      ? Math.max(0, Math.floor((now - conversation.streamStartedAt) / 1000))
      : null;

  // Undecided, unexpired plans across the conversation → batch approvals bar.
  // python-run is hard-excluded: its per-run "I have read this code" ack only
  // exists on the individual plan card, so it can never ride a batch approval.
  const pendingPlans = useMemo(() => {
    const out: PlanCardData[] = [];
    for (const msg of messages) {
      for (const seg of msg.segments) {
        if (
          seg.type === 'plan' &&
          !seg.plan.decision &&
          seg.plan.expiresAt > now &&
          seg.plan.action !== 'python-run'
        )
          out.push(seg.plan);
      }
    }
    return out;
  }, [messages, now]);

  // Autoscroll while streaming, unless the user scrolled up. The empty-state
  // hero anchors to the top instead — pinning it to the bottom clips the bird
  // mark when the hero is taller than the viewport (composer autofocus would
  // otherwise drag the scroll down too).
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (messages.length === 0) el.scrollTop = 0;
    else if (stickToBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [messages, streaming]);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const stick = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    stickToBottomRef.current = stick;
    setAtBottom(stick);
  }, []);

  const jumpToLatest = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottomRef.current = true;
    setAtBottom(true);
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, []);

  // Slash palette: "/" at the start of the draft searches the prompt catalog
  // inline. Esc dismisses until the draft next changes.
  const paletteMatches = useMemo<PaletteEntry[]>(
    () => (draft.startsWith('/') ? filterPaletteEntries(draft.slice(1)) : []),
    [draft],
  );
  const paletteOpen =
    paletteMatches.length > 0 && !paletteDismissed && !streaming && !conversationOrphaned;

  const pickPrompt = useCallback((entry: PaletteEntry) => {
    setDraft(entry.prompt);
    setPaletteDismissed(false);
    setPaletteIndex(0);
    composerRef.current?.focus();
  }, []);

  // Composer grows with content (wrap-aware, capped) instead of counting '\n'.
  useEffect(() => {
    const el = composerRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [draft]);

  // Keyboard lands in the composer on arrival and when a turn settles.
  useEffect(() => {
    if (!loadingAgents && agent && !streaming && !conversationOrphaned) {
      composerRef.current?.focus();
    }
  }, [loadingAgents, agent, streaming, conversationOrphaned]);

  /** One agent, one thread: every message — free-form or library prompt of
   * any flavor — goes to the generalist. The library's role/group concept
   * shapes prompt text only, never the routing, so it never reaches here. */
  const send = useCallback(
    (text: string, preset?: PresetMeta) => {
      const trimmed = text.trim();
      if (!trimmed || streaming || !agent || conversationOrphaned) return;
      setDraft('');
      stickToBottomRef.current = true;
      if (agent.id !== selectedId) selectAgent(agent.id);
      void sendAgentMessage(agent.id, trimmed, undefined, preset);
    },
    [agent, selectedId, streaming, conversationOrphaned],
  );

  const sendDraft = send;

  const insertPrompt = useCallback((prompt: string) => {
    setDraft(prompt);
    composerRef.current?.focus();
  }, []);

  // Settings-history "Restore…": prefill the composer for review; the
  // generalist's write protocol takes it from there.
  const onRestore = useCallback((prompt: string) => {
    setDraft(prompt);
    composerRef.current?.focus();
  }, []);

  const onPlanDecision = useCallback(
    (plan: PlanCardData, decision: 'approved' | 'rejected') => {
      if (!selectedId) return;
      stickToBottomRef.current = true;
      if (decision === 'approved') approvePlans(selectedId, [plan]);
      else rejectPlans(selectedId, [plan]);
    },
    [selectedId],
  );

  const onSubmitActionItems = useCallback(
    (batchId: string, items: ActionItemData[]) => {
      if (!selectedId || !agent) return;
      stickToBottomRef.current = true;
      // Single agent: the "handoff" is just the next message in this thread.
      submitActionItemsToActuator(selectedId, agent.id, batchId, items);
    },
    [selectedId, agent],
  );

  const onProvisionAgents = useCallback(() => {
    setProvisioning(true);
    setProvisionError(null);
    setProvisionResult(null);
    setProvisionKind('agents');
    provisionAgents()
      .then((result) => {
        setProvisionResult(result);
        if (result.ok) void loadAgents();
      })
      .catch((err) => setProvisionError(String(err)))
      .finally(() => setProvisioning(false));
  }, [loadAgents]);

  const onProvisionTraceExplorer = useCallback(() => {
    setProvisioning(true);
    setProvisionError(null);
    setProvisionResult(null);
    setProvisionKind('trace-explorer');
    provisionTraceExplorer()
      .then(setProvisionResult)
      .catch((err) => {
        const locked = (err as { body?: { error?: string } }).body?.error === 'advanced-locked';
        setProvisionError(
          locked
            ? 'Advanced actions are locked — unlock them (toolbar pill) and retry.'
            : String(err),
        );
      })
      .finally(() => setProvisioning(false));
  }, []);

  // History covers server-persisted chats plus local (not-yet-persisted)
  // ones, so past conversations stay reachable even without chat storage.
  const historyList = useMemo<ConversationMeta[]>(() => {
    const serverIds = new Set(chatState.conversationList.map((c) => c.id));
    const locals = Object.values(chatState.conversations)
      .filter((c) => !serverIds.has(c.id) && c.messages.length > 0)
      .map((c) => ({ id: c.id, agentId: c.agentId, title: c.title || deriveTitle(c) }));
    return [...chatState.conversationList, ...locals];
  }, [chatState.conversationList, chatState.conversations]);

  // Distinctive phrases from the real gate/lock messages only — a bare /red/
  // matched "requi_red_"/"expi_red_" and lit the kill-switch dot on unrelated
  // errors (e.g. the "…are required" 400 and confirm-token expiry).
  const errorIsGate =
    /kill-switch|master switch|master password|disabled in agent settings|agentic[- ]actions/i.test(
      conversation?.error || '',
    );
  const traceExplorer = chatState.traceExplorer;
  const explorerViewPath = traceExplorer?.viewPath || conversation?.traceExplorerPath;
  const heroGroups = useMemo(
    () => [groupForRole('triage'), groupForRole('scoping')],
    [],
  );

  // The orb mirrors the agent's live state everywhere it appears.
  const orbState: OrbState = streaming
    ? runningTool
      ? 'tool'
      : 'thinking'
    : conversation?.error
      ? 'error'
      : 'idle';

  return (
    <div className="w-full flex-1 min-h-0 flex flex-col gap-3 py-4">
      {/* Header: one agent, one identity */}
      <div className={`${COLUMN} flex items-center gap-2 flex-wrap`}>
        <span className="inline-flex items-center gap-1.5">
          <AgentOrb size={22} state={orbState} className="mr-0.5" />
          <span className="text-sm font-semibold text-[var(--text-primary)]">
            Admin Toolkit Agent
          </span>
          <InfoDot eduId="agent.unified" />
        </span>
        {conversation && messages.length > 0 ? (
          <span
            className="min-w-0 max-w-[24rem] truncate text-xs text-[var(--text-tertiary)]"
            title={conversation.title || deriveTitle(conversation)}
          >
            — {conversation.title || deriveTitle(conversation)}
          </span>
        ) : (
          <span className="text-xs text-[var(--text-tertiary)]">
            health · triage · scoping · admin actions
          </span>
        )}
        <span className="ml-auto inline-flex items-center gap-2">
          {explorerViewPath ? (
            <a
              href={`${hostBaseUrl(getActiveHostId())}${explorerViewPath}`}
              target="_blank"
              rel="noreferrer"
              className="px-2.5 py-1.5 rounded-lg text-xs text-[var(--text-tertiary)] border border-[var(--border-default)] hover:bg-[var(--bg-hover)] transition-colors"
              title="Native DSS Trace Explorer over the agent interaction-logging dataset"
            >
              Trace Explorer ↗
            </a>
          ) : traceExplorer?.installed ? (
            <button
              onClick={onProvisionTraceExplorer}
              disabled={provisioning}
              className="px-2.5 py-1.5 rounded-lg text-xs text-[var(--accent)] border border-[var(--accent)]/40 bg-[var(--accent-muted)] hover:brightness-110 transition-[filter] disabled:opacity-60"
              title="Create + configure the Trace Explorer webapp in ADMINTOOLKIT over the agent interaction-logging dataset"
            >
              {provisioning ? 'Setting up…' : 'Set up Trace Explorer'}
            </button>
          ) : traceExplorer && !traceExplorer.installed ? (
            <a
              href={`${hostBaseUrl(getActiveHostId())}/plugins/traces-explorer/summary/`}
              target="_blank"
              rel="noreferrer"
              className="px-2.5 py-1.5 rounded-lg text-xs text-[var(--text-tertiary)] border border-[var(--border-default)] hover:bg-[var(--bg-hover)] transition-colors"
              title="The Dataiku Traces Explorer plugin is not installed on this host — install it, then the toolkit can provision the webapp"
            >
              Install Traces Explorer plugin ↗
            </a>
          ) : null}
          <button
            onClick={() => setHistoryOpen(true)}
            className="px-2.5 py-1.5 rounded-lg text-xs text-[var(--text-tertiary)] border border-[var(--border-default)] hover:bg-[var(--bg-hover)] transition-colors"
            title={
              chatState.persistence.enabled
                ? 'Saved conversations on this host (server-side, per user)'
                : 'Past conversations in this browser — enable chat storage in the plugin settings for durable history'
            }
          >
            History
          </button>
          {hasAnyChat && (
            <button
              onClick={() => clearAllConversations()}
              className="px-2.5 py-1.5 rounded-lg text-xs text-[var(--text-tertiary)] border border-[var(--border-default)] hover:bg-[var(--bg-hover)] transition-colors"
            >
              New conversation
            </button>
          )}
        </span>
      </div>

      {(provisionResult || provisionError) && (
        <div className={COLUMN}>
          <div className="glass-card p-3 space-y-1.5 text-xs border-l-2 border-l-[var(--accent)]">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-[var(--text-primary)]">
                {(() => {
                  const label = provisionKind === 'agents' ? 'Agents' : 'Trace Explorer';
                  if (provisionError) return `${label} setup failed`;
                  return provisionResult?.ok ? `${label} ready` : `${label} setup incomplete`;
                })()}
              </span>
              <button
                onClick={() => {
                  setProvisionResult(null);
                  setProvisionError(null);
                }}
                className="ml-auto rounded p-1 text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
                aria-label="Dismiss"
              >
                ✕
              </button>
            </div>
            {provisionError && <p className="text-[var(--danger)]">{provisionError}</p>}
            {(provisionResult?.steps || []).map((step) => (
              <div key={step.step} className="flex items-baseline gap-2">
                <span
                  className={
                    step.status === 'error' ? 'text-[var(--danger)]' : 'text-[var(--text-secondary)]'
                  }
                >
                  {step.status === 'error' ? '✗' : '✓'} {step.step}
                </span>
                <span className="text-[var(--text-muted)] truncate">
                  {step.status !== 'error' && step.status !== 'ok' ? `${step.status} ` : ''}
                  {step.message || ''}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {loadingAgents ? (
        <div className="flex-1 flex items-center justify-center">
          <Spinner size="w-6 h-6" color="border-[var(--accent)]" />
        </div>
      ) : agents.length === 0 ? (
        <div className={COLUMN}>
          <div className="glass-card p-6 max-w-lg space-y-3">
            <h3 className="text-sm font-semibold text-[var(--text-primary)]">No agents on this host</h3>
            <p className="text-sm text-[var(--text-secondary)] leading-relaxed">
              The Admin Toolkit agents are not provisioned here yet. One click creates the agent
              and tool instances in the ADMINTOOLKIT project on this host — no CLI needed.
              {unavailableReason ? ` (${unavailableReason})` : ''}
            </p>
            <button
              onClick={onProvisionAgents}
              disabled={provisioning}
              className="px-3 py-1.5 text-xs font-semibold rounded-md bg-[var(--accent)] text-white hover:opacity-90 transition-opacity disabled:opacity-60"
            >
              {provisioning ? 'Setting up…' : 'Set up agents'}
            </button>
          </div>
        </div>
      ) : (
        <>
          {/* Transcript — centered column, bubbles never touch the edges */}
          <div className="relative flex-1 min-h-0">
            {/* Ambient aurora — drifts behind the conversation, brightens while
                the agent works. Purely decorative, never intercepts input. */}
            <div className="agent-aurora" data-live={streaming ? 'true' : 'false'} aria-hidden="true">
              <span />
              <span />
              <span />
            </div>
            <div
              ref={scrollRef}
              onScroll={handleScroll}
              className="chat-scroll-fade relative z-[1] h-full overflow-y-auto scroll-smooth"
            >
              <div className={`${COLUMN} space-y-4`}>
              {messages.length === 0 && (
                <div className="pt-6 flex flex-col items-center gap-6 text-center">
                  <div className="relative flex items-center justify-center w-[10rem] h-[10rem]">
                    <OrbPlanets />
                    <AgentOrb size={88} state="idle" className="orb-bird-white z-[1]" />
                  </div>
                  <div className="space-y-2">
                    <h2
                      className="hero-shimmer text-4xl font-bold tracking-tight"
                      style={{ fontFamily: 'var(--font-display)' }}
                    >
                      Your fleet, on command.
                    </h2>
                    <p className="text-base text-[var(--text-secondary)]">
                      Ask about fleet health, sizing and scoping, or admin maintenance — or start
                      from a sample below.
                    </p>
                  </div>
                  <div className="grid gap-4 w-full max-w-5xl sm:grid-cols-2 text-left">
                    {heroGroups.map((group, gi) => (
                      <motion.div
                        key={group.role}
                        initial={{ opacity: 0, y: 16, filter: 'blur(5px)' }}
                        animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
                        transition={{ duration: 0.45, delay: 0.12 + gi * 0.1, ease: 'easeOut' }}
                        className="glass-card hero-card p-5 space-y-3 border-l-2 border-l-[var(--accent)]"
                      >
                        <div className="text-sm font-bold uppercase tracking-widest text-[var(--accent)]">
                          {group.title}
                        </div>
                        <p className="text-sm text-[var(--text-secondary)] leading-relaxed">
                          {group.blurb}
                        </p>
                        <button
                          onClick={() =>
                            send(group.megaprompt, {
                              kind: 'megaprompt',
                              title: group.megapromptTitle,
                              group: group.title,
                              gist: group.megapromptBlurb,
                              icon: 'star',
                            })
                          }
                          className="px-4 py-2 text-sm font-semibold rounded-lg bg-[var(--accent)] text-white hover:opacity-90 transition-opacity"
                        >
                          ★ {group.megapromptTitle}
                        </button>
                        <div className="flex flex-col gap-2 pt-1">
                          {samplePrompts(group, 7).map((p) => (
                            <button
                              key={p.id}
                              onClick={() =>
                                send(p.prompt, {
                                  kind: 'prompt',
                                  title: p.label,
                                  group: group.title,
                                  icon: 'prompt',
                                })
                              }
                              className="px-3 py-2 rounded-lg text-sm text-[var(--text-secondary)] border border-[var(--border-default)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)] transition-colors truncate text-left"
                              title={p.prompt}
                            >
                              {p.label}
                            </button>
                          ))}
                        </div>
                      </motion.div>
                    ))}
                  </div>
                  <button
                    onClick={() => setLibraryOpen(true)}
                    className="px-4 py-2 text-sm rounded-lg border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] transition-colors"
                  >
                    Browse all {PROMPT_GROUPS.reduce((n, g) => n + g.sections.reduce((m, s) => m + s.prompts.length, 0), 0)}{' '}
                    prompts…
                  </button>
                </div>
              )}
              <AnimatePresence initial={false}>
                {messages.map((message, i) => (
                  <motion.div
                    key={message.id ?? i}
                    initial={
                      message.role === 'user'
                        ? { opacity: 0, scale: 0.94, y: 8 }
                        : { opacity: 0, y: 10, filter: 'blur(6px)' }
                    }
                    animate={
                      message.role === 'user'
                        ? { opacity: 1, scale: 1, y: 0 }
                        : { opacity: 1, y: 0, filter: 'blur(0px)' }
                    }
                    transition={
                      message.role === 'user'
                        ? { type: 'spring', stiffness: 480, damping: 32 }
                        : { duration: 0.4, ease: 'easeOut' }
                    }
                  >
                    <MessageView
                      message={message}
                      conversationId={conversation?.id}
                      traceExplorer={traceExplorer}
                      now={now}
                      streaming={streaming}
                      live={streaming && i === messages.length - 1}
                      actuatorAvailable={Boolean(agent) && !conversationOrphaned}
                      agentConfigUrl={agentConfigUrl}
                      onPlanDecision={onPlanDecision}
                      onShowAudit={setFocusAuditId}
                      onSubmitActionItems={onSubmitActionItems}
                    />
                  </motion.div>
                ))}
              </AnimatePresence>
                {streaming && (
                  <div className="flex items-center gap-2.5 text-xs text-[var(--text-tertiary)] pb-2">
                    <AgentOrb size={18} state={runningTool ? 'tool' : 'thinking'} />
                    <span className="stream-beam" aria-hidden="true" />
                    <span className="working-shimmer">
                      {runningTool ? (
                        <>
                          running{' '}
                          <span className="font-mono text-[var(--text-secondary)]">{runningTool}</span>
                        </>
                      ) : (
                        'agent working'
                      )}
                      <span className="loading-ellipsis" />
                    </span>
                    {elapsedSec !== null && elapsedSec >= 3 && (
                      <span className="tabular-nums text-[var(--text-muted)]">
                        {formatElapsed(elapsedSec)}
                      </span>
                    )}
                  </div>
                )}
              </div>
            </div>
            <AnimatePresence>
              {!atBottom && messages.length > 0 && (
                <motion.button
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: 8 }}
                  transition={{ duration: 0.15 }}
                  style={{ x: '-50%' }}
                  onClick={jumpToLatest}
                  className="absolute bottom-3 left-1/2 z-20 flex items-center gap-1.5 rounded-full border border-[var(--border-default)] bg-[var(--bg-elevated)] px-3 py-1.5 text-xs text-[var(--text-secondary)] shadow-lg transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
                >
                  {streaming && (
                    <span className="h-1.5 w-1.5 rounded-full bg-[var(--neon-yellow)] animate-pulse" />
                  )}
                  ↓ Latest
                </motion.button>
              )}
            </AnimatePresence>
          </div>

          <div className={`${COLUMN} space-y-2`}>
            <PendingApprovalsBar
              plans={pendingPlans}
              disabled={streaming}
              onApproveAll={(plans) => selectedId && approvePlans(selectedId, plans)}
              onRejectAll={(plans) => selectedId && rejectPlans(selectedId, plans)}
            />

            {conversationOrphaned && (
              <div className="glass-card p-3 text-xs text-[var(--text-secondary)] border-l-2 border-l-[var(--neon-amber)]">
                This conversation belongs to a retired specialist agent from before the
                single-agent migration. It stays readable, but new messages need a new
                conversation with the Admin Agent.
              </div>
            )}

            {conversation?.error && (
              <div className="card-alert-critical p-3 text-sm text-[var(--danger)] flex items-start gap-1.5">
                <span className="flex-1">{conversation.error}</span>
                {errorIsGate && <InfoDot eduId="concept.kill-switch" className="mt-0.5" />}
                {!conversationOrphaned && !streaming && (
                  <button
                    onClick={() => selectedId && retryLastTurn(selectedId)}
                    className="shrink-0 rounded-md border border-[var(--danger)]/40 px-2.5 py-1 text-xs font-medium text-[var(--danger)] transition-colors hover:bg-[var(--danger)]/10"
                    title="Re-run the last message (replaces the failed reply)"
                  >
                    ↻ Retry
                  </button>
                )}
              </div>
            )}

            {/* Composer */}
            <div className="relative flex items-end gap-2">
              {paletteOpen && (
                <ComposerPalette
                  matches={paletteMatches}
                  selectedIndex={Math.min(paletteIndex, paletteMatches.length - 1)}
                  onPick={pickPrompt}
                  onHoverIndex={setPaletteIndex}
                />
              )}
              <button
                onClick={() => setLibraryOpen(true)}
                className="shrink-0 flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg border border-[var(--accent)]/40 bg-[var(--accent-muted)] text-[var(--accent)] hover:brightness-110 transition-[filter]"
                title="Open the prompt library"
              >
                <BookIcon />
                Prompts
              </button>
              <textarea
                ref={composerRef}
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value);
                  setPaletteDismissed(false);
                  setPaletteIndex(0);
                }}
                onKeyDown={(e) => {
                  if (paletteOpen) {
                    const count = paletteMatches.length;
                    if (e.key === 'ArrowDown') {
                      e.preventDefault();
                      setPaletteIndex((i) => (i + 1) % count);
                      return;
                    }
                    if (e.key === 'ArrowUp') {
                      e.preventDefault();
                      setPaletteIndex((i) => (i - 1 + count) % count);
                      return;
                    }
                    if (e.key === 'Enter' || e.key === 'Tab') {
                      e.preventDefault();
                      pickPrompt(paletteMatches[Math.min(paletteIndex, count - 1)]);
                      return;
                    }
                    if (e.key === 'Escape') {
                      e.preventDefault();
                      setPaletteDismissed(true);
                      return;
                    }
                  }
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    sendDraft(draft);
                  }
                }}
                rows={1}
                placeholder={
                  conversationOrphaned
                    ? 'Read-only conversation (retired agent) — start a new conversation to continue.'
                    : streaming
                      ? 'Agent is working…'
                      : 'Message the agent — "/" for prompts, Enter to send'
                }
                disabled={streaming || conversationOrphaned}
                className="agent-composer flex-1 px-3 py-2 text-sm rounded-lg bg-[var(--bg-surface)] border border-[var(--border-default)] text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)] resize-none overflow-y-auto disabled:opacity-60"
              />
              {streaming ? (
                <button
                  onClick={() => selectedId && abortAgentTurn(selectedId)}
                  className="px-3.5 py-2 text-sm font-medium rounded-lg bg-[var(--neon-red)] text-white hover:opacity-90 transition-opacity"
                >
                  Stop
                </button>
              ) : (
                <button
                  onClick={() => sendDraft(draft)}
                  disabled={!draft.trim()}
                  className="btn-agent-send px-3.5 py-2 text-sm font-medium rounded-lg text-white disabled:cursor-not-allowed"
                >
                  Send
                </button>
              )}
            </div>
          </div>

          {/* Audit trail + settings history — may stay wider than the chat column */}
          <div className={`${COLUMN} space-y-3`}>
            <AuditTimeline focusAuditId={focusAuditId} />
            <SettingsHistoryCard onRestore={onRestore} />
          </div>

          <PromptLibrary
            open={libraryOpen}
            onClose={() => setLibraryOpen(false)}
            onInsert={insertPrompt}
            onSend={send}
          />

          <ChatHistoryDrawer
            open={historyOpen}
            onClose={() => setHistoryOpen(false)}
            conversations={historyList}
            persistenceEnabled={chatState.persistence.enabled}
            activeConvIds={Object.values(chatState.activeConvIdByAgent)}
          />
        </>
      )}
    </div>
  );
}
