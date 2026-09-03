import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { useDiag } from './context/DiagContext';
import { DiagProvider } from './context/DiagProvider';
import { bumpSessionEpoch } from './state/sessionCache';
import { fetchRaw } from './utils/api';
import { HostGate } from './components/HostGate';
import {
  Header,
  PacmanLoader,
  DebugPanel,
} from './components';
import { ErrorBoundary } from './components/ErrorBoundary';
import { AppGate } from './components/AppGate';
import { useApiDataLoader, useDataSource } from './hooks';
import { useScanStoreLoadingMirror } from './hooks/useScanStoreLoadingMirror';
import { useDelayedPageWarmup } from './hooks/useDelayedPageWarmup';
// Side-effect imports register module scan stores into scanStoreRegistry at app startup
// so useScanStoreLoadingMirror can subscribe before any page renders.
import './state/containerExecsStore';
import './state/computePlacementStore';
import './state/codeEnvBrokenStore';
import './state/codeEnvComparisonStore';
import './state/csTemplateStore';
import './state/imageCleanerStore';
import './state/managedFoldersStore';
import './state/projectCostScan';
import './state/reportLlmsStore';
import { AppShell } from './components/layout/AppShell';
import { PageRouter } from './components/layout/PageRouter';
import { CommandPalette } from './components/CommandPalette';
import { ShortcutsOverlay } from './components/ShortcutsOverlay';
import { UnlockModal } from './components/UnlockModal';
import { StaleBackendGate } from './components/StaleBackendGate';
import { hydrateHostKeyStatus, useHostKeyState } from './state/hostKeyUnlockStore';
import { useKeyboardNavigation } from './hooks/useKeyboardNavigation';
import { FxLayer } from './fx/FxLayer';
import { ToastHub } from './components/common/ToastHub';

// Lazy load comparison components
const ComparisonUpload = lazy(() => import('./components/comparison/ComparisonUpload').then(m => ({ default: m.ComparisonUpload })));
const ComparisonResultsView = lazy(() => import('./components/comparison/ComparisonResultsView').then(m => ({ default: m.ComparisonResultsView })));

const pageVariants = {
  initial: { opacity: 0, y: 20, scale: 0.98 },
  animate: { opacity: 1, y: 0, scale: 1 },
  exit: { opacity: 0, y: -20, scale: 0.98 },
};

const pageTransition = {
  duration: 0.3,
  ease: [0.4, 0, 0.2, 1] as [number, number, number, number],
};

function AppContent() {
  const { state, setMode, setActivePage, resetComparison, dispatch } = useDiag();
  const { parsedData, isLoading, error, mode, comparison, dataSource } = state;
  useDataSource();
  const [reloadKey, setReloadKey] = useState(0);

  // Host gate: in live API mode, present a host picker before any /api/* calls
  // fire. Skipped automatically when only the local host is configured (single
  // option → no choice to make).
  const [hostChosen, setHostChosen] = useState(false);
  const liveMode = dataSource === 'api';
  useApiDataLoader(liveMode && hostChosen, reloadKey);
  useScanStoreLoadingMirror();
  const hasResults = Object.keys(parsedData).length > 0 && !isLoading;
  useDelayedPageWarmup(liveMode && hostChosen && hasResults, parsedData);

  useEffect(() => {
    const onMacroProjectMissing = () => {
      if (liveMode) setHostChosen(false);
    };
    window.addEventListener('admin-toolkit:macro-project-missing', onMacroProjectMissing);
    return () => window.removeEventListener('admin-toolkit:macro-project-missing', onMacroProjectMissing);
  }, [liveMode]);

  // Encrypted remote-host keys: reconcile with the cookie on boot. Modal
  // visibility is derived from the store (api.ts marks it locked on 409
  // remote-keys-locked, unlockAll marks it unlocked) plus one local bit:
  // "dismissed", scoped to a single locked episode so any re-lock (forget on
  // this device, expired cookie) re-arms the prompt.
  const { configured: hostKeyConfigured, unlocked: hostKeyUnlocked } = useHostKeyState();
  const hostKeyLocked = liveMode && hostKeyConfigured && !hostKeyUnlocked;
  const [hostKeyDismissed, setHostKeyDismissed] = useState(false);
  const [prevHostKeyLocked, setPrevHostKeyLocked] = useState(hostKeyLocked);
  if (hostKeyLocked !== prevHostKeyLocked) {
    setPrevHostKeyLocked(hostKeyLocked);
    if (hostKeyLocked) setHostKeyDismissed(false);
  }
  const showHostKeyUnlock = hostKeyLocked && !hostKeyDismissed;
  useEffect(() => {
    if (liveMode) hydrateHostKeyStatus();
  }, [liveMode]);
  useEffect(() => {
    // A request 409'd while already locked and dismissed: re-open the modal.
    const onLocked = () => setHostKeyDismissed(false);
    window.addEventListener('admin-toolkit:remote-keys-locked', onLocked);
    return () => window.removeEventListener('admin-toolkit:remote-keys-locked', onLocked);
  }, []);
  const handleHostKeyUnlocked = useCallback(() => {
    bumpSessionEpoch();
    setReloadKey((k) => k + 1);
  }, []);

  const handleRefreshCache = useCallback(async () => {
    await fetchRaw('/api/cache/clear', { method: 'POST' });
    bumpSessionEpoch();
    setReloadKey((k) => k + 1);
  }, []);

  const handleBackToHosts = useCallback(() => {
    setHostChosen(false);
  }, []);

  // Command palette state
  const [paletteOpen, setPaletteOpen] = useState(false);

  // '?' shortcuts overlay state
  const [shortcutsOpen, setShortcutsOpen] = useState(false);

  // Keyboard navigation — suspended while a top layer (palette/overlay) owns
  // the keyboard, so '/' can't open the palette invisibly under the overlay.
  useKeyboardNavigation({
    onNavigate: setActivePage,
    onOpenPalette: () => setPaletteOpen(true),
    onOpenShortcuts: () => setShortcutsOpen(true),
    enabled: !paletteOpen && !shortcutsOpen,
  });

  // Header ⌘K hint button (AppShell) opens the palette via a window event.
  useEffect(() => {
    const onOpenPalette = () => setPaletteOpen(true);
    window.addEventListener('admin-toolkit:open-palette', onOpenPalette);
    return () => window.removeEventListener('admin-toolkit:open-palette', onOpenPalette);
  }, []);

  useEffect(() => {
    const onError = (event: ErrorEvent) => {
      const message = event.message || 'Unknown runtime error';
      const where = event.filename ? ` @ ${event.filename}:${event.lineno}:${event.colno}` : '';
      dispatch({
        type: 'ADD_DEBUG_LOG',
        payload: {
          scope: 'frontend',
          level: 'error',
          message: `Unhandled error: ${message}${where}`,
        },
      });
    };

    const onUnhandledRejection = (event: PromiseRejectionEvent) => {
      const reason = event.reason instanceof Error ? event.reason.message : String(event.reason);
      dispatch({
        type: 'ADD_DEBUG_LOG',
        payload: {
          scope: 'frontend',
          level: 'error',
          message: `Unhandled promise rejection: ${reason}`,
        },
      });
    };

    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onUnhandledRejection);
    return () => {
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onUnhandledRejection);
    };
  }, [dispatch]);

  const hasComparisonResults = comparison.before && comparison.after && comparison.result;

  const handleBackFromComparison = useCallback(() => {
    resetComparison();
    setMode('single');
  }, [resetComparison, setMode]);

  const InlineLoadingFallback = (
    <main className="flex-1 flex items-center justify-center">
      <div className="flex flex-col items-center justify-center py-20">
        <PacmanLoader />
        <p className="text-lg text-[var(--text-primary)] mt-6">Loading…</p>
      </div>
    </main>
  );

  // Determine current view
  let viewKey: string;
  let viewContent: React.ReactNode;

  if (liveMode && !hostChosen) {
    viewKey = 'host-gate';
    viewContent = (
      <HostGate
        onEnter={() => {
          bumpSessionEpoch();
          setHostChosen(true);
          setReloadKey((k) => k + 1);
        }}
      />
    );
  } else if (mode === 'comparison' && hasComparisonResults) {
    viewKey = 'comparison-results';
    viewContent = (
      <Suspense fallback={InlineLoadingFallback}>
        <ComparisonResultsView onBack={handleBackFromComparison} />
      </Suspense>
    );
  } else if (mode === 'comparison') {
    viewKey = 'comparison-upload';
    viewContent = (
      <div className="min-h-screen flex flex-col bg-[var(--bg-app)]">
        <Header showBackButton onBack={handleBackFromComparison} />
        <main className="flex-1 flex items-center justify-center">
          <Suspense fallback={null}>
            <ComparisonUpload />
          </Suspense>
        </main>
      </div>
    );
  } else if (!hasResults) {
    viewKey = 'boot';
    viewContent = (
      <div className="min-h-screen flex flex-col bg-[var(--bg-app)]">
        <Header />
        <main className="flex-1 flex items-center justify-center">
          {isLoading ? (
            <div className="flex flex-col items-center justify-center py-20">
              <PacmanLoader />
              <p className="text-lg text-[var(--text-primary)] mt-6">
                Loading live diagnostics...
              </p>
            </div>
          ) : null}
        </main>
        {error && (
          <div className="fixed bottom-4 left-1/2 transform -translate-x-1/2 p-4 card-alert-critical rounded-lg max-w-2xl mx-auto">
            {error}
          </div>
        )}
      </div>
    );
  } else {
    // Main results view — new sidebar-based layout
    viewKey = 'main';
    viewContent = (
      <AppShell onRefreshCache={handleRefreshCache} onBackToHosts={handleBackToHosts}>
        <PageRouter />
      </AppShell>
    );
  }

  return (
    <>
      <FxLayer />
      <ToastHub />
      <AnimatePresence mode="wait">
        <motion.div
          key={viewKey}
          variants={pageVariants}
          initial="initial"
          animate="animate"
          exit="exit"
          transition={pageTransition}
        >
          {viewContent}
        </motion.div>
      </AnimatePresence>

      {/* Command Palette — always mounted, shown on Cmd+K */}
      {hasResults && (
        <CommandPalette isOpen={paletteOpen} onClose={() => setPaletteOpen(false)} />
      )}

      {/* '?' keyboard shortcuts overlay */}
      <ShortcutsOverlay isOpen={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />

      {/* Unified password unlock — mounted app-wide so it can appear during the
          initial data load (before AppShell exists) when remote-host keys are
          locked. One password also unlocks the advanced action pages. */}
      <UnlockModal
        isOpen={showHostKeyUnlock}
        onClose={() => setHostKeyDismissed(true)}
        onUnlocked={handleHostKeyUnlocked}
      />

      {/* Plugin updated under a running webapp backend — blocks the whole app
          from the very first screen, since nothing it shows can be trusted. */}
      <StaleBackendGate />
    </>
  );
}

function App() {
  return (
    <DiagProvider>
      <AppGate>
        {() => (
          <ErrorBoundary>
            <AppContent />
          </ErrorBoundary>
        )}
      </AppGate>
      <DebugPanel />
    </DiagProvider>
  );
}

export default App;
