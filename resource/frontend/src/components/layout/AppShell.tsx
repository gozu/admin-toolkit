import { useState, useEffect, type ReactNode } from 'react';
import { motion } from 'framer-motion';
import { Sidebar } from './Sidebar';
import { Breadcrumb } from './Breadcrumb';
import { useTheme } from '../../hooks/useTheme';
import dkulogo from '../../assets/dkulogo.png';
import { exportAllTablesToZip } from '../../utils/exportTables';
import { exportDataToZip } from '../../utils/exportData';
import { useDiag } from '../../context/DiagContext';
import { RedUnlockModal } from '../RedUnlockModal';
import { DatasetExportModal } from '../DatasetExportModal';
import { useRedState, toggleShowRed, hydrateRedStatus } from '../../state/redUnlockStore';
import { datasetExportConfigStore } from '../../state/datasetExportConfigStore';
import { feedbackFromPageStore } from '../../state/feedbackFromPage';

const COLLAPSE_BREAKPOINT = 1280;
const SIDEBAR_COLLAPSED = 56;

interface AppShellProps {
  children: ReactNode;
  onRefreshCache?: () => Promise<void>;
  onBackToHosts?: () => void;
}

const toolbarButtonClass = 'flex items-center justify-center w-10 h-9 rounded-lg text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)] transition-colors';
const toolbarIconClass = 'w-8 h-8';

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
  const { configuredConnection, loaded: datasetExportLoaded } = datasetExportConfigStore.use();
  const datasetExportEnabled = datasetExportLoaded && !!configuredConnection;

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
        <Sidebar collapsed={collapsed} onToggleCollapse={() => setCollapsed((prev) => !prev)} onBackToHosts={onBackToHosts} />
      </motion.div>

      {/* Top bar */}
      <header className="relative flex items-center justify-between px-5 py-1 border-b border-[var(--border-default)] bg-[var(--bg-surface)]">
        <Breadcrumb />

        {/* Center branding */}
        <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 hidden lg:flex items-center gap-2">
          <button
            type="button"
            onClick={onBackToHosts}
            title="Back to host picker"
            className="flex items-center gap-2 rounded-lg px-2 py-1 hover:bg-[var(--bg-hover)] transition-colors"
          >
            <img
              src={dkulogo}
              alt="Dataiku"
              className="h-5 w-5"
            />
            <span
              className="text-base font-bold text-[var(--text-primary)] tracking-tight"            >
              ADMIN
            </span>
            <span
              className="text-base font-bold text-[#2AB1AC] tracking-tight -ml-1.5"            >
              TOOLKIT
            </span>
          </button>
          <span
            title="This toolkit is an Early Access Preview — features may change."
            className="px-1.5 py-0.5 text-[10px] font-mono font-medium rounded border bg-[var(--neon-cyan)]/10 text-[var(--neon-cyan)] border-[var(--neon-cyan)]/30 cursor-default select-none"
          >
            Early Access Preview
          </span>
          <button
            type="button"
            onClick={() => (authed ? toggleShowRed() : setShowUnlock(true))}
            title={
              !authed
                ? 'Unlock advanced actions'
                : showRed
                  ? 'Hide advanced actions (stays unlocked on this browser)'
                  : 'Show advanced actions'
            }
            aria-pressed={authed && showRed}
            className={`flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-mono font-medium rounded border transition-colors ${
              authed && showRed
                ? 'bg-[var(--neon-red)]/20 text-[var(--neon-red)] border-[var(--neon-red)]/50 hover:bg-[var(--neon-red)]/30'
                : authed
                  ? 'text-[var(--neon-red)]/70 border-[var(--neon-red)]/30 hover:bg-[var(--neon-red)]/10'
                  : 'text-[var(--text-tertiary)] border-[var(--border-default)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)]'
            }`}
          >
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="11" width="18" height="11" rx="2" />
              {authed ? (
                <path d="M7 11V7a5 5 0 0 1 9.9-1" />
              ) : (
                <path d="M7 11V7a5 5 0 0 1 10 0v4" />
              )}
            </svg>
            Advanced Actions
          </button>
        </div>

        <div className="flex items-center gap-2">
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

          {/* Export all data as JSON zip */}
          <button
            type="button"
            onClick={() => exportDataToZip(parsedData)}
            disabled={!parsedData.dataReady}
            title="Export all data as JSON (zip)"
            className={`${toolbarButtonClass} ${!parsedData.dataReady ? 'opacity-30 cursor-not-allowed' : ''}`}
          >
            <svg className={toolbarIconClass} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 8v13H3V8" />
              <path d="M1 3h22v5H1z" />
              <path d="M10 12h4" />
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
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
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
              <ellipse cx="12" cy="5" rx="8" ry="3" />
              <path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5" />
              <path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6" />
            </svg>
          </button>

          {/* Theme toggle */}
          <button
            type="button"
            onClick={toggleTheme}
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
              <div className="absolute right-0 top-full mt-2 w-52 rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)] shadow-lg p-3 z-50">
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
      </header>

      {/* Main content area — scrollable */}
      <main className="overflow-y-auto bg-[var(--bg-app)] flex flex-col relative">
        {children}
      </main>

      <RedUnlockModal isOpen={showUnlock} onClose={() => setShowUnlock(false)} />
      <DatasetExportModal
        isOpen={showDatasetExport}
        onClose={() => setShowDatasetExport(false)}
        connection={configuredConnection ?? ''}
      />
    </div>
  );
}
