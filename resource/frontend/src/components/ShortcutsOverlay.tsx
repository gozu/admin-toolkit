import { useCallback, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';

interface ShortcutsOverlayProps {
  isOpen: boolean;
  onClose: () => void;
}

const IS_MAC =
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad|iPod/.test(navigator.platform);

interface ShortcutRow {
  keys: string[];
  label: string;
}

// Mirrors the real bindings in hooks/useKeyboardNavigation.ts + CommandPalette.
const SHORTCUTS: ShortcutRow[] = [
  { keys: [IS_MAC ? '⌘K' : 'Ctrl K', '/'], label: 'Open command palette' },
  { keys: ['1–6'], label: 'Jump to page' },
  { keys: ['[', ']'], label: 'Previous / next page' },
  { keys: ['↑', '↓'], label: 'Navigate results (palette)' },
  { keys: ['Enter'], label: 'Select (palette)' },
  { keys: ['Esc'], label: 'Close dialogs' },
  { keys: ['?'], label: 'This overlay' },
];

const kbdClass =
  'inline-flex items-center px-1.5 py-0.5 text-[10px] font-mono text-[var(--text-tertiary)] border border-[var(--border-default)] rounded';

/** Inner content rendered only while open, so the Esc listener self-cleans. */
function ShortcutsOverlayContent({ onClose }: { onClose: () => void }) {
  const reduced = useReducedMotion();
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // Modal focus discipline (mirrors Modal.tsx): inert the app behind the
  // dialog, focus the dialog, and hand focus back to the trigger on close.
  // Mount-time effect is correct — the content only renders while open.
  useEffect(() => {
    const prev = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const root = document.getElementById('root');
    root?.setAttribute('inert', '');
    dialogRef.current?.focus();
    return () => {
      root?.removeAttribute('inert');
      prev?.focus();
    };
  }, []);

  const handleBackdropClick = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === e.currentTarget) {
        onClose();
      }
    },
    [onClose],
  );

  return createPortal(
    <motion.div
      ref={dialogRef}
      tabIndex={-1}
      className="fixed inset-0 z-[70] flex items-center justify-center outline-none"
      style={{ backgroundColor: 'rgba(0, 0, 0, 0.5)' }}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: reduced ? 0 : 0.15 }}
      onClick={handleBackdropClick}
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
    >
      <motion.div
        className="w-full max-w-sm mx-4 rounded-xl border border-[var(--border-default)] bg-[var(--bg-elevated)] shadow-2xl overflow-hidden"
        initial={reduced ? { opacity: 0 } : { opacity: 0, scale: 0.95, y: 8 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={
          reduced
            ? { opacity: 0 }
            : { opacity: 0, scale: 0.97, y: 4, transition: { duration: 0.1 } }
        }
        transition={reduced ? { duration: 0 } : { type: 'spring', stiffness: 380, damping: 28 }}
      >
        <div className="px-4 py-3 border-b border-[var(--border-default)] flex items-center justify-between">
          <span className="text-sm font-medium text-[var(--text-primary)]">
            Keyboard shortcuts
          </span>
          <kbd className={kbdClass}>Esc</kbd>
        </div>
        <div className="px-4 py-3 space-y-2">
          {SHORTCUTS.map((row) => (
            <div key={row.label} className="flex items-center justify-between gap-4">
              <span className="text-xs text-[var(--text-secondary)]">{row.label}</span>
              <span className="flex items-center gap-1 shrink-0">
                {row.keys.map((key) => (
                  <kbd key={key} className={kbdClass}>
                    {key}
                  </kbd>
                ))}
              </span>
            </div>
          ))}
        </div>
      </motion.div>
    </motion.div>,
    document.body,
  );
}

export function ShortcutsOverlay({ isOpen, onClose }: ShortcutsOverlayProps) {
  return (
    <AnimatePresence>{isOpen && <ShortcutsOverlayContent onClose={onClose} />}</AnimatePresence>
  );
}
