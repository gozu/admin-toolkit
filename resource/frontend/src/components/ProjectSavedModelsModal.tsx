import { useMemo } from 'react';
import { Modal } from './Modal';
import { dssUrls } from '../utils/codeEnvUsageLinks';
import { modelKindLabel } from '../utils/modelKind';
import type { ProjectFootprintRow } from '../types';

interface ProjectSavedModelsModalProps {
  project: ProjectFootprintRow | null;
  isOpen: boolean;
  onClose: () => void;
}

export function ProjectSavedModelsModal({ project, isOpen, onClose }: ProjectSavedModelsModalProps) {
  const models = useMemo(() => {
    const list = project?.savedModels || [];
    return [...list].sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id));
  }, [project]);

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={project ? `Saved models in ${project.projectKey}` : 'Saved models'}
    >
      <div className="max-h-[70vh] overflow-auto pr-1">
        {!project ? (
          <div className="text-sm text-[var(--text-muted)]">No project selected.</div>
        ) : models.length === 0 ? (
          <div className="text-sm text-[var(--text-muted)]">This project has no saved models.</div>
        ) : (
          <div className="divide-y divide-[var(--border-glass)]/70 rounded-lg border border-[var(--border-glass)] bg-[var(--bg-surface)]">
            {models.map((model) => (
              <div
                key={model.id}
                className="grid min-h-10 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-3 py-2 text-sm"
              >
                <a
                  href={dssUrls.savedModel(project.projectKey, model.id)}
                  target="_blank"
                  rel="noreferrer"
                  className="truncate text-[var(--text-primary)] hover:text-[var(--neon-cyan)] hover:underline"
                >
                  {model.name || model.id}
                </a>
                <span className="text-xs text-[var(--text-muted)] whitespace-nowrap">
                  {modelKindLabel(model)}
                  {(model.versionsCount ?? 0) > 0 && (
                    <span className="ml-2 font-mono">
                      {model.versionsCount} version{model.versionsCount === 1 ? '' : 's'}
                    </span>
                  )}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </Modal>
  );
}
