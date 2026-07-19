// Bottom-right feedback chips. One hub, mounted once in App — every surface
// reports outcomes through pushToast instead of inventing inline banners.
import { AnimatePresence, motion } from 'framer-motion';
import { toastStore, dismissToast, type Toast } from '../../state/toastStore';

const ACCENT: Record<Toast['kind'], string> = {
  success: 'var(--success)',
  error: 'var(--neon-red)',
  info: 'var(--accent)',
};

const GLYPH: Record<Toast['kind'], string> = {
  success: '✓',
  error: '!',
  info: 'i',
};

export function ToastHub() {
  const { toasts } = toastStore.use();
  return (
    <div className="fixed bottom-4 right-4 z-[90] flex flex-col items-end gap-2 pointer-events-none">
      <AnimatePresence initial={false}>
        {toasts.map((toast) => (
          <motion.div
            key={toast.id}
            layout
            initial={{ opacity: 0, y: 14, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, x: 24, transition: { duration: 0.16 } }}
            transition={{ type: 'spring', stiffness: 480, damping: 34 }}
            className="toast-chip pointer-events-auto cursor-pointer select-none"
            style={
              {
                '--toast-accent': ACCENT[toast.kind],
                '--toast-duration': `${toast.duration}ms`,
              } as React.CSSProperties
            }
            onClick={() => dismissToast(toast.id)}
            role="status"
          >
            <div className="flex items-start gap-2.5 pl-1.5">
              <span
                className="mt-0.5 flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full text-[10px] font-bold"
                style={{
                  color: ACCENT[toast.kind],
                  background: `color-mix(in oklab, ${ACCENT[toast.kind]} 16%, transparent)`,
                }}
              >
                {GLYPH[toast.kind]}
              </span>
              <div className="min-w-0">
                <div className="text-[13px] font-medium text-[var(--text-primary)] leading-tight">
                  {toast.title}
                </div>
                {toast.detail && (
                  <div className="mt-0.5 text-[11px] text-[var(--text-tertiary)] leading-snug line-clamp-3">
                    {toast.detail}
                  </div>
                )}
              </div>
            </div>
            <span className="toast-burn" />
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
