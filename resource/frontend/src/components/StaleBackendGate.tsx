import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { motion } from 'framer-motion';
import { useBackendFreshness } from '../state/appVersionStore';
import { webappSelfLink } from '../utils/webappSelfLink';

/**
 * Blocking notice for a webapp backend still running pre-upgrade code.
 *
 * Updating the plugin in DSS swaps the frontend (served from the installed
 * plugin) but leaves a running webapp backend on the Python it started with —
 * DSS never restarts webapp backends on plugin update. The result is a new UI
 * against old routes: endpoints 404, payload fields go missing, and the
 * failures look like unrelated bugs. Nothing in the app is trustworthy in that
 * state, so this gate is deliberately not dismissible: the only way forward is
 * to restart the backend and reload.
 */
export function StaleBackendGate() {
  const { checked, stale, installedVersion, runningVersion } = useBackendFreshness();
  const show = checked && stale;

  // Take the app behind the gate out of the tab order entirely — an overlay
  // that can still be tabbed through is only visually blocking.
  useEffect(() => {
    if (!show) return;
    const root = document.getElementById('root');
    root?.setAttribute('inert', '');
    return () => root?.removeAttribute('inert');
  }, [show]);

  if (!show) return null;

  const link = webappSelfLink();
  const running = runningVersion ? `v${runningVersion}` : 'an earlier release';

  return createPortal(
    <motion.div
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="stale-backend-title"
      className="fixed inset-0 z-[200] flex items-center justify-center p-6
                 bg-[rgba(0,0,0,0.82)] backdrop-blur-md"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
    >
      <motion.div
        className="w-full max-w-[46rem] rounded-2xl border border-[var(--neon-red-dim)]
                   bg-[var(--bg-surface)] shadow-2xl overflow-hidden"
        initial={{ opacity: 0, y: 16, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
      >
        <div className="h-1 w-full bg-[var(--neon-red)]" />

        <div className="p-8">
          <div className="flex items-center gap-2 text-[var(--neon-red)] text-xs font-semibold
                          tracking-[0.14em] uppercase">
            <span aria-hidden="true" className="text-base leading-none">⚠</span>
            Action required
          </div>

          <h1
            id="stale-backend-title"
            className="mt-3 text-3xl font-bold text-[var(--text-primary)] leading-tight"
          >
            Restart this webapp&rsquo;s backend
          </h1>

          <p className="mt-4 text-[var(--text-secondary)] leading-relaxed">
            Admin Toolkit was updated
            {installedVersion ? <> to <strong className="text-[var(--text-primary)]">v{installedVersion}</strong></> : null}
            , but this webapp&rsquo;s Python backend is still running{' '}
            <strong className="text-[var(--text-primary)]">{running}</strong>. DSS does not
            restart webapp backends when a plugin is updated, so the new interface is talking to
            the old backend code.
          </p>
          <p className="mt-3 text-[var(--text-secondary)] leading-relaxed">
            Until it is restarted, pages can fail, load stale results, or report the wrong
            version. Everything below this message is unreliable.
          </p>

          <ol className="mt-6 space-y-2.5 text-[var(--text-secondary)]">
            {[
              link?.kind === 'list'
                ? 'Open this project’s webapp list and select Admin Toolkit.'
                : 'Open the webapp’s settings page in DSS.',
              'In the ACTIONS menu, stop the backend, then start it again.',
              'Come back here and reload.',
            ].map((step, i) => (
              <li key={step} className="flex gap-3">
                <span
                  aria-hidden="true"
                  className="shrink-0 mt-0.5 w-5 h-5 rounded-full border border-[var(--border-glass)]
                             text-[11px] font-semibold text-[var(--text-tertiary)]
                             flex items-center justify-center"
                >
                  {i + 1}
                </span>
                <span>{step}</span>
              </li>
            ))}
          </ol>

          <div className="mt-8 flex flex-wrap items-center gap-3">
            {link && (
              <a
                href={link.href}
                target="_blank"
                rel="noreferrer"
                className="px-5 py-2.5 rounded-lg font-semibold text-[var(--text-inverse)]
                           bg-[var(--neon-red)] hover:brightness-110 transition-[filter] duration-150"
              >
                {link.kind === 'list' ? 'Open webapp list' : 'Open webapp settings'} ↗
              </a>
            )}
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="px-5 py-2.5 rounded-lg font-semibold text-[var(--text-primary)]
                         border border-[var(--border-glass)] bg-[var(--bg-glass)]
                         hover:bg-[var(--bg-hover)] transition-colors duration-150"
            >
              Reload
            </button>
            <span className="text-xs text-[var(--text-tertiary)] font-mono">
              running {runningVersion || '?'} · installed {installedVersion || '?'}
            </span>
          </div>

          {!link && (
            <p className="mt-4 text-sm text-[var(--text-tertiary)]">
              Find the webapp in DSS under its project &rsaquo; Webapps &rsaquo; Admin Toolkit.
            </p>
          )}
        </div>
      </motion.div>
    </motion.div>,
    document.body,
  );
}
