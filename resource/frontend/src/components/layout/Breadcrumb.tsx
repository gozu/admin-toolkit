import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { useEffect, useRef, useState } from 'react';
import { useDiag } from '../../context/DiagContext';
import type { Lifecycle, PageId } from '../../types';
import { resolveLifecycleById } from '../../utils/pageLifecycle';

interface SectionInfo {
  label: string;
  firstPage: PageId;
}

const PAGE_SECTION_MAP: Record<PageId, SectionInfo> = {
  'mission-control': { label: 'Overview', firstPage: 'summary' },
  summary: { label: 'Overview', firstPage: 'summary' },
  filesystem: { label: 'Overview', firstPage: 'summary' },
  memory: { label: 'Overview', firstPage: 'summary' },
  cpu: { label: 'Overview', firstPage: 'summary' },
  'connections-inventory': { label: 'Connections', firstPage: 'connections-inventory' },
  'connections-insights': { label: 'Connections', firstPage: 'connections-inventory' },
  'connections-health': { label: 'Connections', firstPage: 'connections-inventory' },
  'connections-fs-migration': { label: 'Connections', firstPage: 'connections-inventory' },
  projects: { label: 'Projects', firstPage: 'projects' },
  'project-cleaner': { label: 'Projects', firstPage: 'projects' },
  'project-compute': { label: 'Projects', firstPage: 'projects' },
  'project-cost': { label: 'Projects', firstPage: 'projects' },
  users: { label: 'Users', firstPage: 'users' },
  adoption: { label: 'Users', firstPage: 'users' },
  'plugins-installed': { label: 'Plugins', firstPage: 'plugins-installed' },
  plugins: { label: 'Plugins', firstPage: 'plugins-installed' },
  'code-envs': { label: 'Code Envs', firstPage: 'code-envs' },
  'code-envs-cleaner': { label: 'Code Envs', firstPage: 'code-envs' },
  'code-envs-comparison': { label: 'Code Envs', firstPage: 'code-envs' },
  'container-execs': { label: 'AI Compute', firstPage: 'container-execs' },
  'image-cleaner': { label: 'AI Compute', firstPage: 'container-execs' },
  'cs-template-replacement': { label: 'AI Compute', firstPage: 'container-execs' },
  'llm-audit': { label: 'AI Compute', firstPage: 'container-execs' },
  'k8s-insights': { label: 'AI Compute', firstPage: 'container-execs' },
  agents: { label: 'Agents', firstPage: 'agents' },
  'agent-tuning': { label: 'Agents', firstPage: 'agents' },
  settings: { label: 'Misc', firstPage: 'settings' },
  logs: { label: 'Misc', firstPage: 'settings' },
  'sanity-check': { label: 'Misc', firstPage: 'settings' },
  'db-health': { label: 'Misc', firstPage: 'settings' },
  report: { label: 'Misc', firstPage: 'settings' },
  feedback: { label: 'Misc', firstPage: 'settings' },
};

const PAGE_LABELS: Record<PageId, string> = {
  'mission-control': 'Mission Control',
  summary: 'Summary',
  filesystem: 'Filesystem',
  memory: 'Memory',
  cpu: 'CPU',
  projects: 'Projects',
  users: 'Users',
  adoption: 'Adoption',
  'code-envs': 'Cleaner',
  'code-envs-cleaner': 'Insights',
  'code-envs-comparison': 'Comparison',
  'connections-inventory': 'Inventory',
  'connections-insights': 'Insights',
  'connections-health': 'Health',
  'connections-fs-migration': 'FS Migration',
  logs: 'Errors',
  'sanity-check': 'Sanity Check',
  'container-execs': 'Container Execs',
  'project-cleaner': 'Project Cleaner',
  'project-compute': 'Compute',
  'project-cost': 'Cost / CRU',
  'plugins-installed': 'Installed',
  plugins: 'Plugin Sync',
  report: 'Report',
  'db-health': 'DB Health',
  'image-cleaner': 'Docker Images',
  'cs-template-replacement': 'Replace CS Template',
  'llm-audit': 'Model Audit',
  'k8s-insights': 'K8s Insights',
  settings: 'Settings',
  feedback: 'Feedback',
  agents: 'Agents',
  'agent-tuning': 'Agent Tuning',
};

const EASE: [number, number, number, number] = [0.16, 1, 0.3, 1];

type Phase = Lifecycle['phase'];

export function Breadcrumb() {
  const { state, setActivePage } = useDiag();
  const { activePage, parsedData } = state;
  const reduced = useReducedMotion();

  // Settle tick: fires only on a live running→done transition of the page we
  // are currently viewing. prevRef carries the page it was observed on, so
  // navigating onto an already-done page (or mounting into done) never ticks.
  const phase: Phase = resolveLifecycleById(activePage, parsedData).phase;
  const prevRef = useRef<{ page: PageId; phase: Phase } | null>(null);
  const [showTick, setShowTick] = useState(false);

  useEffect(() => {
    const prev = prevRef.current;
    prevRef.current = { page: activePage, phase };
    if (!prev || prev.page !== activePage) {
      setShowTick(false); // navigation resets tracking; a tick never outlives its page
      return;
    }
    if (prev.phase === 'running' && phase === 'done') setShowTick(true);
  }, [activePage, phase]);

  useEffect(() => {
    if (!showTick) return;
    // ~200ms enter + ~1.2s hold; AnimatePresence exit handles the fade-out.
    const id = window.setTimeout(() => setShowTick(false), 1400);
    return () => window.clearTimeout(id);
  }, [showTick]);

  const section = PAGE_SECTION_MAP[activePage];
  const pageLabel = PAGE_LABELS[activePage];

  if (!section) return null;

  const isSectionSamePage = section.firstPage === activePage;

  const segmentEnter = { duration: reduced ? 0 : 0.15, ease: EASE };
  const segmentExit = { duration: reduced ? 0 : 0.1, ease: EASE };

  return (
    // relative anchors popLayout's absolutely-positioned exiting segments;
    // fixed line-height keeps the row height stable through crossfades.
    <nav className="relative flex items-center gap-1.5 text-sm leading-5" aria-label="Breadcrumb">
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.button
          key={section.label}
          type="button"
          onClick={() => setActivePage(section.firstPage)}
          initial={{ opacity: 0, y: reduced ? 0 : 5 }}
          animate={{ opacity: 1, y: 0, transition: segmentEnter }}
          exit={{ opacity: 0, y: reduced ? 0 : -5, transition: segmentExit }}
          className={`transition-colors ${
            isSectionSamePage
              ? 'text-[var(--text-primary)] cursor-default'
              : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
          }`}
        >
          {section.label}
        </motion.button>
      </AnimatePresence>
      <AnimatePresence mode="popLayout" initial={false}>
        {!isSectionSamePage && (
          <motion.span
            key={activePage}
            className="flex items-center gap-1.5"
            initial={{ opacity: 0, y: reduced ? 0 : 5 }}
            animate={{ opacity: 1, y: 0, transition: segmentEnter }}
            exit={{ opacity: 0, y: reduced ? 0 : -5, transition: segmentExit }}
          >
            <span className="text-[var(--text-tertiary)]" aria-hidden="true">
              ›
            </span>
            <span className="text-[var(--text-primary)]">{pageLabel}</span>
          </motion.span>
        )}
      </AnimatePresence>
      {/* Zero-width anchor: the tick is absolutely positioned so the title never shifts. */}
      <span className="relative w-0 self-stretch" aria-hidden="true">
        <AnimatePresence>
          {showTick && (
            <motion.svg
              key="settle-tick"
              width={12}
              height={12}
              viewBox="0 0 12 12"
              fill="none"
              className="absolute left-0 top-1/2 -mt-1.5"
              initial={{ opacity: 0, scale: reduced ? 1 : 0.6 }}
              animate={{
                opacity: 1,
                scale: 1,
                transition: { duration: reduced ? 0 : 0.2, ease: EASE },
              }}
              exit={{ opacity: 0, transition: { duration: reduced ? 0 : 0.4, ease: 'easeOut' } }}
            >
              <path
                d="M2.5 6.5 5 9l4.5-5.5"
                stroke="var(--success)"
                strokeWidth={1.8}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </motion.svg>
          )}
        </AnimatePresence>
      </span>
    </nav>
  );
}
