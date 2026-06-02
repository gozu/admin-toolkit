import { motion, AnimatePresence } from 'framer-motion';
import { useState } from 'react';
import type { CollectionDelta, User, Project, Cluster, CodeEnv } from '../../types';
import { CountBadge } from './DeltaBadge';

interface ComparisonCollectionsSectionProps {
  users: CollectionDelta<User>;
  projects: CollectionDelta<Project>;
  clusters: CollectionDelta<Cluster>;
  codeEnvs: CollectionDelta<CodeEnv>;
  plugins: CollectionDelta<string>;
}

interface CollectionCardProps<T> {
  title: string;
  icon: React.ReactNode;
  delta: CollectionDelta<T>;
  renderItem: (item: T, type: 'added' | 'removed') => React.ReactNode;
  renderModified?: (item: { before: T; after: T; changes: string[] }) => React.ReactNode;
  getKey: (item: T) => string;
}

function CollectionCard<T>({
  title,
  icon,
  delta,
  renderItem,
  renderModified,
  getKey,
}: CollectionCardProps<T>) {
  const [expanded, setExpanded] = useState(true); // Always expanded when displayed
  const [showSection, setShowSection] = useState<'added' | 'removed' | 'modified' | null>(
    delta.added.length > 0 ? 'added' : delta.removed.length > 0 ? 'removed' : delta.modified.length > 0 ? 'modified' : null
  );

  const totalChanges = delta.added.length + delta.removed.length + delta.modified.length;
  const total = totalChanges + delta.unchanged;

  return (
    <motion.div
      className="chart-container"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="chart-header w-full flex items-center justify-between cursor-pointer hover:bg-[var(--bg-glass-hover)] transition-colors rounded-t-lg"
      >
        <div className="flex items-center gap-2">
          <span className="text-lg">{icon}</span>
          <h4>{title}</h4>
          <span className="text-sm text-[var(--text-muted)]">({total})</span>
        </div>
        <div className="flex items-center gap-2">
          <CountBadge count={delta.added.length} type="added" />
          <CountBadge count={delta.removed.length} type="removed" />
          <CountBadge count={delta.modified.length} type="modified" />
          <motion.svg
            className="w-5 h-5 text-[var(--text-muted)]"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            animate={{ rotate: expanded ? 0 : -90 }}
            transition={{ duration: 0.2 }}
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </motion.svg>
        </div>
      </button>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            {totalChanges === 0 ? (
              <div className="p-4 text-center text-[var(--text-muted)]">
                <svg className="w-8 h-8 mx-auto mb-2 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                No changes • {delta.unchanged} unchanged
              </div>
            ) : (
              <div>
                {/* Section tabs */}
                <div className="flex border-b border-[var(--border-glass)]">
                  {delta.added.length > 0 && (
                    <button
                      onClick={() => setShowSection('added')}
                      className={`flex-1 px-4 py-2 text-sm font-medium transition-colors ${
                        showSection === 'added'
                          ? 'text-[var(--neon-green)] border-b-2 border-[var(--neon-green)]'
                          : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'
                      }`}
                    >
                      Added ({delta.added.length})
                    </button>
                  )}
                  {delta.removed.length > 0 && (
                    <button
                      onClick={() => setShowSection('removed')}
                      className={`flex-1 px-4 py-2 text-sm font-medium transition-colors ${
                        showSection === 'removed'
                          ? 'text-[var(--neon-red)] border-b-2 border-[var(--neon-red)]'
                          : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'
                      }`}
                    >
                      Removed ({delta.removed.length})
                    </button>
                  )}
                  {delta.modified.length > 0 && (
                    <button
                      onClick={() => setShowSection('modified')}
                      className={`flex-1 px-4 py-2 text-sm font-medium transition-colors ${
                        showSection === 'modified'
                          ? 'text-[var(--neon-amber)] border-b-2 border-[var(--neon-amber)]'
                          : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'
                      }`}
                    >
                      Modified ({delta.modified.length})
                    </button>
                  )}
                </div>

                {/* Content */}
                <div className="max-h-[300px] overflow-y-auto p-2 space-y-1">
                  {showSection === 'added' &&
                    delta.added.map((item) => (
                      <div
                        key={getKey(item)}
                        className="px-3 py-2 rounded bg-[var(--status-success-bg)]/30 border border-[var(--status-success-border)]/30 light-white-bg"
                      >
                        {renderItem(item, 'added')}
                      </div>
                    ))}
                  {showSection === 'removed' &&
                    delta.removed.map((item) => (
                      <div
                        key={getKey(item)}
                        className="px-3 py-2 rounded bg-[var(--status-critical-bg)]/30 border border-[var(--status-critical-border)]/30"
                      >
                        {renderItem(item, 'removed')}
                      </div>
                    ))}
                  {showSection === 'modified' &&
                    renderModified &&
                    delta.modified.map((item) => (
                      <div
                        key={getKey(item.after)}
                        className="px-3 py-2 rounded bg-[var(--status-warning-bg)]/30 border border-[var(--status-warning-border)]/30"
                      >
                        {renderModified(item)}
                      </div>
                    ))}
                </div>

                {/* Unchanged footer */}
                {delta.unchanged > 0 && (
                  <div className="px-4 py-2 text-sm text-[var(--text-muted)] border-t border-[var(--border-glass)]">
                    + {delta.unchanged} unchanged
                  </div>
                )}
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

// Render functions for each type
function renderUser(user: User, type: 'added' | 'removed') {
  const color = type === 'added' ? 'text-[var(--neon-green)]' : 'text-[var(--neon-red)]';
  return (
    <div className="flex items-center justify-between">
      <div>
        <span className={`font-medium ${color}`}>{user.login}</span>
        {user.email && <span className="text-[var(--text-muted)] text-sm ml-2">({user.email})</span>}
      </div>
      <div className="flex items-center gap-2">
        {user.userProfile && (
          <span className="badge badge-info text-xs">{user.userProfile}</span>
        )}
        {user.enabled !== undefined && (
          <span className={`badge ${user.enabled ? 'badge-success' : 'badge-neutral'} text-xs`}>
            {user.enabled ? 'Enabled' : 'Disabled'}
          </span>
        )}
      </div>
    </div>
  );
}

function renderUserModified(item: { before: User; after: User; changes: string[] }) {
  // Determine if this is a positive or negative change
  const enabledChange = item.changes.includes('enabled');
  const isPositive = enabledChange && !item.before.enabled && item.after.enabled;
  const isNegative = enabledChange && item.before.enabled && !item.after.enabled;

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <span className="font-medium text-[var(--neon-amber)]">{item.after.login}</span>
          {isPositive && (
            <span className="text-[var(--neon-green)]" title="User enabled">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 10l7-7m0 0l7 7m-7-7v18" />
              </svg>
            </span>
          )}
          {isNegative && (
            <span className="text-[var(--neon-red)]" title="User disabled">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
              </svg>
            </span>
          )}
        </div>
        <span className="text-xs text-[var(--text-muted)]">
          Changed: {item.changes.join(', ')}
        </span>
      </div>
      {enabledChange && (
        <div className="text-sm">
          <span className="text-[var(--text-muted)]">Enabled: </span>
          <span className="line-through text-[var(--text-muted)]">{item.before.enabled ? 'Yes' : 'No'}</span>
          <span className="mx-1">→</span>
          <span className={item.after.enabled ? 'text-[var(--neon-green)]' : 'text-[var(--neon-red)]'}>
            {item.after.enabled ? 'Yes' : 'No'}
          </span>
        </div>
      )}
      {item.changes.includes('userProfile') && (
        <div className="text-sm">
          <span className="text-[var(--text-muted)]">Profile: </span>
          <span className="line-through text-[var(--text-muted)]">{item.before.userProfile || '—'}</span>
          <span className="mx-1">→</span>
          <span className="text-[var(--text-primary)]">{item.after.userProfile || '—'}</span>
        </div>
      )}
    </div>
  );
}

function renderProject(project: Project, type: 'added' | 'removed') {
  const color = type === 'added' ? 'text-[var(--neon-green)]' : 'text-[var(--neon-red)]';
  return (
    <div className="flex items-center justify-between">
      <div>
        <span className={`font-medium ${color}`}>{project.name}</span>
        <span className="text-[var(--text-muted)] text-sm ml-2">({project.key})</span>
      </div>
      <div className="flex items-center gap-2 text-sm text-[var(--text-muted)]">
        <span>Owner: {project.owner}</span>
        <span>•</span>
        <span>v{project.versionNumber}</span>
      </div>
    </div>
  );
}

function renderProjectModified(item: { before: Project; after: Project; changes: string[] }) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="font-medium text-[var(--neon-amber)]">{item.after.name}</span>
        <span className="text-xs text-[var(--text-muted)]">
          Changed: {item.changes.join(', ')}
        </span>
      </div>
      {item.changes.includes('owner') && (
        <div className="text-sm">
          <span className="text-[var(--text-muted)]">Owner: </span>
          <span className="line-through text-[var(--text-muted)]">{item.before.owner}</span>
          <span className="mx-1">→</span>
          <span className="text-[var(--text-primary)]">{item.after.owner}</span>
        </div>
      )}
      {item.changes.includes('version') && (
        <div className="text-sm">
          <span className="text-[var(--text-muted)]">Version: </span>
          <span className="line-through text-[var(--text-muted)]">v{item.before.versionNumber}</span>
          <span className="mx-1">→</span>
          <span className="text-[var(--text-primary)]">v{item.after.versionNumber}</span>
        </div>
      )}
      {item.changes.includes('permissions') && (
        <div className="text-sm text-[var(--neon-amber)]">Permissions modified</div>
      )}
    </div>
  );
}

function renderCluster(cluster: Cluster, type: 'added' | 'removed') {
  const color = type === 'added' ? 'text-[var(--neon-green)]' : 'text-[var(--neon-red)]';
  return (
    <div className="flex items-center justify-between">
      <div>
        <span className={`font-medium ${color}`}>{cluster.name}</span>
        {cluster.region && <span className="text-[var(--text-muted)] text-sm ml-2">({cluster.region})</span>}
      </div>
      <div className="flex items-center gap-2">
        {cluster.version && (
          <span className="text-xs text-[var(--text-muted)]">v{cluster.version}</span>
        )}
        {cluster.status && (
          <span className={`badge ${cluster.status === 'ON' ? 'badge-success' : cluster.status === 'OFF' ? 'badge-neutral' : 'badge-warning'} text-xs`}>
            {cluster.status}
          </span>
        )}
      </div>
    </div>
  );
}

function renderClusterModified(item: { before: Cluster; after: Cluster; changes: string[] }) {
  // Determine if this is a positive or negative change
  const statusChange = item.changes.includes('status');
  const isPositive = statusChange && item.before.status !== 'ON' && item.after.status === 'ON';
  const isNegative = statusChange && item.before.status === 'ON' && item.after.status !== 'ON';

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <span className="font-medium text-[var(--neon-amber)]">{item.after.name}</span>
          {isPositive && (
            <span className="text-[var(--neon-green)]" title="Cluster turned ON">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 10l7-7m0 0l7 7m-7-7v18" />
              </svg>
            </span>
          )}
          {isNegative && (
            <span className="text-[var(--neon-red)]" title="Cluster turned OFF">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
              </svg>
            </span>
          )}
        </div>
        <span className="text-xs text-[var(--text-muted)]">
          Changed: {item.changes.join(', ')}
        </span>
      </div>
      {statusChange && (
        <div className="text-sm">
          <span className="text-[var(--text-muted)]">Status: </span>
          <span className="line-through text-[var(--text-muted)]">{item.before.status}</span>
          <span className="mx-1">→</span>
          <span className={item.after.status === 'ON' ? 'text-[var(--neon-green)]' : 'text-[var(--neon-red)]'}>
            {item.after.status}
          </span>
        </div>
      )}
      {item.changes.includes('version') && (
        <div className="text-sm">
          <span className="text-[var(--text-muted)]">Version: </span>
          <span className="line-through text-[var(--text-muted)]">{item.before.version}</span>
          <span className="mx-1">→</span>
          <span className="text-[var(--text-primary)]">{item.after.version}</span>
        </div>
      )}
      {item.changes.includes('nodeGroups') && (
        <div className="text-sm text-[var(--neon-amber)]">Node groups modified</div>
      )}
    </div>
  );
}

function renderCodeEnv(env: CodeEnv, type: 'added' | 'removed') {
  const color = type === 'added' ? 'text-[var(--neon-green)]' : 'text-[var(--neon-red)]';
  return (
    <div className="flex items-center justify-between">
      <span className={`font-medium ${color}`}>{env.name}</span>
      <span className="text-xs font-mono text-[var(--text-muted)]">{env.version}</span>
    </div>
  );
}

function renderCodeEnvModified(item: { before: CodeEnv; after: CodeEnv; changes: string[] }) {
  return (
    <div className="flex items-center justify-between">
      <span className="font-medium text-[var(--neon-amber)]">{item.after.name}</span>
      <div className="text-sm">
        <span className="line-through text-[var(--text-muted)]">{item.before.version}</span>
        <span className="mx-1">→</span>
        <span className="text-[var(--text-primary)]">{item.after.version}</span>
      </div>
    </div>
  );
}

function renderPlugin(plugin: string, type: 'added' | 'removed') {
  const color = type === 'added' ? 'text-[var(--neon-green)]' : 'text-[var(--neon-red)]';
  return <span className={`font-medium ${color}`}>{plugin}</span>;
}

// SVG Icons
const UsersIcon = () => (
  <svg className="w-5 h-5 text-[var(--text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
  </svg>
);

const ProjectsIcon = () => (
  <svg className="w-5 h-5 text-[var(--text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
  </svg>
);

const ClustersIcon = () => (
  <svg className="w-5 h-5 text-[var(--text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z" />
  </svg>
);

const CodeEnvsIcon = () => (
  <svg className="w-5 h-5 text-[var(--text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
  </svg>
);

const PluginsIcon = () => (
  <svg className="w-5 h-5 text-[var(--text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 10V3L4 14h7v7l9-11h-7z" />
  </svg>
);

// Combined change entry type
interface CombinedChangeEntry {
  type: 'added' | 'removed' | 'modified';
  collection: string;
  name: string;
  detail?: string;
  isPositive?: boolean;
  isNegative?: boolean;
}

export function ComparisonCollectionsSection({
  users,
  projects,
  clusters,
  codeEnvs,
  plugins,
}: ComparisonCollectionsSectionProps) {
  const [viewMode, setViewMode] = useState<'cards' | 'combined'>('cards');

  const totalChanges =
    users.added.length + users.removed.length + users.modified.length +
    projects.added.length + projects.removed.length + projects.modified.length +
    clusters.added.length + clusters.removed.length + clusters.modified.length +
    codeEnvs.added.length + codeEnvs.removed.length + codeEnvs.modified.length +
    plugins.added.length + plugins.removed.length;

  // Check if there's any data at all
  const hasData =
    users.added.length + users.removed.length + users.modified.length + users.unchanged > 0 ||
    projects.added.length + projects.removed.length + projects.modified.length + projects.unchanged > 0 ||
    clusters.added.length + clusters.removed.length + clusters.modified.length + clusters.unchanged > 0 ||
    codeEnvs.added.length + codeEnvs.removed.length + codeEnvs.modified.length + codeEnvs.unchanged > 0 ||
    plugins.added.length + plugins.removed.length + plugins.unchanged > 0;

  if (!hasData) return null;

  // Build combined list for combined view
  const combinedChanges: CombinedChangeEntry[] = [
    ...users.added.map(u => ({ type: 'added' as const, collection: 'User', name: u.login, detail: u.email })),
    ...users.removed.map(u => ({ type: 'removed' as const, collection: 'User', name: u.login, detail: u.email })),
    ...users.modified.map(m => ({
      type: 'modified' as const,
      collection: 'User',
      name: m.after.login,
      detail: m.changes.join(', '),
      isPositive: m.changes.includes('enabled') && !m.before.enabled && m.after.enabled,
      isNegative: m.changes.includes('enabled') && m.before.enabled && !m.after.enabled,
    })),
    ...projects.added.map(p => ({ type: 'added' as const, collection: 'Project', name: p.name, detail: p.key })),
    ...projects.removed.map(p => ({ type: 'removed' as const, collection: 'Project', name: p.name, detail: p.key })),
    ...projects.modified.map(m => ({ type: 'modified' as const, collection: 'Project', name: m.after.name, detail: m.changes.join(', ') })),
    ...clusters.added.map(c => ({ type: 'added' as const, collection: 'Cluster', name: c.name, detail: c.region })),
    ...clusters.removed.map(c => ({ type: 'removed' as const, collection: 'Cluster', name: c.name, detail: c.region })),
    ...clusters.modified.map(m => ({
      type: 'modified' as const,
      collection: 'Cluster',
      name: m.after.name,
      detail: m.changes.join(', '),
      isPositive: m.changes.includes('status') && m.before.status !== 'ON' && m.after.status === 'ON',
      isNegative: m.changes.includes('status') && m.before.status === 'ON' && m.after.status !== 'ON',
    })),
    ...codeEnvs.added.map(e => ({ type: 'added' as const, collection: 'Code Env', name: e.name, detail: e.version })),
    ...codeEnvs.removed.map(e => ({ type: 'removed' as const, collection: 'Code Env', name: e.name, detail: e.version })),
    ...codeEnvs.modified.map(m => ({ type: 'modified' as const, collection: 'Code Env', name: m.after.name, detail: `${m.before.version} → ${m.after.version}` })),
    ...plugins.added.map(p => ({ type: 'added' as const, collection: 'Plugin', name: p })),
    ...plugins.removed.map(p => ({ type: 'removed' as const, collection: 'Plugin', name: p })),
  ];

  return (
    <motion.div
      className="mb-5"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold text-neon-subtle">Collections Comparison</h2>
        <div className="flex items-center gap-3">
          {/* View mode toggle */}
          {totalChanges > 0 && (
            <div className="flex items-center bg-[var(--bg-glass)] rounded-lg p-0.5">
              <button
                onClick={() => setViewMode('cards')}
                className={`px-2 py-1 text-xs rounded-md transition-colors ${
                  viewMode === 'cards'
                    ? 'bg-[var(--neon-cyan)]/20 text-[var(--neon-cyan)]'
                    : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'
                }`}
              >
                Cards
              </button>
              <button
                onClick={() => setViewMode('combined')}
                className={`px-2 py-1 text-xs rounded-md transition-colors ${
                  viewMode === 'combined'
                    ? 'bg-[var(--neon-cyan)]/20 text-[var(--neon-cyan)]'
                    : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'
                }`}
              >
                Combined
              </button>
            </div>
          )}
          {totalChanges > 0 ? (
            <span className="badge badge-warning">{totalChanges} change{totalChanges !== 1 ? 's' : ''}</span>
          ) : (
            <span className="badge badge-success">No changes</span>
          )}
        </div>
      </div>

      {/* Combined view */}
      {viewMode === 'combined' && combinedChanges.length > 0 && (
        <motion.div
          className="chart-container"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <div className="chart-header">
            <h4>All Changes</h4>
          </div>
          <div className="max-h-[400px] overflow-y-auto p-2 space-y-1">
            {combinedChanges.map((entry, idx) => {
              const bgClass =
                entry.type === 'added'
                  ? 'bg-[var(--status-success-bg)]/30 border-[var(--status-success-border)]/30'
                  : entry.type === 'removed'
                    ? 'bg-[var(--status-critical-bg)]/30 border-[var(--status-critical-border)]/30'
                    : 'bg-[var(--status-warning-bg)]/30 border-[var(--status-warning-border)]/30';
              const textColor =
                entry.type === 'added'
                  ? 'text-[var(--neon-green)]'
                  : entry.type === 'removed'
                    ? 'text-[var(--neon-red)]'
                    : 'text-[var(--neon-amber)]';

              return (
                <div
                  key={`${entry.collection}-${entry.name}-${idx}`}
                  className={`px-3 py-2 rounded border ${bgClass} flex items-center justify-between`}
                >
                  <div className="flex items-center gap-2">
                    <span className={`badge ${entry.type === 'added' ? 'badge-success' : entry.type === 'removed' ? 'badge-critical' : 'badge-warning'} text-xs`}>
                      {entry.type}
                    </span>
                    <span className="text-xs text-[var(--text-muted)] px-1 py-0.5 bg-[var(--bg-glass)] rounded">
                      {entry.collection}
                    </span>
                    <span className={`font-medium ${textColor}`}>{entry.name}</span>
                    {entry.isPositive && (
                      <span className="text-[var(--neon-green)]">
                        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 10l7-7m0 0l7 7m-7-7v18" />
                        </svg>
                      </span>
                    )}
                    {entry.isNegative && (
                      <span className="text-[var(--neon-red)]">
                        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
                        </svg>
                      </span>
                    )}
                  </div>
                  {entry.detail && (
                    <span className="text-xs text-[var(--text-muted)]">{entry.detail}</span>
                  )}
                </div>
              );
            })}
          </div>
        </motion.div>
      )}

      {/* Cards view */}
      {viewMode === 'cards' && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {(users.added.length + users.removed.length + users.modified.length + users.unchanged > 0) && (
          <CollectionCard
            title="Users"
            icon={<UsersIcon />}
            delta={users}
            renderItem={renderUser}
            renderModified={renderUserModified}
            getKey={(u) => u.login}
          />
        )}

        {(projects.added.length + projects.removed.length + projects.modified.length + projects.unchanged > 0) && (
          <CollectionCard
            title="Projects"
            icon={<ProjectsIcon />}
            delta={projects}
            renderItem={renderProject}
            renderModified={renderProjectModified}
            getKey={(p) => p.key}
          />
        )}

        {(clusters.added.length + clusters.removed.length + clusters.modified.length + clusters.unchanged > 0) && (
          <CollectionCard
            title="K8s Clusters"
            icon={<ClustersIcon />}
            delta={clusters}
            renderItem={renderCluster}
            renderModified={renderClusterModified}
            getKey={(c) => c.name}
          />
        )}

        {(codeEnvs.added.length + codeEnvs.removed.length + codeEnvs.modified.length + codeEnvs.unchanged > 0) && (
          <CollectionCard
            title="Code Environments"
            icon={<CodeEnvsIcon />}
            delta={codeEnvs}
            renderItem={renderCodeEnv}
            renderModified={renderCodeEnvModified}
            getKey={(e) => e.name}
          />
        )}

        {(plugins.added.length + plugins.removed.length + plugins.unchanged > 0) && (
          <CollectionCard
            title="Plugins"
            icon={<PluginsIcon />}
            delta={plugins}
            renderItem={renderPlugin}
            getKey={(p) => p}
          />
        )}
        </div>
      )}
    </motion.div>
  );
}
