import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Rnd } from 'react-rnd';
import { motion, AnimatePresence } from 'framer-motion';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  sizePreset?: 'default' | 'large' | 'full';
}

const MIN_WIDTH = 400;
const MIN_HEIGHT = 300;

export function Modal({ isOpen, onClose, title, children, footer, sizePreset = 'default' }: ModalProps) {
  const modalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  useEffect(() => {
    if (!isOpen) return;
    const root = document.getElementById('root');
    if (root) root.setAttribute('inert', '');
    modalRef.current?.focus();
    return () => { root?.removeAttribute('inert'); };
  }, [isOpen]);

  if (!isOpen) return null;

  const initialWidth = sizePreset === 'full'
    ? window.innerWidth * 0.95
    : sizePreset === 'large'
      ? Math.min(1200, window.innerWidth * 0.9)
      : Math.min(896, window.innerWidth * 0.9);
  const initialHeight = sizePreset === 'full'
    ? window.innerHeight * 0.95
    : sizePreset === 'large'
      ? Math.min(window.innerHeight * 0.75, window.innerHeight * 0.95)
      : Math.min(600, window.innerHeight * 0.9);
  const initialX = (window.innerWidth - initialWidth) / 2;
  const initialY = (window.innerHeight - initialHeight) / 2;

  return createPortal(
    <AnimatePresence>
      <motion.div
        ref={modalRef}
        tabIndex={-1}
        className="fixed inset-0 z-50 modal-overlay outline-none"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.2 }}
        onClick={onClose}
      >
        <Rnd
          default={{
            x: initialX,
            y: initialY,
            width: initialWidth,
            height: initialHeight,
          }}
          minWidth={MIN_WIDTH}
          minHeight={MIN_HEIGHT}
          maxWidth={window.innerWidth * 0.95}
          maxHeight={window.innerHeight * 0.95}
          bounds="parent"
          dragHandleClassName="modal-drag-handle"
          className="modal-content flex flex-col overflow-hidden"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e: React.MouseEvent) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="modal-drag-handle flex items-center justify-between px-4 py-3 border-b border-[var(--border-glass)] cursor-move select-none">
            <h3 className="text-lg font-semibold text-[var(--text-primary)]">{title}</h3>
            <button
              onClick={onClose}
              className="p-1.5 -mr-1.5 rounded-lg text-[var(--text-muted)]
                         hover:text-[var(--neon-cyan)] hover:bg-[var(--bg-glass)]
                         transition-all duration-150"
              aria-label="Close modal"
            >
              <svg
                className="w-5 h-5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M6 18L18 6M6 6l12 12"
                />
              </svg>
            </button>
          </div>

          {/* Body */}
          <div className="flex-1 overflow-auto p-4">{children}</div>

          {/* Footer */}
          {footer && (
            <div className="px-4 py-3 border-t border-[var(--border-glass)]">{footer}</div>
          )}
        </Rnd>
      </motion.div>
    </AnimatePresence>,
    document.body
  );
}
