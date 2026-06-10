import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { useDiag } from '../context/DiagContext';
import type { PageId } from '../types';
import { COMMAND_PALETTE_MODULES } from '../utils/moduleRegistry';

interface CommandPaletteProps {
  isOpen: boolean;
  onClose: () => void;
}

interface PageDef {
  id: PageId;
  label: string;
  section: string;
  keywords: string[];
}

const PAGE_DEFS: PageDef[] = [...COMMAND_PALETTE_MODULES];

const SECTION_ICONS: Record<string, string> = {
  Overview: '\u2302',
  Connections: '\u26A0',
  Projects: '\u25C6',
  Plugins: '\u2692',
  'Code Envs': '\u2318',
  'AI Compute': '\u25A3',
  Misc: '\u2699',
};

function fuzzyMatch(query: string, def: PageDef): boolean {
  const q = query.toLowerCase();
  if (def.label.toLowerCase().includes(q)) return true;
  if (def.section.toLowerCase().includes(q)) return true;
  return def.keywords.some((kw) => kw.toLowerCase().includes(q));
}

/**
 * Inner content rendered only when the palette is open.
 * Mounting/unmounting naturally resets all state (query, selected index).
 */
function CommandPaletteContent({ onClose }: { onClose: () => void }) {
  const { setActivePage } = useDiag();
  const [query, setQuery] = useState('');
  const [rawSelectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const results = useMemo(() => {
    if (!query.trim()) return PAGE_DEFS;
    return PAGE_DEFS.filter((def) => fuzzyMatch(query.trim(), def));
  }, [query]);

  // Derive clamped index from raw index + results length
  const selectedIndex = Math.min(rawSelectedIndex, Math.max(0, results.length - 1));

  // Auto-focus input on mount
  useEffect(() => {
    requestAnimationFrame(() => {
      inputRef.current?.focus();
    });
  }, []);

  // Scroll selected item into view
  useEffect(() => {
    if (!listRef.current) return;
    const items = listRef.current.querySelectorAll('[data-palette-item]');
    const target = items[selectedIndex];
    if (target) {
      target.scrollIntoView({ block: 'nearest' });
    }
  }, [selectedIndex]);

  const handleSelect = useCallback(
    (pageId: PageId) => {
      setActivePage(pageId);
      onClose();
    },
    [setActivePage, onClose],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      switch (e.key) {
        case 'ArrowDown': {
          e.preventDefault();
          setSelectedIndex((prev) => (prev + 1) % Math.max(1, results.length));
          break;
        }
        case 'ArrowUp': {
          e.preventDefault();
          setSelectedIndex((prev) => (prev - 1 + results.length) % Math.max(1, results.length));
          break;
        }
        case 'Enter': {
          e.preventDefault();
          if (results[selectedIndex]) {
            handleSelect(results[selectedIndex].id);
          }
          break;
        }
        case 'Escape': {
          e.preventDefault();
          onClose();
          break;
        }
      }
    },
    [results, selectedIndex, handleSelect, onClose],
  );

  const handleBackdropClick = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === e.currentTarget) {
        onClose();
      }
    },
    [onClose],
  );

  const heading = query.trim() ? 'Results' : 'All Pages';

  return (
    <motion.div
      className="fixed inset-0 z-60 flex items-start justify-center pt-[15vh]"
      style={{ backgroundColor: 'rgba(0, 0, 0, 0.5)', backdropFilter: 'blur(4px)' }}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.15 }}
      onClick={handleBackdropClick}
    >
      <motion.div
        className="w-[560px] max-w-[90vw] rounded-xl border border-[var(--border-default)] bg-[var(--bg-elevated)]/85 backdrop-blur-2xl shadow-2xl overflow-hidden"
        initial={{ opacity: 0, scale: 0.92, y: -14 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96, y: -8, transition: { duration: 0.1 } }}
        transition={{ type: 'spring', stiffness: 380, damping: 28 }}
        onKeyDown={handleKeyDown}
      >
        {/* Search input */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-[var(--border-default)]">
          <svg
            className="w-5 h-5 text-[var(--text-tertiary)] shrink-0"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
            />
          </svg>
          <input
            ref={inputRef}
            type="text"
            aria-label="Search commands and modules"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search pages..."
            className="flex-1 bg-transparent text-[var(--text-primary)] placeholder-[var(--text-tertiary)] text-base outline-none"
            spellCheck={false}
            autoComplete="off"
          />
          <kbd className="hidden sm:inline-flex items-center px-1.5 py-0.5 text-[10px] font-mono text-[var(--text-tertiary)] border border-[var(--border-default)] rounded">
            ESC
          </kbd>
        </div>

        {/* Results list */}
        <div ref={listRef} className="max-h-[400px] overflow-y-auto py-2">
          {results.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-[var(--text-tertiary)]">
              No pages matching &ldquo;{query}&rdquo;
            </div>
          ) : (
            <>
              <div className="px-4 py-1 text-[10px] font-medium uppercase tracking-wider text-[var(--text-tertiary)]">
                {heading}
              </div>
              {results.map((def, index) => {
                const isSelected = index === selectedIndex;
                return (
                  <button
                    key={def.id}
                    data-palette-item
                    onClick={() => handleSelect(def.id)}
                    onMouseEnter={() => setSelectedIndex(index)}
                    className={`w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors ${
                      isSelected
                        ? 'bg-[var(--bg-hover)] text-[var(--text-primary)]'
                        : 'text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]'
                    }`}
                  >
                    <span className="w-6 text-center text-base shrink-0" aria-hidden>
                      {SECTION_ICONS[def.section] || '\u2022'}
                    </span>
                    <span className="flex-1 min-w-0">
                      <span
                        className={`text-sm font-medium ${isSelected ? 'text-[var(--text-primary)]' : ''}`}
                      >
                        {def.label}
                      </span>
                      <span className="ml-2 text-xs text-[var(--text-tertiary)]">
                        {def.section}
                      </span>
                    </span>
                    {isSelected && (
                      <kbd className="hidden sm:inline-flex items-center px-1.5 py-0.5 text-[10px] font-mono text-[var(--text-tertiary)] border border-[var(--border-default)] rounded">
                        Enter
                      </kbd>
                    )}
                  </button>
                );
              })}
            </>
          )}
        </div>

        {/* Footer hint */}
        <div className="flex items-center gap-4 px-4 py-2 border-t border-[var(--border-default)] text-[10px] text-[var(--text-tertiary)]">
          <span className="flex items-center gap-1">
            <kbd className="px-1 py-0.5 border border-[var(--border-default)] rounded font-mono">
              &uarr;&darr;
            </kbd>
            navigate
          </span>
          <span className="flex items-center gap-1">
            <kbd className="px-1 py-0.5 border border-[var(--border-default)] rounded font-mono">
              Enter
            </kbd>
            select
          </span>
          <span className="flex items-center gap-1">
            <kbd className="px-1 py-0.5 border border-[var(--border-default)] rounded font-mono">
              Esc
            </kbd>
            close
          </span>
        </div>
      </motion.div>
    </motion.div>
  );
}

export function CommandPalette({ isOpen, onClose }: CommandPaletteProps) {
  return <AnimatePresence>{isOpen && <CommandPaletteContent onClose={onClose} />}</AnimatePresence>;
}
