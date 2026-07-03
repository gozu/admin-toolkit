import { RichPopover } from './RichPopover';
import { eduEntry } from '../../utils/agentEduContent';

interface InfoDotProps {
  /** Key into the EDU record (e.g. 'concept.plan', 'tool.db_health'). */
  eduId: string;
  className?: string;
}

// 14px ⓘ affordance opening an educational RichPopover. Unknown eduIds render
// nothing, so callers can pass dynamic ids (`tool.<name>`) without guarding.
export function InfoDot({ eduId, className }: InfoDotProps) {
  const entry = eduEntry(eduId);
  if (!entry) return null;
  return (
    <RichPopover
      ariaLabel={`About: ${entry.title}`}
      className={className}
      content={
        <div className="space-y-1.5">
          <div className="text-xs font-semibold text-[var(--text-primary)]">{entry.title}</div>
          {entry.body.map((paragraph, i) => (
            <p key={i} className="text-xs leading-relaxed text-[var(--text-secondary)]">
              {paragraph}
            </p>
          ))}
        </div>
      }
    >
      <span
        aria-hidden
        className="inline-flex h-3.5 w-3.5 items-center justify-center rounded-full border border-[var(--border-default)] text-[9px] font-serif italic leading-none text-[var(--text-muted)] transition-colors hover:border-[var(--accent)] hover:text-[var(--accent)]"
      >
        i
      </span>
    </RichPopover>
  );
}
