import { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import hljs from 'highlight.js';
import 'highlight.js/styles/monokai-sublime.css';
import { InfoDot } from '../common/InfoDot';
import { dssLinkForAction, humanTarget } from '../../utils/agentLinks';
import type { PlanCardData } from '../../state/agentsChatStore';

const PLAN_HIDDEN_KEYS = new Set([
  'summary', 'warning', 'warnings', 'irreversible', 'backupFolder', 'note',
  'targets', 'targetCount', 'code', 'venue', 'executeNote',
]);

/** Power-Up plan payloads carry the exact script — render it verbatim and
 *  syntax-highlighted (the FileViewer/K8s hljs pattern) so the per-run
 *  "I have read this code" ack means something. */
function PowerUpCode({ code }: { code: string }) {
  const codeRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const el = codeRef.current;
    if (!el) return;
    el.textContent = code;
    delete el.dataset.highlighted;
    el.className = 'language-python hljs bg-transparent p-0';
    try {
      hljs.highlightElement(el);
    } catch {
      /* leave plain text on failure */
    }
  }, [code]);
  return (
    <div className="rounded-md border border-[var(--border-default)] bg-[#23241f] max-h-72 overflow-auto">
      <pre className="p-2.5 text-[11px] leading-relaxed whitespace-pre">
        <code ref={codeRef} />
      </pre>
    </div>
  );
}

const BATCH_TARGETS_SHOWN = 8;

interface BatchTargetRow {
  target?: Record<string, unknown>;
  summary?: string;
}

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
  // Confirm-window fuse: drains linearly amber → red under the header.
  const ttl = plan.ttlSeconds && plan.ttlSeconds > 0 ? plan.ttlSeconds : 900;
  const fusePct = Math.max(0, Math.min(100, (secondsLeft / ttl) * 100));
  // Power-Up gate 2: the exact code + an explicit read-ack that arms Approve.
  const code = typeof plan.plan.code === 'string' ? plan.plan.code : null;
  const [codeAck, setCodeAck] = useState(false);
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
  const batchTargets = Array.isArray(plan.plan.targets)
    ? (plan.plan.targets as BatchTargetRow[])
    : null;

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      className={`glass-card my-2 p-3.5 border-l-2 border-l-[var(--neon-amber)] space-y-2.5 ${
        !plan.decision && !expired ? 'border-beam' : ''
      }`}
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

      {!plan.decision && !expired && (
        <div className="h-0.5 overflow-hidden rounded-full bg-[var(--border-default)]/50">
          <div
            className={`h-full rounded-full transition-[width] duration-1000 ease-linear ${
              secondsLeft < 120 ? 'bg-[var(--danger)]' : 'bg-[var(--neon-amber)]'
            }`}
            style={{ width: `${fusePct}%` }}
          />
        </div>
      )}

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

      {batchTargets && batchTargets.length > 0 && (
        <div className="space-y-0.5">
          <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
            {batchTargets.length} targets — one approval covers all
          </div>
          <ol className="space-y-0.5">
            {batchTargets.slice(0, BATCH_TARGETS_SHOWN).map((row, i) => (
              <li
                key={i}
                className="text-xs text-[var(--text-secondary)] leading-snug truncate"
                title={row.summary || humanTarget(row.target)}
              >
                <span className="text-[var(--text-muted)] tabular-nums">{i + 1}.</span>{' '}
                {row.summary || humanTarget(row.target)}
              </li>
            ))}
          </ol>
          {batchTargets.length > BATCH_TARGETS_SHOWN && (
            <div className="text-[10px] text-[var(--text-muted)]">
              +{batchTargets.length - BATCH_TARGETS_SHOWN} more targets in this batch
            </div>
          )}
        </div>
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

      {code !== null && (
        <div className="space-y-1.5">
          <div className="text-[10px] uppercase tracking-wider text-[var(--danger)]">
            Power-Up script — runs with admin credentials
          </div>
          <PowerUpCode code={code} />
          {typeof plan.plan.venue === 'string' && (
            <div className="text-[10px] text-[var(--text-muted)]">{plan.plan.venue}</div>
          )}
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

      {code !== null && !plan.decision && !expired && (
        <label className="flex items-start gap-2 rounded-md border border-[var(--danger)]/40 bg-[var(--danger)]/5 px-2.5 py-1.5 text-xs text-[var(--text-primary)] cursor-pointer">
          <input
            type="checkbox"
            checked={codeAck}
            onChange={(e) => setCodeAck(e.target.checked)}
            className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-[var(--danger)]"
          />
          <span>
            I have read this code and understand it runs with the toolkit&apos;s admin
            credentials.
          </span>
        </label>
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
              disabled={disabled || (code !== null && !codeAck)}
              title={code !== null && !codeAck ? 'Read the code and tick the acknowledgment first.' : undefined}
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
