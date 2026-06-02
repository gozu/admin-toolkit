import { useMemo } from 'react';
import { Modal } from './Modal';
import { dssUrls, getDssBaseUrl } from '../utils/codeEnvUsageLinks';
import type { PluginInfo, PluginProjectUsage, PluginUsageObject } from '../types';

interface PluginProjectsModalProps {
  plugin: PluginInfo | null;
  isOpen: boolean;
  onClose: () => void;
}

function kindLabel(kind: string): string {
  if (!kind) return 'component';
  const base = kind.replace(/^python-/, '').replace(/-/g, ' ');
  // Singularize the trailing 's' for nicer labels.
  return base;
}

function singularize(label: string): string {
  if (label.endsWith('ies')) return label.slice(0, -3) + 'y';
  if (label.endsWith('s')) return label.slice(0, -1);
  return label;
}

function formatKindCount(kind: string, count: number): string {
  const label = kindLabel(kind);
  return `${count} ${count === 1 ? singularize(label) : label}`;
}

function objectUrl(baseUrl: string, projectKey: string, usage: PluginUsageObject): string | null {
  const type = (usage.objectType || '').toUpperCase();
  const id = usage.objectId;
  if (!id) return null;
  if (type === 'RECIPE') return dssUrls.recipe(projectKey, id);
  if (type === 'CLUSTER') return `${baseUrl}/admin/clusters/${encodeURIComponent(id)}/`;
  if (type === 'DATASET') return dssUrls.dataset(projectKey, id);
  if (type === 'SAVED_MODEL') return dssUrls.savedModel(projectKey, id);
  if (type === 'SCENARIO') return dssUrls.scenario(projectKey, id);
  if (type === 'NOTEBOOK' || type === 'JUPYTER_NOTEBOOK') return dssUrls.notebook(projectKey, id);
  if (type === 'WEB_APP' || type === 'WEBAPP') {
    // Webapp leaf route needs `{id}_{name}`; without a name we link to the project's webapps list.
    return `${baseUrl}/projects/${encodeURIComponent(projectKey)}/webapps/`;
  }
  return null;
}

function objectLabel(usage: PluginUsageObject): string {
  const type = (usage.objectType || '').toUpperCase();
  if (!type) return 'object';
  return type.replace(/_/g, ' ').toLowerCase();
}

function sortGroups(groups: PluginProjectUsage[]): PluginProjectUsage[] {
  return [...groups].sort((a, b) => {
    const ad = a.objects.length;
    const bd = b.objects.length;
    if (ad !== bd) return bd - ad;
    return a.projectKey.localeCompare(b.projectKey);
  });
}

export function PluginProjectsModal({ plugin, isOpen, onClose }: PluginProjectsModalProps) {
  const baseUrl = useMemo(() => getDssBaseUrl(), []);
  const groups = useMemo(() => sortGroups(plugin?.projectsUsing || []), [plugin]);
  const missingCount = plugin?.missingTypes?.length || 0;

  const title = plugin
    ? `Projects using ${plugin.label || plugin.id} (${plugin.projectsUsingCount ?? 0})`
    : 'Plugin usage';

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={title}>
      <div className="max-h-[70vh] overflow-auto pr-1">
        {!plugin ? (
          <div className="text-sm text-[var(--text-muted)]">No plugin selected.</div>
        ) : plugin.usagesError ? (
          <div className="text-sm text-[var(--text-muted)]">
            Could not scan usages for this plugin: <span className="font-mono">{plugin.usagesError}</span>
          </div>
        ) : groups.length === 0 ? (
          <div className="text-sm text-[var(--text-muted)]">No projects reference this plugin.</div>
        ) : (
          <div className="space-y-4">
            {groups.map((group) => {
              const kindEntries = Object.entries(group.elementKinds).sort((a, b) => b[1] - a[1]);
              return (
                <div
                  key={group.projectKey}
                  className="rounded-lg border border-[var(--border-glass)] bg-[var(--bg-surface)]"
                >
                  <div className="flex min-h-11 flex-wrap items-center justify-between gap-3 border-b border-[var(--border-glass)] px-3 py-2">
                    <a
                      href={dssUrls.project(group.projectKey)}
                      target="_blank"
                      rel="noreferrer"
                      className="min-w-0 font-mono text-sm font-semibold text-[var(--text-primary)] hover:text-[var(--neon-cyan)] hover:underline"
                    >
                      {group.projectKey}
                    </a>
                    <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--text-muted)]">
                      {kindEntries.map(([kind, count]) => (
                        <span
                          key={kind}
                          className="rounded bg-[var(--bg-glass)] px-1.5 py-0.5 font-mono"
                          title={kind}
                        >
                          {formatKindCount(kind, count)}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="divide-y divide-[var(--border-glass)]/70">
                    {group.objects.map((obj, idx) => {
                      const href = objectUrl(baseUrl, group.projectKey, obj);
                      const name = obj.objectId || '(unnamed)';
                      return (
                        <div
                          key={`${obj.objectType}-${obj.objectId}-${idx}`}
                          className="grid min-h-10 grid-cols-[110px_minmax(0,1fr)] items-center gap-3 px-3 py-2 text-sm"
                        >
                          <span className="text-xs text-[var(--text-muted)]">{objectLabel(obj)}</span>
                          {href ? (
                            <a
                              href={href}
                              target="_blank"
                              rel="noreferrer"
                              className="truncate text-[var(--text-primary)] hover:text-[var(--neon-cyan)] hover:underline"
                              title={obj.elementType}
                            >
                              {name}
                            </a>
                          ) : (
                            <span
                              className="truncate text-[var(--text-primary)]"
                              title={obj.elementType}
                            >
                              {name}
                            </span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        )}
        {plugin && missingCount > 0 && (
          <div className="mt-3 text-xs text-[var(--text-muted)]">
            {missingCount} missing reference{missingCount === 1 ? '' : 's'}
          </div>
        )}
      </div>
    </Modal>
  );
}
