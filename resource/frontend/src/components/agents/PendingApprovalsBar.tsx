import { useState } from 'react';
import { motion } from 'framer-motion';
import { Modal } from '../Modal';
import { Button } from '../common/Button';
import { InfoDot } from '../common/InfoDot';
import { humanTarget, targetTitle } from '../../utils/agentLinks';
import type { PlanCardData } from '../../state/agentsChatStore';

/**
 * Shown when ≥2 undecided plan cards are pending in the actuator
 * conversation: approve or reject them all in one message. Each plan keeps
 * its own confirm token; the actuator executes each independently (one audit
 * row per plan). Individual card buttons keep working alongside.
 */
export function PendingApprovalsBar({
  plans,
  disabled,
  onApproveAll,
  onRejectAll,
}: {
  plans: PlanCardData[];
  disabled: boolean;
  onApproveAll: (plans: PlanCardData[]) => void;
  onRejectAll: (plans: PlanCardData[]) => void;
}) {
  const [confirming, setConfirming] = useState<'approve' | 'reject' | null>(null);
  if (plans.length < 2) return null;

  const verb = confirming === 'approve' ? 'Approve' : 'Reject';
  return (
    <>
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        className="glass-card p-2.5 flex items-center gap-2 border-l-2 border-l-[var(--neon-amber)]"
      >
        <span className="text-xs text-[var(--text-primary)]">
          <span className="font-semibold">{plans.length} plans</span> awaiting your decision
        </span>
        <InfoDot eduId="concept.plan" />
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setConfirming('approve')}
            disabled={disabled}
            className="px-3 py-1 text-xs font-semibold rounded-md bg-[var(--accent)] text-white hover:opacity-90 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Approve all ({plans.length})
          </button>
          <button
            onClick={() => setConfirming('reject')}
            disabled={disabled}
            className="px-3 py-1 text-xs font-medium rounded-md border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Reject all
          </button>
        </div>
      </motion.div>

      <Modal
        isOpen={confirming !== null}
        onClose={() => setConfirming(null)}
        title={`${verb} ${plans.length} plans?`}
        footer={
          <div className="flex items-center justify-end gap-2">
            <Button variant="modalCancel" onClick={() => setConfirming(null)}>
              Cancel
            </Button>
            <Button
              variant={confirming === 'approve' ? 'modalDanger' : 'modalCancel'}
              onClick={() => {
                if (confirming === 'approve') onApproveAll(plans);
                else onRejectAll(plans);
                setConfirming(null);
              }}
            >
              {verb} all {plans.length}
            </Button>
          </div>
        }
      >
        <div className="space-y-2">
          <p className="text-sm text-[var(--text-secondary)]">
            {confirming === 'approve'
              ? 'The actuator will execute each plan independently — one audit row each. This is your explicit approval of every plan below:'
              : 'The actuator will stand down on all of the plans below:'}
          </p>
          <ul className="space-y-1">
            {plans.map((plan) => (
              <li
                key={plan.confirmToken}
                className="flex items-center gap-2 rounded-md border border-[var(--border-default)] bg-[var(--bg-surface)] px-2.5 py-1.5"
              >
                <span className="text-xs font-mono text-[var(--text-primary)]">{plan.action}</span>
                <span className="text-[10px] text-[var(--text-tertiary)]">on {plan.host}</span>
                <span
                  className="ml-auto max-w-[14rem] truncate text-[10px] font-mono text-[var(--text-muted)]"
                  title={targetTitle(plan.canonicalTarget)}
                >
                  {humanTarget(plan.canonicalTarget)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </Modal>
    </>
  );
}
