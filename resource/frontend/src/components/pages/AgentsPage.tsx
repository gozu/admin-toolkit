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
  groupForRole,
  type AgentRole,
  type CatalogGroup,
} from '../../utils/agentPromptCatalog';
import { hostBaseUrl } from '../../utils/agentLinks';
import { dssUrls } from '../../utils/codeEnvUsageLinks';
import { getActiveHostId } from '../../state/hostStore';
import { ChatHistoryDrawer } from '../agents/ChatHistoryDrawer';
import {
  abortAgentTurn,
  agentsChatStore,
  approvePlans,
  clearAllConversations,
  deriveTitle,
  ensureChatBootstrapped,
  provisionTraceExplorer,
  rejectPlans,
  selectAgent,
  sendAgentMessage,
  submitActionItemsToActuator,
  type ActionItemData,
  type AgentInfo,
  type ConversationMeta,
  type PlanCardData,
  type ProvisionResult,
} from '../../state/agentsChatStore';

interface AgentsListResponse {
  available: boolean;
  agents: AgentInfo[];
  reason?: string;
  projectKey: string;
}

/** The UI presents ONE agent; the three provisioned specialists stay behind
 * the curtain and are picked per message (name substring = stable identity). */
function findByRole(agents: AgentInfo[], role: AgentRole): AgentInfo | undefined {
  const pattern = { triage: /triage/i, scoping: /scoping/i, actuator: /actuator/i }[role];
  return agents.find((a) => pattern.test(a.name));
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
  const [draft, setDraft] = useState('');
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [provisioning, setProvisioning] = useState(false);
  const [provisionResult, setProvisionResult] = useState<ProvisionResult | null>(null);
  const [provisionError, setProvisionError] = useState<string | null>(null);
  const [focusAuditId, setFocusAuditId] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const stickToBottomRef = useRef(true);
  // A prompt inserted from the library keeps its group's routing until it is
  // sent or the composer is cleared — editing the text keeps the intent.
  const pendingRoleRef = useRef<AgentRole | null>(null);

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

  const actuator = useMemo(() => findByRole(agents, 'actuator'), [agents]);
  const triage = useMemo(() => findByRole(agents, 'triage'), [agents]);

  // Deep link to the conversation agent's DSS config screen — powers the
  // gate-hint callout when an execute is refused for allow_red_actions=false.
  const agentConfigUrl = useMemo(() => {
    const gateAgent = agents.find((a) => a.id === conversation?.agentId) || actuator;
    if (!gateAgent) return undefined;
    return dssUrls.agentConfig(
      gateAgent.projectKey ?? 'ADMINTOOLKIT',
      gateAgent.id,
      gateAgent.activeVersion ?? 'v1',
    );
  }, [agents, conversation, actuator]);

  // Chat persistence config + trace-explorer status, once per host — the
  // session epoch (host switch / in-app Refresh) resets `persistence.loaded`,
  // which re-triggers this effect against the new host.
  useEffect(() => {
    if (!chatState.persistence.loaded) void ensureChatBootstrapped();
  }, [chatState.persistence.loaded]);

  useEffect(() => {
    fetchJson<AgentsListResponse>('/api/agents')
      .then((data) => {
        setAgents(data.agents);
        if (!data.available) setUnavailableReason(data.reason || 'Agents plugin not provisioned');
        if (data.agents.length > 0) {
          const current = agentsChatStore.get().selectedAgentId;
          if (!current || !data.agents.some((a) => a.id === current)) {
            // Fresh sessions start on the triage generalist (it has every
            // sensor tool); free-form messages continue the visible thread.
            const preferred = findByRole(data.agents, 'triage') || data.agents[0];
            selectAgent(preferred.id);
          }
        }
      })
      .catch((err) => setUnavailableReason(String(err)))
      .finally(() => setLoadingAgents(false));
  }, []);

  // Tick for plan-expiry countdowns — only while an undecided plan is visible.
  const hasPendingPlan = useMemo(
    () =>
      messages.some((msg) =>
        msg.segments.some((seg) => seg.type === 'plan' && !seg.plan.decision && seg.plan.expiresAt > now),
      ),
    [messages, now],
  );
  useEffect(() => {
    if (!hasPendingPlan) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [hasPendingPlan]);

  // Undecided, unexpired plans across the conversation → batch approvals bar.
  const pendingPlans = useMemo(() => {
    const out: PlanCardData[] = [];
    for (const msg of messages) {
      for (const seg of msg.segments) {
        if (seg.type === 'plan' && !seg.plan.decision && seg.plan.expiresAt > now) out.push(seg.plan);
      }
    }
    return out;
  }, [messages, now]);

  // Autoscroll while streaming, unless the user scrolled up.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [messages, streaming]);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }, []);

  /** Route one message: an explicit role (library prompt) picks its
   * specialist; free-form text continues the visible thread, or starts a
   * fresh one on the triage generalist. */
  const send = useCallback(
    (text: string, role?: AgentRole) => {
      const trimmed = text.trim();
      if (!trimmed || streaming || agents.length === 0) return;
      const routed = role ? findByRole(agents, role) : undefined;
      const fallback = agents.find((a) => a.id === selectedId) || triage || agents[0];
      const target = routed || fallback;
      setDraft('');
      pendingRoleRef.current = null;
      stickToBottomRef.current = true;
      if (target.id !== selectedId) selectAgent(target.id);
      void sendAgentMessage(target.id, trimmed);
    },
    [agents, selectedId, streaming, triage],
  );

  const sendDraft = useCallback(
    (text: string) => send(text, pendingRoleRef.current ?? undefined),
    [send],
  );

  const insertPrompt = useCallback((prompt: string, role?: AgentRole) => {
    pendingRoleRef.current = role ?? null;
    setDraft(prompt);
    composerRef.current?.focus();
  }, []);

  // Settings-history "Restore…": restores are the actuator specialist's job —
  // prefill the composer for review and route the send to it.
  const onRestore = useCallback(
    (prompt: string) => {
      if (actuator && actuator.id !== selectedId) selectAgent(actuator.id);
      pendingRoleRef.current = 'actuator';
      setDraft(prompt);
      composerRef.current?.focus();
    },
    [actuator, selectedId],
  );

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
      if (!selectedId || !actuator) return;
      stickToBottomRef.current = true;
      submitActionItemsToActuator(selectedId, actuator.id, batchId, items);
    },
    [selectedId, actuator],
  );

  const onProvisionTraceExplorer = useCallback(() => {
    setProvisioning(true);
    setProvisionError(null);
    setProvisionResult(null);
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

  const errorIsGate = /red|kill|locked|disabled/i.test(conversation?.error || '');
  const traceExplorer = chatState.traceExplorer;
  const explorerViewPath = traceExplorer?.viewPath || conversation?.traceExplorerPath;
  const heroGroups = useMemo(
    () => [groupForRole('triage'), groupForRole('scoping')],
    [],
  );

  return (
    <div className="w-full flex-1 min-h-0 flex flex-col gap-3 py-4">
      {/* Header: one agent, one identity */}
      <div className={`${COLUMN} flex items-center gap-2 flex-wrap`}>
        <span className="inline-flex items-center gap-1.5">
          <span className="text-sm font-semibold text-[var(--text-primary)]">
            Admin Toolkit Agent
          </span>
          <InfoDot eduId="agent.unified" />
        </span>
        <span className="text-xs text-[var(--text-tertiary)]">
          health · triage · scoping · admin actions
        </span>
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
                {provisionError
                  ? 'Trace Explorer setup failed'
                  : provisionResult?.ok
                    ? 'Trace Explorer is ready'
                    : 'Trace Explorer setup incomplete'}
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
          <div className="w-6 h-6 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
        </div>
      ) : agents.length === 0 ? (
        <div className={COLUMN}>
          <div className="glass-card p-6 max-w-lg space-y-2">
            <h3 className="text-sm font-semibold text-[var(--text-primary)]">No agents on this host</h3>
            <p className="text-sm text-[var(--text-secondary)] leading-relaxed">
              The Admin Toolkit agents are not provisioned here (no agent instances found in the
              ADMINTOOLKIT project — run scripts/agents/provision_prod.py against this host).
              {unavailableReason ? ` — ${unavailableReason}` : ''}
            </p>
          </div>
        </div>
      ) : (
        <>
          {/* Transcript — centered column, bubbles never touch the edges */}
          <div ref={scrollRef} onScroll={handleScroll} className="flex-1 min-h-0 overflow-y-auto">
            <div className={`${COLUMN} space-y-4`}>
              {messages.length === 0 && (
                <div className="pt-10 flex flex-col items-center gap-4 text-center">
                  <p className="text-sm text-[var(--text-secondary)]">
                    Ask about fleet health, sizing and scoping, or admin maintenance — or start
                    from a sample below.
                  </p>
                  <div className="grid gap-3 w-full max-w-3xl sm:grid-cols-2 text-left">
                    {heroGroups.map((group) => (
                      <div
                        key={group.role}
                        className="glass-card p-4 space-y-2 border-l-2 border-l-[var(--accent)]"
                      >
                        <div className="text-xs font-bold uppercase tracking-widest text-[var(--accent)]">
                          {group.title}
                        </div>
                        <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
                          {group.blurb}
                        </p>
                        <button
                          onClick={() => send(group.megaprompt, group.role)}
                          className="px-3 py-1 text-xs font-semibold rounded-md bg-[var(--accent)] text-white hover:opacity-90 transition-opacity"
                        >
                          ★ {group.megapromptTitle}
                        </button>
                        <div className="flex flex-col gap-1.5 pt-1">
                          {samplePrompts(group, 7).map((p) => (
                            <button
                              key={p.id}
                              onClick={() => send(p.prompt, group.role)}
                              className="px-2.5 py-1.5 rounded-lg text-xs text-[var(--text-secondary)] border border-[var(--border-default)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)] transition-colors truncate text-left"
                              title={p.prompt}
                            >
                              {p.label}
                            </button>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                  <button
                    onClick={() => setLibraryOpen(true)}
                    className="px-3 py-1.5 text-xs rounded-md border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] transition-colors"
                  >
                    Browse all {PROMPT_GROUPS.reduce((n, g) => n + g.sections.reduce((m, s) => m + s.prompts.length, 0), 0)}{' '}
                    prompts…
                  </button>
                </div>
              )}
              <AnimatePresence initial={false}>
                {messages.map((message, i) => (
                  <motion.div key={i} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}>
                    <MessageView
                      message={message}
                      conversationId={conversation?.id}
                      traceExplorer={traceExplorer}
                      now={now}
                      streaming={streaming}
                      actuatorAvailable={Boolean(actuator)}
                      agentConfigUrl={agentConfigUrl}
                      onPlanDecision={onPlanDecision}
                      onShowAudit={setFocusAuditId}
                      onSubmitActionItems={onSubmitActionItems}
                    />
                  </motion.div>
                ))}
              </AnimatePresence>
              {streaming && (
                <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)] pb-2">
                  <span className="w-2 h-2 rounded-full bg-[var(--neon-yellow)] animate-pulse motion-reduce:animate-none" />
                  agent working<span className="loading-ellipsis" />
                </div>
              )}
            </div>
          </div>

          <div className={`${COLUMN} space-y-2`}>
            <PendingApprovalsBar
              plans={pendingPlans}
              disabled={streaming}
              onApproveAll={(plans) => selectedId && approvePlans(selectedId, plans)}
              onRejectAll={(plans) => selectedId && rejectPlans(selectedId, plans)}
            />

            {conversation?.error && (
              <div className="card-alert-critical p-3 text-sm text-[var(--danger)] flex items-start gap-1.5">
                <span className="flex-1">{conversation.error}</span>
                {errorIsGate && <InfoDot eduId="concept.kill-switch" className="mt-0.5" />}
              </div>
            )}

            {/* Composer */}
            <div className="flex items-end gap-2">
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
                  if (!e.target.value) pendingRoleRef.current = null;
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    sendDraft(draft);
                  }
                }}
                rows={Math.min(8, Math.max(1, draft.split('\n').length))}
                placeholder={streaming ? 'Agent is working…' : 'Message the agent… (Enter to send)'}
                disabled={streaming}
                className="flex-1 px-3 py-2 text-sm rounded-lg bg-[var(--bg-surface)] border border-[var(--border-default)] text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)] resize-none disabled:opacity-60"
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
                  className="px-3.5 py-2 text-sm font-medium rounded-lg bg-[var(--accent)] text-white hover:opacity-90 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed"
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
