import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { fetchJson } from '../../utils/api';
import { ActivityChips } from './ActivityChips';
import { PlanCard } from './PlanCard';
import { ExecutionCard } from './ExecutionCard';
import { ActionItemsCard } from './ActionItemsCard';
import type {
  ActionItemData,
  ChatMessage,
  PlanCardData,
  Segment,
} from '../../state/agentsChatStore';

/** Copy the turn's native dku-trace JSON (for Trace Explorer's "Paste a new
 * trace"). The backend keeps only the last few traces — expiry is normal. */
function TraceChip({ traceId }: { traceId: string }) {
  const [state, setState] = useState<'idle' | 'copied' | 'expired'>('idle');
  const copy = async () => {
    try {
      const data = await fetchJson<{ available: boolean; trace?: unknown }>(
        `/api/agents/last-trace?id=${encodeURIComponent(traceId)}`,
      );
      if (!data.available || !data.trace) throw new Error('expired');
      await navigator.clipboard.writeText(JSON.stringify(data.trace, null, 2));
      setState('copied');
    } catch {
      setState('expired');
    }
    setTimeout(() => setState('idle'), 2500);
  };
  return (
    <button
      onClick={() => void copy()}
      className="text-[11px] text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors"
      title="Copy this turn's trace JSON — paste it into Trace Explorer for instant inspection"
    >
      {state === 'copied' ? 'trace copied ✓' : state === 'expired' ? 'trace expired' : 'copy trace'}
    </button>
  );
}

export function MessageView({
  message,
  now,
  streaming,
  actuatorAvailable,
  onPlanDecision,
  onShowAudit,
  onSubmitActionItems,
}: {
  message: ChatMessage;
  now: number;
  streaming: boolean;
  actuatorAvailable: boolean;
  onPlanDecision: (plan: PlanCardData, decision: 'approved' | 'rejected') => void;
  onShowAudit: (auditId: number) => void;
  onSubmitActionItems: (batchId: string, items: ActionItemData[]) => void;
}) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[min(85%,40rem)] rounded-xl rounded-br-sm px-3.5 py-2 bg-[var(--accent-muted)] border border-[var(--accent)]/20 text-sm text-[var(--text-primary)] whitespace-pre-wrap">
          {message.content}
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
          <TraceChip traceId={message.traceId} />
        </div>
      )}
    </div>
  );
}
