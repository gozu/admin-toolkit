import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
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
        <div className="max-w-[85%] rounded-xl rounded-br-sm px-3.5 py-2 bg-[var(--accent-muted)] border border-[var(--accent)]/20 text-sm text-[var(--text-primary)] whitespace-pre-wrap">
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
    </div>
  );
}
