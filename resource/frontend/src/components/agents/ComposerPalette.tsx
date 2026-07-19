import { useEffect, useRef } from 'react';
import { motion } from 'framer-motion';
import type { PaletteEntry } from '../../utils/agentPromptCatalog';

/**
 * Inline slash-command palette: typing "/" in the composer surfaces the
 * prompt catalog right above it — arrow keys + Enter insert into the draft
 * (Enter never sends while the palette is open). Keyboard events stay on the
 * textarea; this component is display + click only.
 */
export function ComposerPalette({
  matches,
  selectedIndex,
  onPick,
  onHoverIndex,
}: {
  matches: PaletteEntry[];
  selectedIndex: number;
  onPick: (entry: PaletteEntry) => void;
  onHoverIndex: (index: number) => void;
}) {
  const rowRefs = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    rowRefs.current[selectedIndex]?.scrollIntoView({ block: 'nearest' });
  }, [selectedIndex]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 6, scale: 0.99 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.14 }}
      className="absolute bottom-full left-0 right-0 z-30 mb-2 overflow-hidden rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] shadow-2xl"
    >
      <div className="max-h-72 overflow-y-auto p-1">
        {matches.map((entry, i) => (
          <button
            key={entry.id}
            ref={(el) => {
              rowRefs.current[i] = el;
            }}
            onClick={() => onPick(entry)}
            onMouseEnter={() => onHoverIndex(i)}
            className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left transition-colors ${
              i === selectedIndex ? 'bg-[var(--bg-hover)]' : ''
            }`}
          >
            {entry.mega && <span className="shrink-0 text-[11px] text-[var(--accent)]">★</span>}
            <span
              className={`min-w-0 flex-1 truncate text-xs ${
                i === selectedIndex ? 'text-[var(--text-primary)]' : 'text-[var(--text-secondary)]'
              }`}
              title={entry.prompt}
            >
              {entry.label}
            </span>
            <span className="shrink-0 text-[10px] text-[var(--text-muted)]">{entry.section}</span>
          </button>
        ))}
      </div>
      <div className="flex items-center gap-3 border-t border-[var(--border-default)] px-2.5 py-1 text-[10px] text-[var(--text-muted)]">
        <span>↑↓ navigate</span>
        <span>Enter / Tab insert</span>
        <span>Esc dismiss</span>
        <span className="ml-auto">prompt library</span>
      </div>
    </motion.div>
  );
}
