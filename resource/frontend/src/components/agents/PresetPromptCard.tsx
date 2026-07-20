import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import type { PresetMeta } from '../../state/agentsChatStore';

/** Small badge glyphs per preset kind — inline so no icon library rides in. */
function PresetIcon({ icon }: { icon: PresetMeta['icon'] }) {
  const common = {
    className: 'h-3.5 w-3.5',
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 2,
  } as const;
  switch (icon) {
    case 'star':
      return (
        <svg {...common} fill="currentColor" stroke="none">
          <path d="M12 2.5l2.9 6.05 6.6.85-4.85 4.6 1.25 6.5L12 17.3l-5.9 3.2 1.25-6.5L2.5 9.4l6.6-.85L12 2.5z" />
        </svg>
      );
    case 'approve':
      return (
        <svg {...common}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.5l5 5 10-11" />
        </svg>
      );
    case 'reject':
      return (
        <svg {...common}>
          <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
        </svg>
      );
    case 'handoff':
      return (
        <svg {...common}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 12h13M11 6l6 6-6 6M20 4v16" />
        </svg>
      );
    default:
      // 'prompt' — a spark/chat glyph for plain catalog prompts
      return (
        <svg {...common}>
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z"
          />
        </svg>
      );
  }
}

/**
 * Compact card for a preset send (hero megaprompt, catalog prompt, synthetic
 * approve/reject/handoff): icon + title + group chip + two-line gist, with a
 * click-to-expand reveal of the full text that actually went to the agent.
 * Replaces the raw megaprompt dump in the transcript — the full prompt still
 * travels to the backend as `content` untouched.
 */
export function PresetPromptCard({ preset, content }: { preset: PresetMeta; content: string }) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const gist = preset.gist ?? content;
  const expandLabel =
    preset.kind === 'action' ? 'Show message sent to the agent' : 'Show full prompt';
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard unavailable — leave the label as-is
    }
  };
  return (
    <div className="preset-card max-w-[min(85%,28rem)] rounded-xl rounded-br-sm px-3.5 py-2.5 text-left">
      <div className="flex items-center gap-2 min-w-0">
        <span className="preset-card-icon shrink-0" data-icon={preset.icon} aria-hidden="true">
          <PresetIcon icon={preset.icon} />
        </span>
        <span className="min-w-0 flex-1 truncate text-sm font-semibold text-[var(--text-primary)]">
          {preset.title}
        </span>
        {preset.group && (
          <span className="shrink-0 rounded-full border border-[var(--accent)]/25 bg-[var(--accent)]/10 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-[var(--accent)]">
            {preset.group}
          </span>
        )}
      </div>
      {gist && (
        <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-[var(--text-secondary)] whitespace-pre-line">
          {gist}
        </p>
      )}
      <div className="mt-1.5 flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="inline-flex items-center gap-1 text-[11px] font-medium text-[var(--accent)] opacity-80 transition-opacity hover:opacity-100"
        >
          <motion.span
            animate={{ rotate: expanded ? 90 : 0 }}
            transition={{ duration: 0.18 }}
            className="inline-flex"
            aria-hidden="true"
          >
            <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 6l6 6-6 6" />
            </svg>
          </motion.span>
          {expanded ? 'Hide' : expandLabel}
        </button>
        <button
          type="button"
          onClick={() => void copy()}
          title="Copy the full text sent to the agent"
          className="text-[11px] text-[var(--text-muted)] opacity-0 transition-opacity group-hover:opacity-100 hover:text-[var(--text-secondary)] focus:opacity-100"
        >
          {copied ? 'copied ✓' : 'copy'}
        </button>
      </div>
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: 'easeOut' }}
            className="overflow-hidden"
          >
            <pre className="mt-2 max-h-64 overflow-y-auto whitespace-pre-wrap rounded-md border border-[var(--border-default)] bg-[var(--bg-tertiary)]/70 px-2.5 pt-2 pb-3 font-mono text-[11px] leading-relaxed text-[var(--text-secondary)]">
              {content}
            </pre>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
