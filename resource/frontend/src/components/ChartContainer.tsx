import type { ReactNode } from 'react';
import { useState, useCallback, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'framer-motion';

interface ChartContainerProps {
  id?: string;
  title: string;
  titleContent?: ReactNode;
  headerExtra?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function ChartContainer({
  id,
  title,
  titleContent,
  headerExtra,
  children,
  className,
}: ChartContainerProps) {
  const [isMaximized, setIsMaximized] = useState(false);

  const open = useCallback(() => setIsMaximized(true), []);
  const close = useCallback(() => setIsMaximized(false), []);

  // ESC key to close
  useEffect(() => {
    if (!isMaximized) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isMaximized, close]);

  // Lock body scroll when maximized
  useEffect(() => {
    if (isMaximized) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => { document.body.style.overflow = ''; };
  }, [isMaximized]);

  return (
    <motion.div
      className={`chart-container ${className ?? ''}`}
      id={id}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="chart-header">
        <div className="flex items-center justify-between w-full">
          <div className="flex items-center gap-2 min-w-0 flex-1">
            {titleContent ?? <h4>{title}</h4>}
            {headerExtra}
          </div>
          <button
            onClick={(e) => { e.stopPropagation(); open(); }}
            className="p-1 rounded-md text-[var(--text-muted)] hover:text-[var(--neon-cyan)] hover:bg-[var(--bg-glass)] transition-all duration-150 flex-shrink-0 ml-2"
            aria-label={`Maximize ${title}`}
            title="Maximize"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5v-4m0 4h-4m4 0l-5-5" />
            </svg>
          </button>
        </div>
      </div>

      {children}

      {createPortal(
        <AnimatePresence>
          {isMaximized && (
            <motion.div
              className="fixed inset-0 z-[60] flex flex-col"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
            >
              {/* Backdrop */}
              <div
                className="absolute inset-0 bg-black/70 backdrop-blur-sm"
                onClick={close}
              />

              {/* Content panel */}
              <motion.div
                className="relative z-10 flex flex-col m-4 sm:m-6 lg:m-8 flex-1 min-h-0 rounded-xl border border-[var(--border-glass)] bg-[var(--bg-elevated)] shadow-2xl overflow-hidden"
                initial={{ scale: 0.95, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0.95, opacity: 0 }}
                transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
              >
                {/* Overlay header */}
                <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border-glass)]">
                  <h3 className="text-lg font-semibold text-[var(--text-primary)]">{title}</h3>
                  <button
                    onClick={close}
                    className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-[var(--neon-cyan)] hover:bg-[var(--bg-glass)] transition-all duration-150"
                    aria-label="Close maximized view"
                  >
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </div>

                {/* Body — children expand to fill */}
                <div className="card-maximize-body flex-1 overflow-auto p-4">
                  {children}
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>,
        document.body
      )}
    </motion.div>
  );
}
