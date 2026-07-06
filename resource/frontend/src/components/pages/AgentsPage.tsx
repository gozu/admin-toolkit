import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { fetchJson } from '../../utils/api';
import { InfoDot } from '../common/InfoDot';
import { MessageView } from '../agents/MessageView';
import { PromptLibrary } from '../agents/PromptLibrary';
import { PendingApprovalsBar } from '../agents/PendingApprovalsBar';
import { AuditTimeline } from '../agents/AuditTimeline';
import { SettingsHistoryCard } from '../agents/SettingsHistoryCard';
import { catalogForAgent } from '../../utils/agentPromptCatalog';
import {
  abortAgentTurn,
  agentsChatStore,
  approvePlans,
  clearConversation,
  rejectPlans,
  selectAgent,
  sendAgentMessage,
  submitActionItemsToActuator,
  type ActionItemData,
  type AgentInfo,
  type PlanCardData,
} from '../../state/agentsChatStore';

interface AgentsListResponse {
  available: boolean;
  agents: AgentInfo[];
  reason?: string;
  projectKey: string;
}

const AGENT_HINTS: Record<string, string> = {
  'ATK Health Triage': 'fleet health sweeps & triage reports',
  'ATK Scoping Architect': 'sizing, adoption & scoping analysis',
  'ATK Ops Actuator': 'plans + executes admin actions (with your approval)',
};

const AGENT_EDU: Record<string, string> = {
  'ATK Health Triage': 'agent.health-triage',
  'ATK Scoping Architect': 'agent.scoping-architect',
  'ATK Ops Actuator': 'agent.ops-actuator',
};

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
  const [focusAuditId, setFocusAuditId] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const stickToBottomRef = useRef(true);

  const chatState = agentsChatStore.use();
  const selectedId = chatState.selectedAgentId;
  const conversation = selectedId ? chatState.conversations[selectedId] : undefined;
  const messages = useMemo(() => conversation?.messages ?? [], [conversation]);
  const streaming = conversation?.streaming ?? false;

  const actuator = useMemo(() => agents.find((a) => a.name.includes('Actuator')), [agents]);

  useEffect(() => {
    fetchJson<AgentsListResponse>('/api/agents')
      .then((data) => {
        setAgents(data.agents);
        if (!data.available) setUnavailableReason(data.reason || 'Agents plugin not provisioned');
        if (data.agents.length > 0) {
          const current = agentsChatStore.get().selectedAgentId;
          if (!current || !data.agents.some((a) => a.id === current)) {
            const preferred = data.agents.find((a) => a.name.includes('Actuator')) || data.agents[0];
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

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || !selectedId || streaming) return;
      setDraft('');
      stickToBottomRef.current = true;
      void sendAgentMessage(selectedId, trimmed);
    },
    [selectedId, streaming],
  );

  const insertPrompt = useCallback((prompt: string) => {
    setDraft(prompt);
    composerRef.current?.focus();
  }, []);

  // Settings-history "Restore…": hand the restore plan to the actuator —
  // switch to it (restores are its job) and prefill the composer for review.
  const onRestore = useCallback(
    (prompt: string) => {
      if (actuator && actuator.id !== selectedId) selectAgent(actuator.id);
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

  const selectedAgent = agents.find((a) => a.id === selectedId);
  const catalog = catalogForAgent(selectedAgent?.name);
  const errorIsGate = /red|kill|locked|disabled/i.test(conversation?.error || '');

  return (
    <div className="w-full flex-1 min-h-0 flex flex-col gap-3 py-4">
      {/* Header: agent picker */}
      <div className={`${COLUMN} flex items-center gap-2 flex-wrap`}>
        {agents.map((agent) => (
          <span key={agent.id} className="inline-flex items-center gap-1">
            <button
              onClick={() => selectAgent(agent.id)}
              className={`px-3 py-1.5 rounded-lg text-sm border transition-colors ${
                agent.id === selectedId
                  ? 'bg-[var(--accent-muted)] border-[var(--accent)]/40 text-[var(--accent)]'
                  : 'bg-[var(--bg-surface)] border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]'
              }`}
              title={AGENT_HINTS[agent.name]}
            >
              {agent.name.replace(/^ATK /, '')}
            </button>
            {AGENT_EDU[agent.name] && <InfoDot eduId={AGENT_EDU[agent.name]} />}
          </span>
        ))}
        {messages.length > 0 && (
          <button
            onClick={() => selectedId && clearConversation(selectedId)}
            className="ml-auto px-2.5 py-1.5 rounded-lg text-xs text-[var(--text-tertiary)] border border-[var(--border-default)] hover:bg-[var(--bg-hover)] transition-colors"
          >
            New conversation
          </button>
        )}
      </div>

      {loadingAgents ? (
        <div className="flex-1 flex items-center justify-center">
          <div className="w-6 h-6 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
        </div>
      ) : agents.length === 0 ? (
        <div className={COLUMN}>
          <div className="glass-card p-6 max-w-lg space-y-2">
            <h3 className="text-sm font-semibold text-[var(--text-primary)]">No agents on this host</h3>
            <p className="text-sm text-[var(--text-secondary)] leading-relaxed">
              The Admin Toolkit agents plugin is not provisioned here (no AGENTOPS project with agent
              instances was found).
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
                <div className="pt-16 flex flex-col items-center gap-4 text-center">
                  <p className="text-sm text-[var(--text-secondary)]">
                    {selectedAgent ? AGENT_HINTS[selectedAgent.name] || 'Ask the agent anything.' : ''}
                  </p>
                  {catalog && (
                    <div className="glass-card w-full max-w-md p-4 space-y-2 text-left border-l-2 border-l-[var(--accent)]">
                      <div className="text-xs font-semibold text-[var(--accent)]">
                        ★ {catalog.megapromptTitle}
                      </div>
                      <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
                        {catalog.megapromptBlurb}
                      </p>
                      <div className="flex items-center gap-2 pt-1">
                        <button
                          onClick={() => send(catalog.megaprompt)}
                          className="px-3 py-1 text-xs font-semibold rounded-md bg-[var(--accent)] text-white hover:opacity-90 transition-opacity"
                        >
                          Run it
                        </button>
                        <button
                          onClick={() => setLibraryOpen(true)}
                          className="px-3 py-1 text-xs rounded-md border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] transition-colors"
                        >
                          Browse all prompts…
                        </button>
                      </div>
                    </div>
                  )}
                  <div className="flex flex-col gap-2 w-full max-w-md">
                    {(catalog?.sections || []).slice(0, 3).map((section) => (
                      <button
                        key={section.id}
                        onClick={() => send(section.prompts[0].prompt)}
                        className="px-3 py-1.5 rounded-lg text-xs text-[var(--text-secondary)] border border-[var(--border-default)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)] transition-colors truncate"
                        title={section.prompts[0].prompt}
                      >
                        {section.prompts[0].label}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <AnimatePresence initial={false}>
                {messages.map((message, i) => (
                  <motion.div key={i} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}>
                    <MessageView
                      message={message}
                      now={now}
                      streaming={streaming}
                      actuatorAvailable={Boolean(actuator)}
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
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    send(draft);
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
                  onClick={() => send(draft)}
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
            agentName={selectedAgent?.name}
            onClose={() => setLibraryOpen(false)}
            onInsert={insertPrompt}
            onSend={send}
          />
        </>
      )}
    </div>
  );
}
