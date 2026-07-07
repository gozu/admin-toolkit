import { useState } from 'react';
import { motion } from 'framer-motion';
import { InfoDot } from '../common/InfoDot';
import { useRowSelection } from '../../hooks/useRowSelection';
import type { ActionItemData, ActionItemsCardData } from '../../state/agentsChatStore';

// Risk → color semantics per the toolkit contract: red = destructive,
// amber = partial/locking, green = safe. (Not lifecycle tones — no
// ProgressIndicator here.)
const RISK_DOT: Record<string, string> = {
  red: 'bg-[var(--danger)]',
  amber: 'bg-[var(--neon-amber)]',
  green: 'bg-[var(--accent)]',
};

function ItemRow({
  item,
  checked,
  locked,
  submitted,
  onToggle,
}: {
  item: ActionItemData;
  checked: boolean;
  locked: boolean;
  submitted: boolean;
  onToggle: () => void;
}) {
  const [showEvidence, setShowEvidence] = useState(false);
  const selectable = item.actionable && !locked && !submitted;
  return (
    <div
      className={`rounded-md border px-2.5 py-2 transition-colors ${
        checked
          ? 'border-[var(--accent)]/40 bg-[var(--accent-muted)]'
          : 'border-[var(--border-default)] bg-[var(--bg-surface)]'
      } ${submitted ? 'opacity-60' : ''}`}
    >
      <label className={`flex items-start gap-2.5 ${selectable ? 'cursor-pointer' : 'cursor-default'}`}>
        <input
          type="checkbox"
          checked={checked || submitted}
          disabled={!selectable}
          onChange={onToggle}
          className="mt-0.5 accent-[var(--accent)] disabled:opacity-40"
        />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`w-2 h-2 rounded-full shrink-0 ${RISK_DOT[item.risk] || RISK_DOT.amber}`} />
            <span className="text-sm text-[var(--text-primary)] leading-snug">{item.title}</span>
            {item.action && (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-[var(--bg-base)] border border-[var(--border-default)] text-[var(--text-secondary)] inline-flex items-center gap-1">
                {item.action}
                <InfoDot eduId={`action.${item.action}`} />
              </span>
            )}
            {item.host !== 'local' && (
              <span className="text-[10px] text-[var(--text-muted)]">@{item.host}</span>
            )}
            {!item.actionable && (
              <span className="px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wider border border-[var(--border-default)] text-[var(--text-muted)]">
                advisory
              </span>
            )}
            {submitted && (
              <span className="text-[10px] text-[var(--accent)] font-medium">✓ sent to actuator</span>
            )}
          </div>
          {item.why && <p className="text-xs text-[var(--text-secondary)] leading-snug">{item.why}</p>}
          {item.validation && (
            <p className="text-[10px] text-[var(--neon-amber)]">{item.validation}</p>
          )}
          {item.evidence.length > 0 && (
            <button
              type="button"
              onClick={(e) => {
                e.preventDefault();
                setShowEvidence((v) => !v);
              }}
              className="text-[10px] text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
            >
              {showEvidence ? '▾' : '▸'} evidence ({item.evidence.length})
            </button>
          )}
          {showEvidence && (
            <ul className="space-y-0.5 pl-3">
              {item.evidence.map((line, i) => (
                <li key={i} className="text-[10px] text-[var(--text-tertiary)] font-mono leading-relaxed">
                  {line}
                </li>
              ))}
            </ul>
          )}
        </div>
      </label>
    </div>
  );
}

/**
 * Risk-colored checklist of proposed action items. Checked actionable items
 * are handed to the ops-actuator in one batch; advisory items are shown
 * (disabled) so nothing the agent found gets silently lost.
 */
export function ActionItemsCard({
  batch,
  disabled,
  actuatorAvailable,
  onSubmit,
}: {
  batch: ActionItemsCardData;
  disabled: boolean;
  actuatorAvailable: boolean;
  onSubmit: (items: ActionItemData[]) => void;
}) {
  const { selectedKeys, toggleSelect, toggleSelectAll, clear } = useRowSelection();
  const submitted = new Set(batch.submittedIds);
  const selectableIds = batch.items
    .filter((item) => item.actionable && !submitted.has(item.id))
    .map((item) => item.id);
  const checkedItems = batch.items.filter((item) => selectedKeys.has(item.id) && !submitted.has(item.id));

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      className="glass-card my-2 p-3.5 space-y-2.5 border-l-2 border-l-[var(--accent)]"
    >
      <div className="flex items-center gap-2">
        <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider bg-[var(--accent-muted)] border border-[var(--accent)]/30 text-[var(--accent)]">
          Action items
        </span>
        <InfoDot eduId="concept.action-items" />
        <span className="text-xs text-[var(--text-tertiary)]">
          {batch.items.length} proposed{batch.droppedCount ? ` (+${batch.droppedCount} over cap)` : ''}
        </span>
        <span className="ml-auto flex items-center gap-1 text-[10px] text-[var(--text-muted)]">
          <span className="w-1.5 h-1.5 rounded-full bg-[var(--danger)]" /> risky
          <span className="w-1.5 h-1.5 rounded-full bg-[var(--neon-amber)] ml-1.5" /> caution
          <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent)] ml-1.5" /> safe
          <InfoDot eduId="concept.risk-colors" className="ml-0.5" />
        </span>
      </div>

      <div className="space-y-1.5">
        {batch.items.map((item) => (
          <ItemRow
            key={item.id}
            item={item}
            checked={selectedKeys.has(item.id)}
            locked={disabled}
            submitted={submitted.has(item.id)}
            onToggle={() => toggleSelect(item.id)}
          />
        ))}
      </div>

      {selectableIds.length > 0 && (
        <div className="flex items-center gap-2 pt-1">
          <button
            onClick={() => {
              if (checkedItems.length === 0) return;
              onSubmit(checkedItems);
              clear();
            }}
            disabled={disabled || checkedItems.length === 0 || !actuatorAvailable}
            className="px-3 py-1 text-xs font-semibold rounded-md bg-[var(--accent)] text-white hover:opacity-90 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed"
            title={actuatorAvailable ? undefined : 'No action-capable agent found on this host'}
          >
            Plan{checkedItems.length > 0 ? ` ${checkedItems.length}` : ''} selected action
            {checkedItems.length === 1 ? '' : 's'}
          </button>
          <button
            onClick={() => toggleSelectAll(selectableIds)}
            disabled={disabled}
            className="px-2.5 py-1 text-xs rounded-md border border-[var(--border-default)] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] transition-colors disabled:opacity-40"
          >
            {selectableIds.every((id) => selectedKeys.has(id)) ? 'Clear all' : 'Select all actionable'}
          </button>
          <InfoDot eduId="concept.handoff" />
        </div>
      )}
    </motion.div>
  );
}
