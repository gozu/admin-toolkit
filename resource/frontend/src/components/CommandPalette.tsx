import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { useDiag } from '../context/DiagContext';
import type { PageId } from '../types';
import { COMMAND_PALETTE_MODULES, EXPERIMENTAL_PAGES } from '../utils/moduleRegistry';
import { SHOW_EXPERIMENTAL_STORAGE_KEY } from './pages/SettingsPage';
import { useToggleFlag } from '../hooks/useToggleFlag';

const IS_MAC =
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad|iPod/.test(navigator.platform);

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

const RECENT_PAGES_KEY = 'admin-toolkit-recent-pages';
const MAX_RECENT_STORED = 6;
const MAX_RECENT_SHOWN = 5;

function loadRecentPageIds(): string[] {
  try {
    const raw = window.localStorage.getItem(RECENT_PAGES_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((v): v is string => typeof v === 'string');
  } catch {
    return [];
  }
}

function recordRecentPage(pageId: PageId): void {
  try {
    const next = [pageId, ...loadRecentPageIds().filter((id) => id !== pageId)].slice(
      0,
      MAX_RECENT_STORED,
    );
    window.localStorage.setItem(RECENT_PAGES_KEY, JSON.stringify(next));
  } catch {
    // localStorage unavailable; recents are best-effort
  }
}

/**
 * Inner content rendered only when the palette is open.
 * Mounting/unmounting naturally resets all state (query, selected index).
 */
function CommandPaletteContent({ onClose }: { onClose: () => void }) {
  const { setActivePage } = useDiag();
  const reducedMotion = useReducedMotion();
  const [query, setQuery] = useState('');
  const [rawSelectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Hidden experimental pages must not be ⌘K-discoverable either — same
  // gate the sidebar and PageRouter apply.
  const [showExperimental] = useToggleFlag(
    SHOW_EXPERIMENTAL_STORAGE_KEY,
    'experimental-flag-changed',
  );
  const visibleDefs = useMemo(
    () => (showExperimental ? PAGE_DEFS : PAGE_DEFS.filter((d) => !EXPERIMENTAL_PAGES.has(d.id))),
    [showExperimental],
  );

  // Snapshot recents once per palette open (content remounts each time)
  const recentDefs = useMemo(() => {
    const defs: PageDef[] = [];
    for (const id of loadRecentPageIds()) {
      const def = visibleDefs.find((d) => d.id === id);
      if (def && !defs.includes(def)) defs.push(def);
      if (defs.length >= MAX_RECENT_SHOWN) break;
    }
    return defs;
  }, [visibleDefs]);

  const filtered = useMemo(() => {
    if (!query.trim()) return visibleDefs;
    return visibleDefs.filter((def) => fuzzyMatch(query.trim(), def));
  }, [query, visibleDefs]);

  const showRecent = !query.trim() && recentDefs.length > 0;

  // Flat list driving keyboard navigation: Recent section first, then the full list
  const results = useMemo(
    () => (showRecent ? [...recentDefs, ...filtered] : filtered),
    [showRecent, recentDefs, filtered],
  );

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
      recordRecentPage(pageId);
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

  // Shared row renderer so Recent + All Pages stay one flat keyboard list
  const renderRow = (def: PageDef, index: number, keyPrefix: string) => {
    const isSelected = index === selectedIndex;
    return (
      <button
        key={`${keyPrefix}-${def.id}`}
        data-palette-item
        onClick={() => handleSelect(def.id)}
        onMouseEnter={() => setSelectedIndex(index)}
        className={`relative w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors ${
          isSelected ? 'text-[var(--text-primary)]' : 'text-[var(--text-secondary)]'
        }`}
      >
        {isSelected && (
          <motion.div
            layoutId="palette-selection"
            aria-hidden
            className="absolute inset-0 rounded bg-[var(--bg-hover)]"
            transition={
              reducedMotion
                ? { duration: 0 }
                : { type: 'spring', stiffness: 500, damping: 38 }
            }
          />
        )}
        <span className="relative w-6 text-center text-base shrink-0" aria-hidden>
          {SECTION_ICONS[def.section] || '\u2022'}
        </span>
        <span className="relative flex-1 min-w-0">
          <span
            className={`text-sm font-medium ${isSelected ? 'text-[var(--text-primary)]' : ''}`}
          >
            {def.label}
          </span>
          <span className="ml-2 text-xs text-[var(--text-tertiary)]">{def.section}</span>
        </span>
        {isSelected && (
          <kbd className="relative hidden sm:inline-flex items-center px-1.5 py-0.5 text-[10px] font-mono text-[var(--text-tertiary)] border border-[var(--border-default)] rounded">
            Enter
          </kbd>
        )}
      </button>
    );
  };

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

        {/* Results list: layoutScroll keeps the layoutId highlight accurate while scrolling */}
        <motion.div ref={listRef} layoutScroll className="max-h-[400px] overflow-y-auto py-2">
          {results.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-[var(--text-tertiary)]">
              No pages matching &ldquo;{query}&rdquo;
            </div>
          ) : (
            <>
              {showRecent && (
                <>
                  <div className="px-4 py-1 text-[10px] font-medium uppercase tracking-wider text-[var(--text-tertiary)]">
                    Recent
                  </div>
                  {recentDefs.map((def, index) => renderRow(def, index, 'recent'))}
                </>
              )}
              <div className="px-4 py-1 text-[10px] font-medium uppercase tracking-wider text-[var(--text-tertiary)]">
                {heading}
              </div>
              {filtered.map((def, index) =>
                renderRow(def, index + (showRecent ? recentDefs.length : 0), 'all'),
              )}
            </>
          )}
        </motion.div>

        {/* Footer hint */}
        <div className="flex items-center gap-4 px-4 py-2 border-t border-[var(--border-default)] text-[10px] text-[var(--text-tertiary)]">
          <span className="flex items-center gap-1">
            <kbd className="px-1 py-0.5 border border-[var(--border-default)] rounded font-mono">
              {IS_MAC ? '⌘K' : 'Ctrl K'}
            </kbd>
            search
          </span>
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
