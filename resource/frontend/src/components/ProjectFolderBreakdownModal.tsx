import { useMemo } from 'react';
import { Modal } from './Modal';
import { useDiag } from '../context/DiagContext';
import { formatAuto, getRelativeSizeColor } from '../utils/formatters';
import type { ProjectFootprintRow } from '../types';

interface ProjectFolderBreakdownModalProps {
  project: ProjectFootprintRow | null;
  isOpen: boolean;
  onClose: () => void;
}

export function ProjectFolderBreakdownModal({
  project,
  isOpen,
  onClose,
}: ProjectFolderBreakdownModalProps) {
  const { setActivePage } = useDiag();
  const breakdown = project?.footprintBreakdown;
  const buckets = useMemo(() => breakdown?.buckets || [], [breakdown]);
  const total = project?.totalBytes || 0;

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={project ? `Storage breakdown — ${project.projectKey}` : 'Storage breakdown'}
    >
      <div className="max-h-[70vh] overflow-auto pr-1">
        {!project ? (
          <div className="text-sm text-[var(--text-muted)]">No project selected.</div>
        ) : buckets.length === 0 ? (
          <div className="text-sm text-[var(--text-muted)]">No folder breakdown available.</div>
        ) : (
          <>
            <div className="divide-y divide-[var(--border-glass)]/70 rounded-lg border border-[var(--border-glass)] bg-[var(--bg-surface)]">
              {buckets.map((bucket) => {
                const pct = total > 0 ? Math.min(100, (bucket.bytes / total) * 100) : 0;
                return (
                  <div key={bucket.name} className="px-3 py-2 text-sm">
                    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-[var(--text-primary)]">{bucket.label}</div>
                        {bucket.location && (
                          <div className="truncate font-mono text-[10px] text-[var(--text-muted)]">
                            {bucket.location}
                          </div>
                        )}
                      </div>
                      <span
                        className={`whitespace-nowrap font-mono ${getRelativeSizeColor(bucket.bytes, total)}`}
                      >
                        {formatAuto(bucket.bytes)}
                      </span>
                    </div>
                    <div className="mt-1 h-1 overflow-hidden rounded-full bg-[var(--border-glass)]">
                      <div
                        className="h-full bg-[var(--neon-cyan)]/60"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>

            {(breakdown?.otherCount ?? 0) > 0 && (
              <div className="mt-2 text-xs text-[var(--text-muted)]">
                +{breakdown?.otherCount} smaller folders — {formatAuto(breakdown?.otherBytes)}
              </div>
            )}

            <div className="mt-3 flex items-center justify-between text-sm">
              <span className="text-[var(--text-secondary)]">Total</span>
              <span className="font-mono font-semibold text-[var(--text-primary)]">
                {formatAuto(total)}
              </span>
            </div>

            <button
              type="button"
              onClick={() => {
                setActivePage('filesystem');
                onClose();
              }}
              className="mt-3 cursor-pointer bg-transparent p-0 text-xs text-[var(--neon-cyan)] hover:underline"
            >
              View in Filesystem tree →
            </button>
          </>
        )}
      </div>
    </Modal>
  );
}
