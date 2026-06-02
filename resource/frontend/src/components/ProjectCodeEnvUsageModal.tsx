import { useMemo } from 'react';
import { Modal } from './Modal';
import { getDssBaseUrl, objectLabel, objectUrl } from '../utils/codeEnvUsageLinks';
import type { CodeEnvUsageRef, ProjectFootprintRow } from '../types';

interface ProjectCodeEnvUsageModalProps {
  project: ProjectFootprintRow | null;
  isOpen: boolean;
  onClose: () => void;
}

interface EnvGroup {
  envKey: string;
  envName: string;
  envLanguage: string;
  envOwner: string;
  rows: CodeEnvUsageRef[];
}

function fmt(value: unknown): string {
  if (value == null || value === '') return '--';
  return String(value);
}

function envDesignUrl(baseUrl: string, language: string, name: string): string {
  return `${baseUrl}/admin/code-envs/design/${encodeURIComponent(language)}/${encodeURIComponent(name)}/`;
}

function deriveEnvNameAndLang(envKey: string): { name: string; language: string } {
  const idx = envKey.indexOf(':');
  if (idx >= 0) {
    return { language: envKey.slice(0, idx), name: envKey.slice(idx + 1) };
  }
  return { language: '', name: envKey };
}

export function ProjectCodeEnvUsageModal({ project, isOpen, onClose }: ProjectCodeEnvUsageModalProps) {
  const baseUrl = useMemo(() => getDssBaseUrl(), []);

  const groups = useMemo<EnvGroup[]>(() => {
    if (!project) return [];
    const grouped = new Map<string, EnvGroup>();
    for (const usage of project.usageDetails || []) {
      const envKey = usage.codeEnvKey || (usage.codeEnvLanguage && usage.codeEnvName ? `${usage.codeEnvLanguage}:${usage.codeEnvName}` : usage.codeEnvName || '');
      if (!envKey) continue;
      const existing = grouped.get(envKey);
      if (existing) {
        existing.rows.push(usage);
        if (!existing.envName && usage.codeEnvName) existing.envName = usage.codeEnvName;
        if (!existing.envLanguage && usage.codeEnvLanguage) existing.envLanguage = usage.codeEnvLanguage;
        if (!existing.envOwner && usage.codeEnvOwner) existing.envOwner = usage.codeEnvOwner;
      } else {
        const derived = deriveEnvNameAndLang(envKey);
        grouped.set(envKey, {
          envKey,
          envName: usage.codeEnvName || derived.name,
          envLanguage: usage.codeEnvLanguage || derived.language,
          envOwner: usage.codeEnvOwner || '',
          rows: [usage],
        });
      }
    }
    for (const envKey of project.codeEnvKeys || []) {
      if (!grouped.has(envKey)) {
        const derived = deriveEnvNameAndLang(envKey);
        grouped.set(envKey, {
          envKey,
          envName: derived.name,
          envLanguage: derived.language,
          envOwner: '',
          rows: [],
        });
      }
    }
    return Array.from(grouped.values()).sort((a, b) => a.envName.localeCompare(b.envName));
  }, [project]);

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={project ? `Code envs used by ${project.projectKey}` : 'Code env usage'}>
      <div className="max-h-[70vh] overflow-auto pr-1">
        {!project ? (
          <div className="text-sm text-[var(--text-muted)]">No project selected.</div>
        ) : groups.length === 0 ? (
          <div className="text-sm text-[var(--text-muted)]">This project does not reference any code environments.</div>
        ) : (
          <div className="space-y-4">
            {groups.map((group) => {
              const headerHref = group.envLanguage && group.envName ? envDesignUrl(baseUrl, group.envLanguage, group.envName) : null;
              return (
                <div key={group.envKey} className="rounded-lg border border-[var(--border-glass)] bg-[var(--bg-surface)]">
                  <div className="flex min-h-11 flex-wrap items-center justify-between gap-3 border-b border-[var(--border-glass)] px-3 py-2">
                    {headerHref ? (
                      <a
                        href={headerHref}
                        target="_blank"
                        rel="noreferrer"
                        className="min-w-0 font-mono text-sm font-semibold text-[var(--text-primary)] hover:text-[var(--neon-cyan)] hover:underline"
                      >
                        {group.envName}
                      </a>
                    ) : (
                      <span className="min-w-0 font-mono text-sm font-semibold text-[var(--text-primary)]">
                        {group.envName || group.envKey}
                      </span>
                    )}
                    <div className="flex items-center gap-3 text-xs text-[var(--text-muted)]">
                      {group.envLanguage && <span className="font-mono">{group.envLanguage}</span>}
                      {group.envOwner && <span>Owner: {group.envOwner}</span>}
                    </div>
                  </div>
                  <div className="divide-y divide-[var(--border-glass)]/70">
                    {group.rows.length === 0 ? (
                      <div className="px-3 py-2 text-sm text-[var(--text-muted)]">
                        No specific recipes/notebooks/scenarios &mdash; referenced only as project default or via project settings.
                      </div>
                    ) : (
                      group.rows.map((usage, idx) => (
                        <div
                          key={`${usage.objectType || usage.usageType}-${usage.objectId}-${idx}`}
                          className="grid min-h-10 grid-cols-[110px_minmax(0,1fr)] items-center gap-3 px-3 py-2 text-sm"
                        >
                          <span className="text-xs text-[var(--text-muted)]">{objectLabel(usage)}</span>
                          <a
                            href={objectUrl(baseUrl, usage)}
                            target="_blank"
                            rel="noreferrer"
                            className="truncate text-[var(--text-primary)] hover:text-[var(--neon-cyan)] hover:underline"
                          >
                            {fmt(usage.objectName || usage.objectId)}
                          </a>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </Modal>
  );
}
