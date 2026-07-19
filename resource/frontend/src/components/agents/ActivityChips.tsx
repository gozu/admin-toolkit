import { motion } from 'framer-motion';
import { RichPopover } from '../common/RichPopover';
import { InfoDot } from '../common/InfoDot';
import type { ActivityItem } from '../../state/agentsChatStore';

function formatMs(ms?: number): string {
  if (!ms || ms <= 0) return '';
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function ChipDetail({ item }: { item: ActivityItem }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5">
        <span className="text-xs font-semibold font-mono text-[var(--text-primary)]">{item.name}</span>
        <InfoDot eduId={`tool.${item.name}`} />
        {!item.running && item.durationMs !== undefined && (
          <span className="ml-auto text-[10px] text-[var(--text-muted)] tabular-nums">
            {formatMs(item.durationMs)}
          </span>
        )}
      </div>
      {item.args !== null && typeof item.args === 'object' && Object.keys(item.args).length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">Arguments</div>
          <pre className="mt-0.5 max-h-40 overflow-auto rounded bg-[var(--bg-surface)] p-1.5 text-[10px] leading-relaxed text-[var(--text-secondary)] whitespace-pre-wrap break-all">
            {JSON.stringify(item.args, null, 1)}
          </pre>
        </div>
      )}
      <div className="text-[10px]">
        {item.running ? (
          <span className="text-[var(--neon-yellow)]">running…</span>
        ) : item.error ? (
          <span className="text-[var(--danger)]">failed: {item.error}</span>
        ) : item.ok === false ? (
          <span className="text-[var(--danger)]">failed</span>
        ) : (
          <span className="text-[var(--text-tertiary)]">completed OK</span>
        )}
      </div>
    </div>
  );
}

// Tool-call activity chips. Each chip opens a RichPopover with the tool's
// arguments and outcome (replaces the old lossy `title` tooltip).
export function ActivityChips({ items }: { items: ActivityItem[] }) {
  return (
    <div className="flex flex-wrap gap-1.5 my-1.5">
      {items.map((item, i) => (
        <RichPopover key={`${item.name}-${i}`} width={300} content={<ChipDetail item={item} />}>
          <motion.span
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-mono border transition-colors hover:border-[var(--accent)]/50 ${
              item.running
                ? 'chip-live border-[var(--neon-yellow)]/40 text-[var(--neon-yellow)] bg-[var(--bg-surface)]'
                : item.ok === false || item.error
                  ? 'border-[var(--danger)]/40 text-[var(--danger)] bg-[var(--bg-surface)]'
                  : 'border-[var(--border-default)] text-[var(--text-secondary)] bg-[var(--bg-surface)]'
            }`}
          >
            {item.running ? (
              <span className="w-2 h-2 rounded-full bg-[var(--neon-yellow)] animate-pulse" />
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
        </RichPopover>
      ))}
    </div>
  );
}
