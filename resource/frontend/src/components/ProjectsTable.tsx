import { useState, useMemo } from 'react';
import { motion } from 'framer-motion';
import { useDiag } from '../context/DiagContext';
import { useTableFilter } from '../hooks/useTableFilter';
import { DataGrid } from './common/DataGrid';
import { ExternalLinkIcon } from './ExternalLinkIcon';
import { dssUrls } from '../utils/codeEnvUsageLinks';
import type { ColumnDef } from '../utils/dataGridTypes';
import type { Project } from '../types';

interface ProjectsTableProps {
  onViewPermissions: (project: Project) => void;
}

const EMPTY_ARR: never[] = [];

export function ProjectsTable({ onViewPermissions }: ProjectsTableProps) {
  const { state, setFocusedUserFilter, setActivePage } = useDiag();
  const { isVisible } = useTableFilter();
  const { parsedData } = state;
  const projects = parsedData.projects ?? EMPTY_ARR;

  const [searchText, setSearchText] = useState('');

  const filteredProjects = useMemo(
    () =>
      projects.filter((p) => p.name.toLowerCase().includes(searchText.toLowerCase())),
    [projects, searchText],
  );

  const columns = useMemo<ColumnDef<Project>[]>(() => {
    const goToUser = (login: string) => {
      setFocusedUserFilter({ login });
      setActivePage('users');
    };
    return [
      {
        id: 'name',
        label: 'Project Name',
        defaultSortDir: 'asc',
        width: '70%',
        headerClassName: 'min-w-[300px]',
        cellClassName: 'max-w-[400px]',
        render: (project) => (
          <>
            <button
              onClick={() => onViewPermissions(project)}
              className="text-[var(--neon-cyan)] font-medium hover:underline text-left break-words"
            >
              {project.name}
            </button>
            <a
              href={dssUrls.project(project.key)}
              target="_blank"
              rel="noopener noreferrer"
              title={`Open ${project.key} in DSS`}
              aria-label={`Open ${project.key} in DSS`}
              className="ml-1 text-[var(--text-muted)] hover:text-[var(--neon-cyan)]"
            >
              <ExternalLinkIcon />
            </a>
            <div className="text-xs text-[var(--text-muted)] mt-1">
              Owner:{' '}
              {project.owner ? (
                <button
                  type="button"
                  onClick={() => goToUser(project.owner)}
                  title={`Show ${project.owner} on the Users page`}
                  className="hover:text-[var(--neon-cyan)] hover:underline"
                >
                  {project.owner}
                </button>
              ) : (
                'Unknown'
              )}
            </div>
          </>
        ),
        sortValue: (project) => project.name,
      },
      {
        id: 'versions',
        label: 'Versions',
        mono: true,
        headerClassName: 'whitespace-nowrap',
        cellClassName: 'whitespace-nowrap',
        headerTooltip: 'Sort by versions',
        defaultSortDir: 'desc',
        render: (project) => project.versionNumber,
        sortValue: (project) => project.versionNumber || 0,
      },
      {
        id: 'perms',
        label: 'Perms',
        mono: true,
        headerClassName: 'whitespace-nowrap',
        cellClassName: 'whitespace-nowrap',
        headerTooltip: 'Sort by permissions',
        defaultSortDir: 'desc',
        render: (project) => `${project.permissions.length} entries`,
        sortValue: (project) => project.permissions?.length || 0,
      },
    ];
  }, [onViewPermissions, setFocusedUserFilter, setActivePage]);

  if (!isVisible('projects-table') || projects.length === 0) {
    return null;
  }

  return (
    <motion.div
      className="glass-card overflow-hidden flex flex-col flex-1 min-h-0"
      id="projects-table"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="px-4 py-3 border-b border-[var(--border-glass)]">
        <div className="flex items-center justify-between">
          <h4 className="text-lg font-semibold text-[var(--text-primary)]">Projects</h4>
          <span className="badge badge-info font-mono">{projects.length}</span>
        </div>
      </div>

      <div className="p-4">
        <div className="relative">
          <svg
            className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--text-muted)]"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
            />
          </svg>
          <input
            type="text"
            aria-label="Search projects"
            placeholder="Search projects..."
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            className="input-glass w-full"
            style={{ paddingLeft: '2.5rem', paddingRight: '1rem' }}
          />
        </div>
      </div>

      <DataGrid
        rows={filteredProjects}
        columns={columns}
        rowKey={(project) => project.key}
        defaultSortColumnId="versions"
        filtersActive={searchText.trim().length > 0}
        noMatchMessage="No projects match your search."
        scroll="card"
      />
    </motion.div>
  );
}
