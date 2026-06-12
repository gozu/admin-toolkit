import { lazy, Suspense, useEffect, useRef } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { useDiag } from '../../context/DiagContext';
import type { PageId } from '../../types';
import {
  SHOW_DEPRECATED_STORAGE_KEY,
  SHOW_EXPERIMENTAL_STORAGE_KEY,
} from '../pages/SettingsPage';
import { useToggleFlag } from '../../hooks/useToggleFlag';
import { DEPRECATED_PAGES, EXPERIMENTAL_PAGES } from '../../utils/moduleRegistry';

function HiddenFeatureNotice({ kind }: { kind: 'experimental' | 'deprecated' }) {
  const title =
    kind === 'experimental'
      ? 'This feature is experimental'
      : 'This feature is deprecated';
  const label = kind === 'experimental' ? 'experimental' : 'deprecated';
  return (
    <div className="flex-1 flex items-center justify-center py-20">
      <div className="glass-card max-w-md p-6 text-center space-y-2">
        <h3 className="text-lg font-semibold text-[var(--text-primary)]">{title}</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Enable {label} features in <strong>Tools &gt; Settings</strong> to access this page.
        </p>
      </div>
    </div>
  );
}

// Eagerly import lightweight page components to avoid Suspense/AnimatePresence conflicts
import { MissionControlPage } from '../pages/MissionControlPage';
import { SummaryPage } from '../pages/SummaryPage';
import { FilesystemPage } from '../pages/FilesystemPage';
import { MemoryPage } from '../pages/MemoryPage';
import { CpuUsagePage } from '../pages/CpuUsagePage';
import { ProjectsPage } from '../pages/ProjectsPage';
import { ProjectComputePage } from '../pages/ProjectComputePage';
import { UsersPage } from '../pages/UsersPage';
import { CodeEnvsInsightsPage } from '../pages/CodeEnvsPage';
import { CodeEnvsComparisonPage } from '../pages/CodeEnvsComparisonPage';
import { ConnectionsInventoryPage } from '../pages/ConnectionsInventoryPage';
import { ConnectionsInsightsPage } from '../pages/ConnectionsInsightsPage';
import { ConnectionsHealthPage } from '../pages/ConnectionsHealthPage';
import { ConnectionsFsMigrationPage } from '../pages/ConnectionsFsMigrationPage';
import { LogsPage } from '../pages/LogsPage';
import { SanityCheckPage } from '../pages/SanityCheckPage';
import { SettingsPage } from '../pages/SettingsPage';
import { InstalledPluginsPage } from '../pages/InstalledPluginsPage';
import { FeedbackPage } from '../pages/FeedbackPage';

// Lazy-load only the heavy views
const ToolsContainer = lazy(() =>
  import('../ToolsContainer').then((m) => ({ default: m.ToolsContainer })),
);
const ReportPage = lazy(() =>
  import('../pages/ReportPage').then((m) => ({ default: m.ReportPage })),
);
const DbHealthPage = lazy(() =>
  import('../pages/DbHealthPage').then((m) => ({ default: m.DbHealthPage })),
);
const ImageCleanerLazy = lazy(() =>
  import('../ImageCleaner').then((m) => ({ default: m.ImageCleaner })),
);
const ContainerExecsLazy = lazy(() =>
  import('../ContainerExecs').then((m) => ({ default: m.ContainerExecs })),
);
const CSTemplateReplacementLazy = lazy(() =>
  import('../CSTemplateReplacement').then((m) => ({ default: m.CSTemplateReplacement })),
);
const LlmAuditPage = lazy(() =>
  import('../pages/LlmAuditPage').then((m) => ({ default: m.LlmAuditPage })),
);
const K8sInsightsLazy = lazy(() =>
  import('../K8sInsights').then((m) => ({ default: m.K8sInsights })),
);

function LoadingSpinner() {
  return (
    <div className="flex-1 flex items-center justify-center py-20">
      <div className="w-6 h-6 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
    </div>
  );
}

// Game-menu page swap: incoming page glides up out of a slight blur; outgoing
// page drops away fast so navigation never feels gated on the animation.
const crossfadeVariants = {
  initial: { opacity: 0, y: 10, scale: 0.995, filter: 'blur(5px)' },
  animate: { opacity: 1, y: 0, scale: 1, filter: 'blur(0px)' },
  exit: {
    opacity: 0,
    y: -6,
    filter: 'blur(4px)',
    transition: { duration: 0.07, ease: 'easeIn' as const },
  },
};

const crossfadeTransition = {
  duration: 0.22,
  ease: [0.16, 1, 0.3, 1] as [number, number, number, number],
};

function renderPage(activePage: PageId): React.ReactNode {
  switch (activePage) {
    case 'mission-control':
      return <MissionControlPage />;
    case 'summary':
      return <SummaryPage />;
    case 'filesystem':
      return <FilesystemPage />;
    case 'memory':
      return <MemoryPage />;
    case 'cpu':
      return <CpuUsagePage />;
    case 'projects':
      return <ProjectsPage />;
    case 'project-compute':
      return <ProjectComputePage />;
    case 'users':
      return <UsersPage />;
    case 'code-envs':
      return <CodeEnvsInsightsPage />;
    case 'code-envs-cleaner':
      return <CodeEnvsInsightsPage readOnly />;
    case 'code-envs-comparison':
      return <CodeEnvsComparisonPage />;
    case 'connections-inventory':
      return <ConnectionsInventoryPage />;
    case 'connections-insights':
      return <ConnectionsInsightsPage />;
    case 'connections-health':
      return <ConnectionsHealthPage />;
    case 'connections-fs-migration':
      return <ConnectionsFsMigrationPage />;
    case 'logs':
      return <LogsPage />;
    case 'sanity-check':
      return <SanityCheckPage />;
    case 'image-cleaner':
      return <ImageCleanerLazy />;
    case 'container-execs':
      return <ContainerExecsLazy />;
    case 'cs-template-replacement':
      return <CSTemplateReplacementLazy />;
    case 'plugins-installed':
      return <InstalledPluginsPage />;
    case 'project-cleaner':
    case 'plugins':
      return <ToolsContainer />;
    case 'report':
      return <ReportPage />;
    case 'db-health':
      return <DbHealthPage />;
    case 'llm-audit':
      return <LlmAuditPage />;
    case 'k8s-insights':
      return <K8sInsightsLazy />;
    case 'settings':
      return <SettingsPage />;
    case 'feedback':
      return <FeedbackPage />;
    default:
      return <SummaryPage />;
  }
}

export function PageRouter() {
  const { state, addDebugLog } = useDiag();
  const { activePage } = state;
  const prevPageRef = useRef(activePage);

  const [showExperimental] = useToggleFlag(
    SHOW_EXPERIMENTAL_STORAGE_KEY,
    'experimental-flag-changed',
  );
  const [showDeprecated] = useToggleFlag(
    SHOW_DEPRECATED_STORAGE_KEY,
    'deprecated-flag-changed',
  );

  useEffect(() => {
    if (prevPageRef.current !== activePage) {
      addDebugLog(`Page rendered: ${activePage} (prev: ${prevPageRef.current})`, 'navigation');
      prevPageRef.current = activePage;
    }
  }, [activePage, addDebugLog]);

  const hiddenKind: 'experimental' | 'deprecated' | null =
    !showExperimental && EXPERIMENTAL_PAGES.has(activePage)
      ? 'experimental'
      : !showDeprecated && DEPRECATED_PAGES.has(activePage)
        ? 'deprecated'
        : null;

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={activePage}
        variants={crossfadeVariants}
        initial="initial"
        animate="animate"
        exit="exit"
        transition={crossfadeTransition}
        className="flex-1 flex flex-col"
        onAnimationStart={(definition) => {
          // AppShell restores the page's saved scroll offset on this signal:
          // content is mounted and laid out, entrance not yet visible.
          if (definition === 'animate') {
            window.dispatchEvent(new CustomEvent('admin-toolkit:page-entered'));
          }
        }}
      >
        <Suspense fallback={<LoadingSpinner />}>
          {hiddenKind ? <HiddenFeatureNotice kind={hiddenKind} /> : renderPage(activePage)}
        </Suspense>
      </motion.div>
    </AnimatePresence>
  );
}
