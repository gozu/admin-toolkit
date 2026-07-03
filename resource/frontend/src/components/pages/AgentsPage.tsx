import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import ReactMarkdown from 'react-markdown';
import { fetchJson } from '../../utils/api';
import { DataGrid } from '../common/DataGrid';
import {
  abortAgentTurn,
  agentsChatStore,
  clearConversation,
  decidePlan,
  sendAgentMessage,
  type ActivityItem,
  type AgentInfo,
  type ChatMessage,
  type ExecutionCardData,
  type PlanCardData,
  type Segment,
} from '../../state/agentsChatStore';

interface AgentsListResponse {
  available: boolean;
  agents: AgentInfo[];
  reason?: string;
  projectKey: string;
}

interface AuditRow {
  id: number;
  ts: string;
  agent: string;
  host: string;
  action: string;
  target: unknown;
  status: string;
  result_snippet?: string;
}

const AGENT_HINTS: Record<string, string> = {
  'ATK Health Triage': 'fleet health sweeps & triage reports',
  'ATK Scoping Architect': 'sizing, adoption & scoping analysis',
  'ATK Ops Actuator': 'plans + executes admin actions (with your approval)',
};

const SUGGESTIONS: Record<string, string[]> = {
  'ATK Health Triage': [
    'Run a fleet health sweep and give me the triage report.',
    'What are the top risks on this instance right now?',
  ],
  'ATK Scoping Architect': [
    'How is adoption trending? Who are the top builders?',
    'Which projects dominate storage and compute?',
  ],
  'ATK Ops Actuator': [
    'Find the largest inactive project and plan its cleanup.',
    'Which DB tables need a vacuum? Plan the worst one.',
  ],
};

function formatMs(ms?: number): string {
  if (!ms || ms <= 0) return '';
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function ActivityChips({ items }: { items: ActivityItem[] }) {
  return (
    <div className="flex flex-wrap gap-1.5 my-1.5">
      {items.map((item, i) => (
        <motion.span
          key={`${item.name}-${i}`}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-mono border ${
            item.running
              ? 'border-[var(--neon-yellow)]/40 text-[var(--neon-yellow)] bg-[var(--bg-surface)]'
              : item.ok === false || item.error
                ? 'border-[var(--danger)]/40 text-[var(--danger)] bg-[var(--bg-surface)]'
                : 'border-[var(--border-default)] text-[var(--text-secondary)] bg-[var(--bg-surface)]'
          }`}
          title={item.error || (item.args ? JSON.stringify(item.args) : undefined)}
        >
          {item.running ? (
            <span className="w-2 h-2 rounded-full bg-[var(--neon-yellow)] animate-pulse motion-reduce:animate-none" />
          ) : item.ok === false || item.error ? (
            <span className="w-2 h-2 rounded-full bg-[var(--danger)]" />
          ) : (
            <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          )}
          {item.name}
          {!item.running && item.durationMs !== undefined && (
            <span className="text-[var(--text-muted)]">{formatMs(item.durationMs)}</span>
          )}
        </motion.span>
      ))}
    </div>
  );
}

const PLAN_HIDDEN_KEYS = new Set(['summary', 'warning', 'warnings', 'irreversible', 'backupFolder', 'note']);

function PlanCard({
  plan,
  now,
  disabled,
  onDecide,
}: {
  plan: PlanCardData;
  now: number;
  disabled: boolean;
  onDecide: (decision: 'approved' | 'rejected') => void;
}) {
  const secondsLeft = Math.max(0, Math.floor((plan.expiresAt - now) / 1000));
  const expired = secondsLeft <= 0 && !plan.decision;
  const warnings: string[] = [];
  const rawWarning = plan.plan.warning || plan.plan.warnings;
  if (typeof rawWarning === 'string') warnings.push(rawWarning);
  if (Array.isArray(rawWarning)) warnings.push(...rawWarning.map(String));
  const details = Object.entries(plan.plan).filter(
    ([key, value]) => !PLAN_HIDDEN_KEYS.has(key) && value !== null && value !== undefined
      && (typeof value !== 'object' || Array.isArray(value)),
  );
  const backup = plan.plan.backupFolder as { name?: string } | undefined;

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      className="glass-card my-2 p-3.5 border-l-2 border-l-[var(--neon-amber)] space-y-2.5"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider bg-[var(--status-warning-bg)] border border-[var(--status-warning-border)] text-[var(--neon-amber)]">
            Plan
          </span>
          <span className="text-sm font-mono text-[var(--text-primary)] truncate">{plan.action}</span>
          <span className="text-xs text-[var(--text-tertiary)]">on {plan.host}</span>
        </div>
        {!plan.decision && !expired && (
          <span className={`text-xs tabular-nums ${secondsLeft < 120 ? 'text-[var(--neon-amber)]' : 'text-[var(--text-tertiary)]'}`}>
            {Math.floor(secondsLeft / 60)}:{String(secondsLeft % 60).padStart(2, '0')}
          </span>
        )}
      </div>

      {typeof plan.plan.summary === 'string' && (
        <p className="text-sm text-[var(--text-primary)] leading-snug">{plan.plan.summary}</p>
      )}

      {details.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1">
          {details.slice(0, 9).map(([key, value]) => (
            <div key={key} className="min-w-0">
              <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">{key}</div>
              <div className="text-xs text-[var(--text-secondary)] font-mono truncate" title={String(value)}>
                {Array.isArray(value) ? value.slice(0, 4).join(', ') + (value.length > 4 ? '…' : '') : String(value)}
              </div>
            </div>
          ))}
        </div>
      )}

      {warnings.map((warning, i) => (
        <div key={i} className="text-xs text-[var(--neon-amber)] flex items-start gap-1.5">
          <span className="mt-px">⚠</span>
          <span>{warning}</span>
        </div>
      ))}
      {typeof plan.plan.irreversible === 'string' && (
        <div className="text-xs text-[var(--danger)]">{plan.plan.irreversible}</div>
      )}
      {backup?.name && (
        <div className="text-xs text-[var(--text-tertiary)]">Backup destination: {backup.name}</div>
      )}

      <div className="flex items-center gap-2 pt-1">
        {plan.decision === 'approved' ? (
          <span className="text-xs font-medium text-[var(--accent)]">✓ Approved — executing</span>
        ) : plan.decision === 'rejected' ? (
          <span className="text-xs font-medium text-[var(--text-tertiary)]">✕ Rejected</span>
        ) : expired ? (
          <span className="text-xs text-[var(--text-tertiary)]">
            Confirm window expired — ask the agent to re-plan.
          </span>
        ) : (
          <>
            <button
              onClick={() => onDecide('approved')}
              disabled={disabled}
              className="px-3 py-1 text-xs font-semibold rounded-md bg-[var(--accent)] text-white hover:opacity-90 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Approve &amp; execute
            </button>
            <button
              onClick={() => onDecide('rejected')}
              disabled={disabled}
              className="px-3 py-1 text-xs font-medium rounded-md border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Reject
            </button>
          </>
        )}
      </div>
    </motion.div>
  );
}

function ExecutionCard({ execution }: { execution: ExecutionCardData }) {
  const ok = execution.status === 'ok';
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      className={`glass-card my-2 p-3 border-l-2 space-y-1 ${ok ? 'border-l-[var(--accent)]' : 'border-l-[var(--danger)]'}`}
    >
      <div className="flex items-center gap-2">
        <span
          className={`px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider border ${
            ok
              ? 'bg-[var(--accent-muted)] border-[var(--accent)]/30 text-[var(--accent)]'
              : 'bg-[var(--status-critical-bg)] border-[var(--status-critical-border)] text-[var(--danger)]'
          }`}
        >
          {ok ? 'Executed' : 'Failed'}
        </span>
        <span className="text-sm font-mono text-[var(--text-primary)]">{execution.action}</span>
        <span className="text-xs text-[var(--text-tertiary)]">on {execution.host}</span>
        {execution.auditId != null && (
          <span className="ml-auto text-xs text-[var(--text-muted)] font-mono">audit #{execution.auditId}</span>
        )}
      </div>
      {execution.auditWarning && (
        <div className="text-xs text-[var(--neon-amber)]">{execution.auditWarning}</div>
      )}
    </motion.div>
  );
}

function MessageView({
  message,
  now,
  streaming,
  onPlanDecision,
}: {
  message: ChatMessage;
  now: number;
  streaming: boolean;
  onPlanDecision: (plan: PlanCardData, decision: 'approved' | 'rejected') => void;
}) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-xl rounded-br-sm px-3.5 py-2 bg-[var(--accent-muted)] border border-[var(--accent)]/20 text-sm text-[var(--text-primary)] whitespace-pre-wrap">
          {message.content}
        </div>
      </div>
    );
  }
  return (
    <div className="max-w-[95%] space-y-0.5">
      {message.segments.map((segment: Segment, i) => {
        if (segment.type === 'text') {
          return (
            <div key={i} className="ai-analysis-markdown text-sm text-[var(--text-primary)]">
              <ReactMarkdown>{segment.text}</ReactMarkdown>
            </div>
          );
        }
        if (segment.type === 'activity') {
          return <ActivityChips key={i} items={segment.items} />;
        }
        if (segment.type === 'plan') {
          return (
            <PlanCard
              key={segment.plan.confirmToken || i}
              plan={segment.plan}
              now={now}
              disabled={streaming}
              onDecide={(decision) => onPlanDecision(segment.plan, decision)}
            />
          );
        }
        return <ExecutionCard key={i} execution={segment.execution} />;
      })}
    </div>
  );
}

function AuditTimeline() {
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  const [reason, setReason] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    fetchJson<{ available: boolean; actions: AuditRow[]; reason?: string }>('/api/agents/actions?limit=50')
      .then((data) => {
        setRows(data.actions || []);
        if (!data.available) setReason(data.reason || null);
      })
      .catch((err) => setReason(String(err)));
  }, []);

  if (reason || !rows || rows.length === 0) return null;

  return (
    <div className="glass-card p-3">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between text-left"
      >
        <span className="text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
          Action audit trail
        </span>
        <span className="text-xs text-[var(--text-tertiary)]">
          {rows.length} action{rows.length === 1 ? '' : 's'} {open ? '▾' : '▸'}
        </span>
      </button>
      {open && (
        <div className="mt-2">
          <DataGrid<AuditRow>
            rows={rows}
            rowKey={(row) => String(row.id)}
            scroll={{ maxH: 'max-h-64' }}
            columns={[
              { id: 'id', label: '#', mono: true, render: (row) => `#${row.id}`,
                sortValue: (row) => row.id, defaultSortDir: 'desc' },
              { id: 'ts', label: 'When', render: (row) => row.ts.slice(0, 16).replace('T', ' '),
                sortValue: (row) => row.ts },
              { id: 'action', label: 'Action', mono: true, render: (row) => row.action,
                sortValue: (row) => row.action },
              { id: 'target', label: 'Target',
                render: (row) => (
                  <span className="block truncate max-w-[16rem]" title={JSON.stringify(row.target)}>
                    {typeof row.target === 'object' ? JSON.stringify(row.target) : String(row.target ?? '')}
                  </span>
                ) },
              { id: 'host', label: 'Host', render: (row) => row.host, sortValue: (row) => row.host },
              { id: 'status', label: 'Status', render: (row) => row.status,
                sortValue: (row) => row.status,
                cellClassName: (row) =>
                  row.status === 'ok' ? 'text-[var(--accent)]' : 'text-[var(--danger)]' },
            ]}
            defaultSortColumnId="id"
            defaultSortDir="desc"
          />
        </div>
      )}
    </div>
  );
}

export function AgentsPage() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [unavailableReason, setUnavailableReason] = useState<string | null>(null);
  const [loadingAgents, setLoadingAgents] = useState(true);
  const [selectedId, setSelectedId] = useState<string>('');
  const [draft, setDraft] = useState('');
  const [now, setNow] = useState(() => Date.now());
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);

  const chatState = agentsChatStore.use();
  const conversation = selectedId ? chatState.conversations[selectedId] : undefined;
  const messages = useMemo(() => conversation?.messages ?? [], [conversation]);
  const streaming = conversation?.streaming ?? false;

  useEffect(() => {
    fetchJson<AgentsListResponse>('/api/agents')
      .then((data) => {
        setAgents(data.agents);
        if (!data.available) setUnavailableReason(data.reason || 'Agents plugin not provisioned');
        if (data.agents.length > 0) {
          const actuator = data.agents.find((a) => a.name.includes('Actuator'));
          setSelectedId((prev) => prev || (actuator || data.agents[0]).id);
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

  const onPlanDecision = useCallback(
    (plan: PlanCardData, decision: 'approved' | 'rejected') => {
      if (!selectedId) return;
      decidePlan(selectedId, plan.confirmToken, decision);
      const text =
        decision === 'approved'
          ? `Approved — I confirm. Execute the planned ${plan.action} on host ${plan.host} with the exact planned target, confirm=true and confirm_token ${plan.confirmToken}. Report the outcome and the auditId.`
          : `Rejected — do NOT execute the planned ${plan.action}. Stand down and await further instructions.`;
      stickToBottomRef.current = true;
      void sendAgentMessage(selectedId, text);
    },
    [selectedId],
  );

  const selectedAgent = agents.find((a) => a.id === selectedId);
  const suggestions = selectedAgent ? SUGGESTIONS[selectedAgent.name] || [] : [];

  return (
    <div className="w-full flex-1 min-h-0 flex flex-col gap-3 py-4">
      {/* Header: agent picker */}
      <div className="flex items-center gap-2 flex-wrap">
        {agents.map((agent) => (
          <button
            key={agent.id}
            onClick={() => setSelectedId(agent.id)}
            className={`px-3 py-1.5 rounded-lg text-sm border transition-colors ${
              agent.id === selectedId
                ? 'bg-[var(--accent-muted)] border-[var(--accent)]/40 text-[var(--accent)]'
                : 'bg-[var(--bg-surface)] border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]'
            }`}
            title={AGENT_HINTS[agent.name]}
          >
            {agent.name.replace(/^ATK /, '')}
          </button>
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
        <div className="glass-card p-6 max-w-lg space-y-2">
          <h3 className="text-sm font-semibold text-[var(--text-primary)]">No agents on this host</h3>
          <p className="text-sm text-[var(--text-secondary)] leading-relaxed">
            The Admin Toolkit agents plugin is not provisioned here (no AGENTOPS project with agent
            instances was found).
            {unavailableReason ? ` — ${unavailableReason}` : ''}
          </p>
        </div>
      ) : (
        <>
          {/* Transcript */}
          <div
            ref={scrollRef}
            onScroll={handleScroll}
            className="flex-1 min-h-0 overflow-y-auto space-y-4 pr-1"
          >
            {messages.length === 0 && (
              <div className="h-full flex flex-col items-center justify-center gap-3 text-center">
                <p className="text-sm text-[var(--text-secondary)]">
                  {selectedAgent ? AGENT_HINTS[selectedAgent.name] || 'Ask the agent anything.' : ''}
                </p>
                <div className="flex flex-col gap-2">
                  {suggestions.map((suggestion) => (
                    <button
                      key={suggestion}
                      onClick={() => send(suggestion)}
                      className="px-3 py-1.5 rounded-lg text-xs text-[var(--text-secondary)] border border-[var(--border-default)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)] transition-colors"
                    >
                      {suggestion}
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
                    onPlanDecision={onPlanDecision}
                  />
                </motion.div>
              ))}
            </AnimatePresence>
            {streaming && (
              <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)]">
                <span className="w-2 h-2 rounded-full bg-[var(--neon-yellow)] animate-pulse motion-reduce:animate-none" />
                agent working<span className="loading-ellipsis" />
              </div>
            )}
          </div>

          {conversation?.error && (
            <div className="card-alert-critical p-3 text-sm text-[var(--danger)]">
              {conversation.error}
            </div>
          )}

          {/* Composer */}
          <div className="flex items-end gap-2">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  send(draft);
                }
              }}
              rows={Math.min(4, Math.max(1, draft.split('\n').length))}
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

          <AuditTimeline />
        </>
      )}
    </div>
  );
}
