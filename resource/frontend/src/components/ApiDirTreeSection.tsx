import { useCallback, useEffect } from 'react';
import { motion } from 'framer-motion';
import { DirTreemap } from './DirTreemap';
import { DirTreeTable } from './DirTreeTable';
import { useApiDirTree } from '../hooks';

export function ApiDirTreeSection() {
  const { state, loadRoot, abortLoad, expandDirectory, schedulePrefetch, cancelPrefetch } = useApiDirTree();

  const scope = state.scope;
  const projectKey = state.projectKey;

  const handleLoad = useCallback(() => {
    if (!state.isLoading) {
      loadRoot({ scope, projectKey });
    }
  }, [loadRoot, scope, projectKey, state.isLoading]);

  useEffect(() => {
    if (!state.tree && !state.isLoading && !state.error) {
      loadRoot();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => () => {
    cancelPrefetch();
  }, [cancelPrefetch]);

  if (state.isLoading && !state.tree) {
    return (
      <div className="col-span-full">
        <motion.div
          className="glass-card p-6"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
        >
          <h3 className="text-lg font-semibold text-neon-subtle mb-4">
            Directory Space Analysis
          </h3>
          <p className="text-sm text-[var(--text-muted)]">Loading directory tree from server (DSS Data Directory)...</p>
          <button
            onClick={abortLoad}
            className="mt-4 px-4 py-2 text-sm rounded bg-[var(--status-warning-bg)] border border-[var(--status-warning-border)] text-[var(--text-primary)] hover:opacity-90 transition-colors"
          >
            Abort
          </button>
        </motion.div>
      </div>
    );
  }

  if (state.error) {
    return (
      <div className="col-span-full">
        <motion.div
          className="glass-card p-6 border-l-4 border-[var(--neon-red)]"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <h3 className="text-lg font-semibold text-[var(--neon-red)] mb-2">
            Failed to Load Directory Analysis
          </h3>
          <p className="text-sm text-[var(--text-muted)]">{state.error}</p>
          <button
            onClick={handleLoad}
            className="mt-4 px-4 py-2 text-sm rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-[var(--text-secondary)] transition-colors"
          >
            Retry
          </button>
        </motion.div>
      </div>
    );
  }

  if (!state.tree) {
    return (
      <div className="col-span-full">
        <motion.div
          className="glass-card p-6"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
        >
          <h3 className="text-lg font-semibold text-neon-subtle mb-4">
            Directory Space Analysis
          </h3>
          <p className="text-sm text-[var(--text-muted)] mb-4">Analyze disk usage of the DSS Data Directory.</p>
          <button
            onClick={handleLoad}
            disabled={state.isLoading}
            className="px-4 py-2 text-sm rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-[var(--text-secondary)] transition-colors disabled:opacity-60"
          >
            {state.isLoading ? 'Loading...' : 'Load Directory Tree'}
          </button>
        </motion.div>
      </div>
    );
  }

  return (
    <div className="col-span-full flex flex-col flex-1 min-h-0">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 flex-1 min-h-0">
        <DirTreemap
          data={state.tree}
          onExpand={expandDirectory}
          expandedNodes={state.expandedNodes}
          isExpanding={state.isExpanding}
          onVisibleDirectoriesChange={schedulePrefetch}
        />
        <DirTreeTable
          data={state.tree}
          onExpand={expandDirectory}
          expandedNodes={state.expandedNodes}
          isExpanding={state.isExpanding}
        />
      </div>
    </div>
  );
}
