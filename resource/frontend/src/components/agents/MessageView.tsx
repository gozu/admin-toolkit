import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { fetchJson } from '../../utils/api';
import { EXPLORE_TRACE_STORAGE_KEY, traceExplorerHandoffUrl } from '../../utils/agentLinks';
import { getActiveHostId } from '../../state/hostStore';
import { ActivityChips } from './ActivityChips';
import { PlanCard } from './PlanCard';
import { ExecutionCard } from './ExecutionCard';
import { ActionItemsCard } from './ActionItemsCard';
import { useDiag } from '../../context/DiagContext';
import type { PageId } from '../../types';
import type {
  ActionItemData,
  ChatMessage,
  GateLink,
  PlanCardData,
  Segment,
  TraceExplorerStatus,
} from '../../state/agentsChatStore';

/** The turn's dku-trace JSON: the backend's in-memory ring first (last few
 * turns), then the persisted per-message copy when chat persistence is on. */
async function fetchTraceJson(
  traceId: string,
  conversationId: string | undefined,
  messageId: string,
): Promise<unknown | null> {
  try {
    const data = await fetchJson<{ available: boolean; trace?: unknown }>(
      `/api/agents/last-trace?id=${encodeURIComponent(traceId)}`,
    );
    if (data.available && data.trace) return data.trace;
  } catch {
    // ring rotated — try the durable copy
  }
  if (conversationId) {
    try {
      const data = await fetchJson<{ available?: boolean; trace?: unknown }>(
        `/api/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/trace`,
      );
      if (data.available && data.trace) return data.trace;
    } catch {
      // not persisted either
    }
  }
  return null;
}

/** One-click trace: on the local hub with a provisioned Trace Explorer, hand
 * the trace over via the explorer's native localStorage flow
 * (?readTraceFromLS=true) and open it in a new tab. Anywhere else (remote
 * hosts: localStorage is per-origin, no handoff possible) fall back to the
 * copy-trace chip for Trace Explorer's "Paste a new trace". */
function TraceAction({
  traceId,
  conversationId,
  messageId,
  traceExplorer,
}: {
  traceId: string;
  conversationId: string | undefined;
  messageId: string;
  traceExplorer: TraceExplorerStatus | null;
}) {
  const [state, setState] = useState<'idle' | 'busy' | 'copied' | 'expired'>('idle');
  const canHandoff = Boolean(traceExplorer?.sameOrigin && traceExplorer.webAppId);

  const flash = (next: 'copied' | 'expired') => {
    setState(next);
    setTimeout(() => setState('idle'), 2500);
  };

  const openInExplorer = async () => {
    setState('busy');
    const trace = await fetchTraceJson(traceId, conversationId, messageId);
    if (!trace || !traceExplorer?.webAppId) {
      flash('expired');
      return;
    }
    try {
      globalThis.localStorage?.setItem(EXPLORE_TRACE_STORAGE_KEY, JSON.stringify(trace));
    } catch {
      flash('expired');
      return;
    }
    window.open(
      traceExplorerHandoffUrl(getActiveHostId(), traceExplorer.projectKey, traceExplorer.webAppId),
      '_blank',
      'noopener',
    );
    setState('idle');
  };

  const copy = async () => {
    const trace = await fetchTraceJson(traceId, conversationId, messageId);
    if (!trace) {
      flash('expired');
      return;
    }
    try {
      await navigator.clipboard.writeText(JSON.stringify(trace, null, 2));
      flash('copied');
    } catch {
      flash('expired');
    }
  };

  if (state === 'copied') {
    return <span className="text-[11px] text-[var(--text-muted)]">trace copied ✓</span>;
  }
  if (state === 'expired') {
    return <span className="text-[11px] text-[var(--text-muted)]">trace expired</span>;
  }
  if (canHandoff) {
    return (
      <button
        onClick={() => void openInExplorer()}
        disabled={state === 'busy'}
        className="text-[11px] text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors disabled:opacity-60"
        title="Open this turn's trace in the native DSS Trace Explorer"
      >
        {state === 'busy' ? 'opening trace…' : 'open trace ↗'}
      </button>
    );
  }
  return (
    <button
      onClick={() => void copy()}
      className="text-[11px] text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors"
      title="Copy this turn's trace JSON — paste it into Trace Explorer for instant inspection"
    >
      copy trace
    </button>
  );
}

/** Inline callout for a safety-gate refusal an admin can clear in DSS. Today
 *  the only case is `agent-execution-disabled` (per-agent `allow_red_actions`
 *  is off), with a deep link straight to the agent's config screen. */
const GATE_TITLES: Record<string, string> = {
  'action-disabled': 'Action disabled in Agent Permissions',
  'red-actions-disabled': 'Master kill-switch is off',
  'red-locked': 'Agentic actions are locked',
};

function GateHint({
  code,
  message,
  link,
  agentConfigUrl,
}: {
  code: string;
  message?: string;
  link?: GateLink;
  agentConfigUrl?: string;
}) {
  const { setActivePage } = useDiag();
  if (code === 'agent-execution-disabled') {
    return (
      <div className="my-1.5 rounded-lg border border-[var(--neon-yellow)]/40 bg-[var(--neon-yellow)]/5 px-3 py-2 text-xs text-[var(--text-secondary)]">
        <div className="font-semibold text-[var(--text-primary)]">Agentic actions are disabled for this agent</div>
        <p className="mt-0.5 leading-relaxed">
          This agent can plan but not execute until an admin enables{' '}
          <span className="font-medium text-[var(--text-primary)]">Allow agentic actions</span> on the agent
          itself (it&apos;s a per-agent setting, separate from the plugin kill-switch). After enabling it,
          recycle the agent kernel so it re-reads the config.
        </p>
        {agentConfigUrl && (
          <a
            href={agentConfigUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-1.5 inline-flex items-center gap-1 font-medium text-[var(--accent)] hover:underline"
          >
            Open agent settings ↗
          </a>
        )}
      </div>
    );
  }
  // Generic admin-clearable refusal: the backend attached an internal deep
  // link {page, label} — one click lands on the page that clears the gate.
  if (!link) return null;
  return (
    <div className="my-1.5 rounded-lg border border-[var(--neon-yellow)]/40 bg-[var(--neon-yellow)]/5 px-3 py-2 text-xs text-[var(--text-secondary)]">
      <div className="font-semibold text-[var(--text-primary)]">
        {GATE_TITLES[code] ?? 'Action unavailable'}
      </div>
      {message && <p className="mt-0.5 leading-relaxed">{message}</p>}
      <button
        type="button"
        onClick={() => setActivePage(link.page as PageId)}
        className="mt-1.5 inline-flex items-center gap-1 font-medium text-[var(--accent)] hover:underline"
      >
        {link.label} →
      </button>
    </div>
  );
}

export function MessageView({
  message,
  conversationId,
  traceExplorer,
  now,
  streaming,
  actuatorAvailable,
  agentConfigUrl,
  onPlanDecision,
  onShowAudit,
  onSubmitActionItems,
}: {
  message: ChatMessage;
  conversationId?: string;
  traceExplorer?: TraceExplorerStatus | null;
  now: number;
  streaming: boolean;
  actuatorAvailable: boolean;
  /** Deep link to the conversation agent's DSS config (for gate-hint callouts). */
  agentConfigUrl?: string;
  onPlanDecision: (plan: PlanCardData, decision: 'approved' | 'rejected') => void;
  onShowAudit: (auditId: number) => void;
  onSubmitActionItems: (batchId: string, items: ActionItemData[]) => void;
}) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[min(85%,40rem)] rounded-xl rounded-br-sm px-3.5 py-2 bg-[var(--accent-muted)] border border-[var(--accent)]/20 text-sm text-[var(--text-primary)] whitespace-pre-wrap">
          {message.display ?? message.content}
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-0.5">
      {message.segments.map((segment: Segment, i) => {
        if (segment.type === 'text') {
          return (
            <div key={i} className="ai-analysis-markdown text-sm text-[var(--text-primary)]">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{segment.text}</ReactMarkdown>
            </div>
          );
        }
        if (segment.type === 'activity') {
          return <ActivityChips key={i} items={segment.items} />;
        }
        if (segment.type === 'gate_hint') {
          return (
            <GateHint
              key={i}
              code={segment.code}
              message={segment.message}
              link={segment.link}
              agentConfigUrl={agentConfigUrl}
            />
          );
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
        if (segment.type === 'action_items') {
          return (
            <ActionItemsCard
              key={segment.batch.batchId || i}
              batch={segment.batch}
              disabled={streaming}
              actuatorAvailable={actuatorAvailable}
              onSubmit={(items) => onSubmitActionItems(segment.batch.batchId, items)}
            />
          );
        }
        return <ExecutionCard key={i} execution={segment.execution} onShowAudit={onShowAudit} />;
      })}
      {message.traceId && (
        <div className="pt-0.5">
          <TraceAction
            traceId={message.traceId}
            conversationId={conversationId}
            messageId={message.id}
            traceExplorer={traceExplorer ?? null}
          />
        </div>
      )}
    </div>
  );
}
