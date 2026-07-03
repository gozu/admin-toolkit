import { motion } from 'framer-motion';
import { InfoDot } from '../common/InfoDot';
import { dssLinkForAction, hostBaseUrl } from '../../utils/agentLinks';
import type { ExecutionCardData } from '../../state/agentsChatStore';

export function ExecutionCard({
  execution,
  onShowAudit,
}: {
  execution: ExecutionCardData;
  /** Expand + scroll the audit timeline to this row (in-app). */
  onShowAudit?: (auditId: number) => void;
}) {
  const ok = execution.status === 'ok';
  // No link after a successful delete — the linked object no longer exists.
  const objectGone = ok && (execution.action === 'project-delete' || execution.action === 'code-env-delete');
  const targetLink = objectGone ? null : dssLinkForAction(execution.action, execution.target, execution.host);
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      className={`glass-card my-2 p-3 border-l-2 space-y-1 ${ok ? 'border-l-[var(--accent)]' : 'border-l-[var(--danger)]'}`}
    >
      <div className="flex items-center gap-2 flex-wrap">
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
        <InfoDot eduId={`action.${execution.action}`} />
        <span className="text-xs text-[var(--text-tertiary)]">
          on{' '}
          <a
            href={hostBaseUrl(execution.host)}
            target="_blank"
            rel="noreferrer"
            className="hover:text-[var(--accent)] hover:underline"
          >
            {execution.host}
          </a>
        </span>
        {execution.itemRef?.itemId && (
          <span
            className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-[var(--bg-surface)] border border-[var(--border-default)] text-[var(--text-muted)]"
            title={`From action item ${execution.itemRef.itemId} (batch ${execution.itemRef.batchId || '?'})`}
          >
            {execution.itemRef.itemId}
          </span>
        )}
        {execution.auditId != null && (
          <span className="ml-auto flex items-center gap-1">
            <button
              onClick={() => onShowAudit?.(execution.auditId as number)}
              className="text-xs text-[var(--text-muted)] font-mono hover:text-[var(--accent)] hover:underline"
              title="Show this row in the audit trail"
            >
              audit #{execution.auditId}
            </button>
            <InfoDot eduId="concept.audit-trail" />
          </span>
        )}
      </div>
      {execution.auditWarning && (
        <div className="text-xs text-[var(--neon-amber)]">{execution.auditWarning}</div>
      )}
      {targetLink && (
        <a
          href={targetLink}
          target="_blank"
          rel="noreferrer"
          className="inline-block text-xs text-[var(--accent)] hover:underline"
        >
          open the affected area in DSS ↗
        </a>
      )}
    </motion.div>
  );
}
