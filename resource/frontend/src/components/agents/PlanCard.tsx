import { motion } from 'framer-motion';
import { InfoDot } from '../common/InfoDot';
import { dssLinkForAction } from '../../utils/agentLinks';
import type { PlanCardData } from '../../state/agentsChatStore';

const PLAN_HIDDEN_KEYS = new Set(['summary', 'warning', 'warnings', 'irreversible', 'backupFolder', 'note']);

export function PlanCard({
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
  const targetLink = dssLinkForAction(plan.action, plan.canonicalTarget, plan.host);

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
          <InfoDot eduId="concept.plan" />
          <span className="text-sm font-mono text-[var(--text-primary)] truncate">{plan.action}</span>
          <InfoDot eduId={`action.${plan.action}`} />
          <span className="text-xs text-[var(--text-tertiary)]">on {plan.host}</span>
          {plan.itemRef?.itemId && (
            <span
              className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-[var(--bg-surface)] border border-[var(--border-default)] text-[var(--text-muted)]"
              title={`From action item ${plan.itemRef.itemId} (batch ${plan.itemRef.batchId || '?'})`}
            >
              {plan.itemRef.itemId}
            </span>
          )}
        </div>
        {!plan.decision && !expired && (
          <span className="flex items-center gap-1">
            <span className={`text-xs tabular-nums ${secondsLeft < 120 ? 'text-[var(--neon-amber)]' : 'text-[var(--text-tertiary)]'}`}>
              {Math.floor(secondsLeft / 60)}:{String(secondsLeft % 60).padStart(2, '0')}
            </span>
            <InfoDot eduId="concept.confirm-token" />
          </span>
        )}
      </div>

      {typeof plan.plan.summary === 'string' && (
        <p className="text-sm text-[var(--text-primary)] leading-snug">
          {plan.plan.summary}
          {targetLink && (
            <>
              {' '}
              <a
                href={targetLink}
                target="_blank"
                rel="noreferrer"
                className="text-xs text-[var(--accent)] hover:underline whitespace-nowrap"
              >
                open in DSS ↗
              </a>
            </>
          )}
        </p>
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
        <div className="text-xs text-[var(--danger)] flex items-center gap-1.5">
          {plan.plan.irreversible}
          <InfoDot eduId="concept.risk-colors" />
        </div>
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
