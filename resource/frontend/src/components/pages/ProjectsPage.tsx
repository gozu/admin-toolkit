import { useState, useCallback } from 'react';
import { useDiag } from '../../context/DiagContext';
import {
  ProjectsTable,
  ProjectFootprintTable,
  ProjectPermissionsModal,
} from '../index';
import { useModal } from '../../hooks';
import type { Project } from '../../types';

export function ProjectsPage() {
  const { state } = useDiag();
  const { parsedData } = state;

  const permissionsModal = useModal();
  const [viewingProject, setViewingProject] = useState<Project | null>(null);

  const handleViewPermissions = useCallback(
    (project: Project) => {
      setViewingProject(project);
      permissionsModal.open();
    },
    [permissionsModal],
  );

  const hasProjects = (parsedData.projects?.length || 0) > 0;
  const hasFootprint = (parsedData.projectFootprint?.length || 0) > 0;
  const footprintLifecycle = parsedData.projectFootprintLoading;
  const footprintLoading =
    footprintLifecycle?.phase === 'running' || footprintLifecycle?.phase === 'queued';

  return (
    <div className="page-fill">
      <div className="flex flex-col gap-6 flex-1 min-h-0">
        {hasProjects && <ProjectsTable onViewPermissions={handleViewPermissions} />}

        {(hasFootprint || footprintLoading) && <ProjectFootprintTable />}

        {!hasProjects && !hasFootprint && !footprintLoading && (
          <div className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)] p-8 text-center">
            <p className="text-[var(--text-secondary)]">No project data available.</p>
          </div>
        )}
      </div>

      <ProjectPermissionsModal
        isOpen={permissionsModal.isOpen}
        onClose={permissionsModal.close}
        project={viewingProject}
      />
    </div>
  );
}
