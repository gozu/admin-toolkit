import { useState, useEffect, useRef, useCallback, type ReactNode } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import type { PageId } from '../../types';
import { Sidebar } from './Sidebar';
import { Breadcrumb } from './Breadcrumb';
import { useTheme } from '../../hooks/useTheme';
import dkulogo from '../../assets/dkulogo.png';
import { exportAllTablesToZip } from '../../utils/exportTables';
import { buildDiagBundle, snapshotDiagState } from '../../utils/diagBundle';
import { storeExportInArchive } from '../../utils/archiveStore';
import { useDiag } from '../../context/DiagContext';
import { UnlockModal } from '../UnlockModal';
import { DatasetExportModal } from '../DatasetExportModal';
import { useRedState, toggleShowRed, hydrateRedStatus } from '../../state/redUnlockStore';
import { datasetExportConfigStore } from '../../state/datasetExportConfigStore';
import { feedbackFromPageStore } from '../../state/feedbackFromPage';
import { subscribeSessionEpoch } from '../../state/sessionCache';
import { unlockAdoption, useAdoptionVisible } from '../../state/adoptionUnlockStore';

const COLLAPSE_BREAKPOINT = 1280;
const SIDEBAR_COLLAPSED = 56;
const SCROLL_TOP_THRESHOLD = 600;

// Per-page scroll positions — module-level so they survive AppShell remounts
// within a session; cleared on session-epoch bumps (host switch / cache
// refresh), where restored offsets would point into stale content.
const pageScrollPositions = new Map<PageId, number>();
subscribeSessionEpoch(() => pageScrollPositions.clear());

interface AppShellProps {
  children: ReactNode;
  onRefreshCache?: () => Promise<void>;
  onBackToHosts?: () => void;
}

const toolbarButtonClass = 'flex items-center justify-center w-10 h-9 rounded-lg text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)] transition-colors';
const toolbarIconClass = 'w-6 h-6';

export function AppShell({ children, onRefreshCache, onBackToHosts }: AppShellProps) {
  const [collapsed, setCollapsed] = useState(
    () => typeof window !== 'undefined' && window.innerWidth < COLLAPSE_BREAKPOINT,
  );
  const [showAbout, setShowAbout] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const { theme, toggle: toggleTheme } = useTheme();
  const { state, setActivePage } = useDiag();
  const { parsedData } = state;
  const { authed, showRed } = useRedState();
  const [showUnlock, setShowUnlock] = useState(false);
  const [showDatasetExport, setShowDatasetExport] = useState(false);
  const [exporting, setExporting] = useState(false);
  const { configuredConnection, loaded: datasetExportLoaded } = datasetExportConfigStore.use();
  const datasetExportEnabled = datasetExportLoaded && !!configuredConnection;
  const reducedMotion = useReducedMotion();
  const adoptionVisible = useAdoptionVisible();
  const eggBufRef = useRef('');

  // Scroll-to-top: rAF-throttled scrollTop tracking on <main>
  const mainRef = useRef<HTMLElement | null>(null);
  const scrollRafRef = useRef<number | null>(null);
  const [showScrollTop, setShowScrollTop] = useState(false);

  const handleMainScroll = useCallback(() => {
    if (scrollRafRef.current !== null) return;
    scrollRafRef.current = requestAnimationFrame(() => {
      scrollRafRef.current = null;
      const el = mainRef.current;
      if (el) setShowScrollTop(el.scrollTop > SCROLL_TOP_THRESHOLD);
    });
  }, []);

  useEffect(
    () => () => {
      if (scrollRafRef.current !== null) cancelAnimationFrame(scrollRafRef.current);
    },
    [],
  );

  // Per-page scroll restoration: save the outgoing page's position on the
  // navigation commit; restore exactly once when the incoming page begins its
  // enter animation (PageRouter dispatches 'admin-toolkit:page-entered' from
  // onAnimationStart — content is mounted and laid out, but the entrance
  // hasn't visibly played, so there is no two-stage jump and no delayed timer
  // to fight the user's own scrolling).
  const { activePage } = state;
  const prevPageRef = useRef<PageId>(activePage);

  useEffect(() => {
    const prev = prevPageRef.current;
    if (prev === activePage) return;
    if (mainRef.current) pageScrollPositions.set(prev, mainRef.current.scrollTop);
    prevPageRef.current = activePage;
  }, [activePage]);

  useEffect(() => {
    const onPageEntered = () => {
      const el = mainRef.current;
      if (el) el.scrollTop = pageScrollPositions.get(activePage) ?? 0;
    };
    window.addEventListener('admin-toolkit:page-entered', onPageEntered);
    return () => window.removeEventListener('admin-toolkit:page-entered', onPageEntered);
  }, [activePage]);

  // On-demand Users deep-dive: type the keyword outside any input to opt in.
  useEffect(() => {
    if (adoptionVisible) return;
    const handler = (e: KeyboardEvent) => {
      const el = document.activeElement as HTMLElement | null;
      const tag = el?.tagName.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || el?.isContentEditable) return;
      if (e.metaKey || e.ctrlKey || e.altKey || e.key.length !== 1) return;
      eggBufRef.current = (eggBufRef.current + e.key.toLowerCase()).slice(-8);
      if (eggBufRef.current.endsWith('adoption')) {
        unlockAdoption();
        setActivePage('adoption');
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [adoptionVisible, setActivePage]);

  // Reconcile the unlock UI with the HttpOnly cookie once on boot.
  useEffect(() => {
    hydrateRedStatus();
  }, []);

  // Resolve whether "Save Tables as Datasets" is enabled (admin-picked connection).
  useEffect(() => {
    datasetExportConfigStore.loadConfig();
  }, []);

  const handleRefresh = async () => {
    if (!onRefreshCache || refreshing) return;
    setRefreshing(true);
    try {
      await onRefreshCache();
    } finally {
      setRefreshing(false);
    }
  };

  // Top-bar export = the full diagnostic bundle (client state + parsed data +
  // backend dumps). Backend fetches are best-effort, so this also works in
  // zip-import mode. A copy is stored in the server-side archive as before.
  const handleExportBundle = async () => {
    if (exporting) return;
    setExporting(true);
    try {
      const { blob, filename } = await buildDiagBundle({
        report: {
          type: 'export',
          message: 'Top-bar export (diagnostic bundle download).',
          email: '',
          diagnosticsText: [
            `Version: ${__APP_VERSION__}`,
            `Page: ${state.activePage}`,
            `Trigger: topbar-export`,
            `Time: ${new Date().toISOString()}`,
          ].join('\n'),
        },
        state: snapshotDiagState(state),
      });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      a.click();
      URL.revokeObjectURL(a.href);
      void storeExportInArchive(blob, filename);
    } finally {
      setExporting(false);
    }
  };

  // Listen for viewport changes to auto-collapse
  useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${COLLAPSE_BREAKPOINT - 1}px)`);

    const handleChange = (e: MediaQueryListEvent | MediaQueryList) => {
      setCollapsed(e.matches);
    };

    // Set initial value
    handleChange(mql);

    mql.addEventListener('change', handleChange as (e: MediaQueryListEvent) => void);
    return () =>
      mql.removeEventListener('change', handleChange as (e: MediaQueryListEvent) => void);
  }, []);

  return (
    <div
      className="h-screen overflow-hidden bg-[var(--bg-app)]"
      style={{
        display: 'grid',
        gridTemplateColumns: collapsed ? `${SIDEBAR_COLLAPSED}px 1fr` : 'auto 1fr',
        gridTemplateRows: 'auto 1fr',
      }}
    >
      {/* Sidebar — spans both rows */}
      <motion.div
        className="row-span-2 overflow-hidden"
        animate={{ width: collapsed ? SIDEBAR_COLLAPSED : 'auto' }}
        transition={{ type: 'spring', stiffness: 400, damping: 35 }}
        style={{ minWidth: collapsed ? SIDEBAR_COLLAPSED : undefined }}
      >
        {/* One-time entrance: the shell builds itself — sidebar glides in… */}
        <motion.div
          className="h-full"
          initial={{ x: -20, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
        >
          <Sidebar collapsed={collapsed} onToggleCollapse={() => setCollapsed((prev) => !prev)} onBackToHosts={onBackToHosts} />
        </motion.div>
      </motion.div>

      {/* Top bar — …and the header drops in just behind it */}
      <motion.header
        initial={{ y: -14, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.45, delay: 0.08, ease: [0.16, 1, 0.3, 1] }}
        className="app-topbar relative flex items-center justify-between px-5 py-1 border-b border-[var(--border-default)] bg-[var(--bg-surface)]">
        <div className="flex items-center gap-3 min-w-0">
          <Breadcrumb />

          {/* Advanced Actions — switch (Beta). Locked → opens the unlock modal;
              unlocked → toggles visibility of the red/agentic surfaces. */}
          <button
            type="button"
            role="switch"
            aria-checked={authed && showRed}
            onClick={() => (authed ? toggleShowRed() : setShowUnlock(true))}
            title={
              !authed
                ? 'Unlock advanced actions'
                : showRed
                  ? 'Hide advanced actions (stays unlocked on this browser)'
                  : 'Show advanced actions'
            }
            className="flex items-center gap-1.5 px-1.5 py-0.5 rounded-lg hover:bg-[var(--bg-hover)] transition-colors"
          >
            <span
              aria-hidden
              className={`relative inline-block h-3.5 w-[26px] rounded-full border transition-colors ${
                authed && showRed
                  ? 'bg-[var(--neon-red)]/80 border-[var(--neon-red)]'
                  : 'bg-[var(--bg-hover)] border-[var(--border-default)]'
              }`}
            >
              <span
                className={`absolute top-1/2 -translate-y-1/2 h-2.5 w-2.5 rounded-full transition-all ${
                  authed && showRed
                    ? 'left-[13px] bg-white'
                    : 'left-[2px] bg-[var(--text-tertiary)]'
                }`}
              />
            </span>
            {!authed && (
              <svg
                width="11"
                height="11"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
                className="text-[var(--text-tertiary)]"
              >
                <rect x="3" y="11" width="18" height="11" rx="2" />
                <path d="M7 11V7a5 5 0 0 1 10 0v4" />
              </svg>
            )}
            <span
              className={`text-[10px] font-mono font-medium ${
                authed && showRed ? 'text-[var(--neon-red)]' : 'text-[var(--text-tertiary)]'
              }`}
            >
              Advanced Actions
            </span>
            <span
              title="Advanced Actions are in Beta — features may change."
              className="inline-flex items-center h-3.5 px-1 text-[8px] leading-none font-mono font-semibold uppercase tracking-wide rounded border bg-[var(--accent)] text-white border-[var(--accent)] select-none"
            >
              Beta
            </span>
          </button>
        </div>

        <div className="flex items-center gap-2">
          {/* Search — opens the command palette (handled in App.tsx). Icon-only
              to stay compact; the ⌘K shortcut is revealed inside the palette. */}
          <button
            type="button"
            onClick={() => window.dispatchEvent(new CustomEvent('admin-toolkit:open-palette'))}
            title="Search"
            aria-label="Search"
            className={toolbarButtonClass}
          >
            <svg className={toolbarIconClass} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
              <circle cx="11" cy="11" r="7" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
          </button>

          {/* Feedback — always-visible cyan-outline button (EAP). Distinct from
              the icon-only toolbar actions; navigates to the Feedback page. */}
          <button
            type="button"
            onClick={() => {
              if (state.activePage !== 'feedback') feedbackFromPageStore.set(state.activePage);
              setActivePage('feedback');
            }}
            title="Send feedback (bug, idea, or anything else)"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm border border-[var(--neon-cyan)] text-[var(--neon-cyan)] hover:bg-[var(--neon-cyan)] hover:text-white transition-colors"
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
            <span className="hidden sm:inline">Feedback</span>
          </button>

          <button
            type="button"
            onClick={handleRefresh}
            disabled={!onRefreshCache || refreshing}
            title="Refresh cache"
            className={`${toolbarButtonClass} ${!onRefreshCache ? 'opacity-30 cursor-not-allowed' : ''}`}
          >
            <svg className={`${toolbarIconClass} ${refreshing ? 'animate-spin' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
              <polyline points="23 4 23 10 17 10" />
              <polyline points="1 20 1 14 7 14" />
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10" />
              <path d="M20.49 15a9 9 0 0 1-14.85 3.36L1 14" />
            </svg>
          </button>

          {/* Export = full diagnostic bundle (zip) */}
          <button
            type="button"
            onClick={() => void handleExportBundle()}
            disabled={!parsedData.dataReady || exporting}
            title="Download diagnostic bundle (zip — all data + state)"
            className={`${toolbarButtonClass} ${!parsedData.dataReady ? 'opacity-30 cursor-not-allowed' : ''}`}
          >
            <svg className={`${toolbarIconClass} ${exporting ? 'animate-pulse' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
              <line x1="16.5" y1="9.4" x2="7.5" y2="4.21" />
              <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
              <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
              <line x1="12" y1="22.08" x2="12" y2="12" />
            </svg>
          </button>

          {/* Export all tables as CSV zip */}
          <button
            type="button"
            onClick={exportAllTablesToZip}
            title="Export all tables to CSV (zip)"
            className={toolbarButtonClass}
          >
            <svg className={toolbarIconClass} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
              <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" />
              <polyline points="14 2 14 8 20 8" />
              <path d="M8 13h2" />
              <path d="M14 13h2" />
              <path d="M8 17h2" />
              <path d="M14 17h2" />
            </svg>
          </button>

          {/* Save all tables as Dataiku datasets (local-scoped; admin enables via plugin settings) */}
          <button
            type="button"
            onClick={() => setShowDatasetExport(true)}
            disabled={!datasetExportEnabled}
            title={
              datasetExportEnabled
                ? `Save all tables as Dataiku datasets (→ ${configuredConnection})`
                : 'Save tables as datasets — disabled until an admin selects a connection in the Admin Toolkit plugin settings'
            }
            className={`${toolbarButtonClass} ${!datasetExportEnabled ? 'opacity-30 cursor-not-allowed' : ''}`}
          >
            <svg className={toolbarIconClass} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
              <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
              <polyline points="17 21 17 13 7 13 7 21" />
              <polyline points="7 3 7 8 15 8" />
            </svg>
          </button>

          {/* Theme toggle */}
          <button
            type="button"
            onClick={(e) => toggleTheme(e)}
            title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            className={toolbarButtonClass}
          >
            {theme === 'dark' ? (
              <svg className={toolbarIconClass} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="5" />
                <line x1="12" y1="1" x2="12" y2="3" />
                <line x1="12" y1="21" x2="12" y2="23" />
                <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
                <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
                <line x1="1" y1="12" x2="3" y2="12" />
                <line x1="21" y1="12" x2="23" y2="12" />
                <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
                <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
              </svg>
            ) : (
              <svg className={toolbarIconClass} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
              </svg>
            )}
          </button>

          {/* About popover */}
          <div className="relative">
            <button
              type="button"
              title="About"
              onClick={() => setShowAbout((p) => !p)}
              className={toolbarButtonClass}
            >
              <svg
                className={toolbarIconClass}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={1.5}
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <circle cx="12" cy="12" r="10" />
                <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
                <line x1="12" y1="17" x2="12.01" y2="17" />
              </svg>
            </button>
            {showAbout && (
              <div className="topbar-menu absolute right-0 top-full mt-2 w-52 rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)] shadow-lg p-3 z-50">
                <div className="text-xs font-mono text-[var(--text-secondary)] mb-1">
                  v{__APP_VERSION__}
                </div>
                <div className="flex items-center gap-2 text-[var(--text-tertiary)]">
                  <span className="text-[11px]">by Alex Kaos</span>
                </div>
              </div>
            )}
          </div>
        </div>
      </motion.header>

      {/* Center branding — dead center of the whole page. Overlays the header
          row across BOTH grid columns (sidebar included) so 50% here is 50% of
          the viewport, not of the header cell. Click-through everywhere except
          the wordmark button itself. */}
      <motion.div
        initial={{ y: -14, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.45, delay: 0.08, ease: [0.16, 1, 0.3, 1] }}
        className="relative pointer-events-none hidden lg:flex items-center justify-center"
        style={{ gridColumn: '1 / -1', gridRow: '1' }}
      >
        <button
          type="button"
          onClick={onBackToHosts}
          title="Back to host picker"
          className="pointer-events-auto flex items-center gap-2 rounded-lg px-2 py-1 hover:bg-[var(--bg-hover)] transition-colors"
        >
          <img
            src={dkulogo}
            alt="Dataiku"
            className="h-5 w-5"
          />
          <span
            className="text-base font-bold text-[var(--text-primary)] tracking-tight"          >
            ADMIN
          </span>
          <span
            className="text-base font-bold text-[#2AB1AC] tracking-tight -ml-1.5"          >
            TOOLKIT
          </span>
        </button>
      </motion.div>

      {/* Main content area — scrollable */}
      <main
        ref={mainRef}
        onScroll={handleMainScroll}
        className="overflow-y-auto bg-[var(--bg-app)] flex flex-col relative"
      >
        {children}
      </main>

      {/* Scroll-to-top — appears once <main> is scrolled past the threshold */}
      <AnimatePresence>
        {showScrollTop && (
          <motion.button
            type="button"
            title="Back to top"
            aria-label="Scroll back to top"
            onClick={() =>
              mainRef.current?.scrollTo({ top: 0, behavior: reducedMotion ? 'auto' : 'smooth' })
            }
            className="fixed bottom-5 right-5 z-30 flex items-center justify-center w-9 h-9 rounded-full bg-[var(--bg-elevated)] border border-[var(--border-default)] shadow-md text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
            initial={reducedMotion ? { opacity: 0 } : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reducedMotion ? { opacity: 0 } : { opacity: 0, y: 8 }}
            transition={{ duration: reducedMotion ? 0 : 0.18, ease: [0.16, 1, 0.3, 1] }}
          >
            <svg
              className="w-4 h-4"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polyline points="18 15 12 9 6 15" />
            </svg>
          </motion.button>
        )}
      </AnimatePresence>

      <UnlockModal isOpen={showUnlock} onClose={() => setShowUnlock(false)} />
      <DatasetExportModal
        isOpen={showDatasetExport}
        onClose={() => setShowDatasetExport(false)}
        connection={configuredConnection ?? ''}
      />
    </div>
  );
}
