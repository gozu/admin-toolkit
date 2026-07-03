import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';

const EASE_OUT: [number, number, number, number] = [0.16, 1, 0.3, 1];
const GAP = 8;
const MARGIN = 12;

interface RichPopoverProps {
  /** Popover body — interactive content allowed (links, buttons). */
  content: ReactNode;
  /** The trigger. The wrapper span toggles the popover on click. */
  children: ReactNode;
  /** Max width of the popover panel (px). */
  width?: number;
  /** Extra classes on the inline trigger wrapper. */
  className?: string;
  ariaLabel?: string;
}

interface Position {
  left: number;
  top: number;
  origin: string;
}

// Interactive generalization of the Sidebar's RailTooltip idiom: portaled to
// <body> (no overflow clipping), fixed-positioned off the trigger rect,
// framer-motion in/out. Unlike RailTooltip it is click-toggled, accepts rich
// interactive content, and closes on outside pointerdown / Escape / scroll.
export function RichPopover({ content, children, width = 320, className, ariaLabel }: RichPopoverProps) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<Position | null>(null);
  const triggerRef = useRef<HTMLSpanElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();

  const place = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const panelH = panelRef.current?.offsetHeight ?? 160;
    const below = rect.bottom + GAP + panelH <= window.innerHeight - MARGIN;
    const top = below ? rect.bottom + GAP : Math.max(MARGIN, rect.top - GAP - panelH);
    const left = Math.min(
      Math.max(MARGIN, rect.left + rect.width / 2 - width / 2),
      window.innerWidth - width - MARGIN,
    );
    setPos({ left, top, origin: below ? 'top center' : 'bottom center' });
  }, [width]);

  // Position before paint on open (and re-clamp once the panel has a height).
  useLayoutEffect(() => {
    if (open) place();
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    const onScroll = () => setOpen(false);
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKey);
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', place);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', place);
    };
  }, [open, place]);

  return (
    <>
      <span
        ref={triggerRef}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        aria-label={ariaLabel}
        className={`inline-flex cursor-pointer ${className || ''}`}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            e.stopPropagation();
            setOpen((v) => !v);
          }
        }}
      >
        {children}
      </span>
      {createPortal(
        <AnimatePresence>
          {open && pos && (
            <motion.div
              ref={panelRef}
              role="dialog"
              className="fixed z-50 rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] p-3 shadow-lg"
              style={{ left: pos.left, top: pos.top, width, transformOrigin: pos.origin }}
              initial={{ opacity: 0, scale: 0.97, y: -3 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.97, y: -3 }}
              transition={reduced ? { duration: 0 } : { duration: 0.16, ease: EASE_OUT }}
              onClick={(e) => e.stopPropagation()}
            >
              {content}
            </motion.div>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </>
  );
}
