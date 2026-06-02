import { useMemo } from 'react';
import { Modal } from './Modal';
import { dssUrls } from '../utils/codeEnvUsageLinks';
import type { ProjectFootprintRow } from '../types';

interface ProjectCodeStudiosModalProps {
  project: ProjectFootprintRow | null;
  isOpen: boolean;
  onClose: () => void;
}

export function ProjectCodeStudiosModal({ project, isOpen, onClose }: ProjectCodeStudiosModalProps) {
  const studios = useMemo(() => {
    const list = project?.codeStudios || [];
    return [...list].sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id));
  }, [project]);

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={project ? `Code studios in ${project.projectKey}` : 'Code studios'}
    >
      <div className="max-h-[70vh] overflow-auto pr-1">
        {!project ? (
          <div className="text-sm text-[var(--text-muted)]">No project selected.</div>
        ) : studios.length === 0 ? (
          <div className="text-sm text-[var(--text-muted)]">This project has no code studios.</div>
        ) : (
          <div className="divide-y divide-[var(--border-glass)]/70 rounded-lg border border-[var(--border-glass)] bg-[var(--bg-surface)]">
            {studios.map((studio) => (
              <div
                key={studio.id}
                className="grid min-h-10 grid-cols-[minmax(0,1fr)] items-center gap-3 px-3 py-2 text-sm"
              >
                <a
                  href={dssUrls.codeStudio(project.projectKey, studio.id)}
                  target="_blank"
                  rel="noreferrer"
                  className="truncate text-[var(--text-primary)] hover:text-[var(--neon-cyan)] hover:underline"
                >
                  {studio.name || studio.id}
                </a>
              </div>
            ))}
          </div>
        )}
      </div>
    </Modal>
  );
}
