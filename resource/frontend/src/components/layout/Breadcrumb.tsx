import { useDiag } from '../../context/DiagContext';
import type { PageId } from '../../types';

interface SectionInfo {
  label: string;
  firstPage: PageId;
}

const PAGE_SECTION_MAP: Record<PageId, SectionInfo> = {
  summary: { label: 'Overview', firstPage: 'summary' },
  filesystem: { label: 'Overview', firstPage: 'summary' },
  memory: { label: 'Overview', firstPage: 'summary' },
  cpu: { label: 'Overview', firstPage: 'summary' },
  'connections-inventory': { label: 'Connections', firstPage: 'connections-inventory' },
  'connections-insights': { label: 'Connections', firstPage: 'connections-inventory' },
  'connections-health': { label: 'Connections', firstPage: 'connections-inventory' },
  'connections-usage': { label: 'Connections', firstPage: 'connections-inventory' },
  'connections-fs-migration': { label: 'Connections', firstPage: 'connections-inventory' },
  projects: { label: 'Projects', firstPage: 'projects' },
  'project-cleaner': { label: 'Projects', firstPage: 'projects' },
  'project-compute': { label: 'Projects', firstPage: 'projects' },
  users: { label: 'Users', firstPage: 'users' },
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
  settings: { label: 'Misc', firstPage: 'settings' },
  logs: { label: 'Misc', firstPage: 'settings' },
  'sanity-check': { label: 'Misc', firstPage: 'settings' },
  'db-health': { label: 'Misc', firstPage: 'settings' },
  report: { label: 'Misc', firstPage: 'settings' },
};

const PAGE_LABELS: Record<PageId, string> = {
  summary: 'Summary',
  filesystem: 'Filesystem',
  memory: 'Memory',
  cpu: 'CPU',
  projects: 'Projects',
  users: 'Users',
  'code-envs': 'Cleaner',
  'code-envs-cleaner': 'Insights',
  'code-envs-comparison': 'Comparison',
  'connections-inventory': 'Inventory',
  'connections-insights': 'Insights',
  'connections-health': 'Health',
  'connections-usage': 'Usage',
  'connections-fs-migration': 'FS Migration',
  logs: 'Errors',
  'sanity-check': 'Sanity Check',
  'container-execs': 'Container Execs',
  'project-cleaner': 'Project Cleaner',
  'project-compute': 'Compute',
  'plugins-installed': 'Installed',
  plugins: 'Plugin Sync',
  report: 'Report',
  'db-health': 'DB Health',
  'image-cleaner': 'Docker Images',
  'cs-template-replacement': 'Replace CS Template',
  'llm-audit': 'Model Audit',
  'k8s-insights': 'K8s Insights',
  settings: 'Settings',
};

export function Breadcrumb() {
  const { state, setActivePage } = useDiag();
  const { activePage } = state;

  const section = PAGE_SECTION_MAP[activePage];
  const pageLabel = PAGE_LABELS[activePage];

  if (!section) return null;

  const isSectionSamePage = section.firstPage === activePage;

  return (
    <nav className="flex items-center gap-1.5 text-sm" aria-label="Breadcrumb">
      <button
        type="button"
        onClick={() => setActivePage(section.firstPage)}
        className={`transition-colors ${
          isSectionSamePage
            ? 'text-[var(--text-primary)] cursor-default'
            : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
        }`}
      >
        {section.label}
      </button>
      {!isSectionSamePage && (
        <>
          <span className="text-[var(--text-tertiary)]" aria-hidden="true">
            ›
          </span>
          <span className="text-[var(--text-primary)]">{pageLabel}</span>
        </>
      )}
    </nav>
  );
}
