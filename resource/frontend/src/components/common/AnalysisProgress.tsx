import { useMemo } from 'react';
import { useDiag } from '../../context/DiagContext';
import { MODULES, BACKGROUND_LOADING_FIELDS, SHARED_LOADING_FIELDS } from '../../utils/moduleRegistry';
import { deriveAnalysisLifecycle } from '../../utils/analysisLifecycle';
import { resolveLifecycleById } from '../../utils/pageLifecycle';
import { ProgressIndicator } from './ProgressIndicator';

export function AnalysisProgress() {
  const { state, setActivePage } = useDiag();
  const { parsedData, dataSource } = state;
  const { core, remaining, failed, rows } = useMemo(() => ({
    core: deriveAnalysisLifecycle(parsedData, SHARED_LOADING_FIELDS, ''),
    remaining: BACKGROUND_LOADING_FIELDS.filter((field) => !['done', 'error'].includes(parsedData[field]?.phase ?? 'queued')).length,
    failed: BACKGROUND_LOADING_FIELDS.filter((field) => parsedData[field]?.phase === 'error').length,
    rows: MODULES.filter((mod) => mod.analysis === 'background').map((mod) => ({
      mod, lifecycle: resolveLifecycleById(mod.id, parsedData),
    })),
  }), [parsedData]);
  if (dataSource !== 'api') return null;
  const label = core.phase === 'done' ? 'Core analysis ready' : core.phase === 'error' ? 'Core analysis has errors' : 'Core analysis loading';
  return (
    <details className="relative w-32 shrink-0 text-[10px]">
      <summary className="cursor-pointer truncate text-[var(--text-secondary)]" title={`${label} · ${remaining} background scans remaining`}>
        {core.phase === 'done' ? 'Core ready' : core.phase === 'error' ? 'Core errors' : 'Analyzing'} · {failed ? `${failed} failed` : `${remaining} pending`}
      </summary>
      <div className="absolute left-0 top-full z-50 mt-2 w-80 max-h-96 overflow-auto rounded-lg border border-[var(--border-default)] bg-[var(--bg-surface)] p-3 shadow-xl">
        <ProgressIndicator lifecycle={core} compact />
        {rows.map(({ mod, lifecycle }) => (
          <div key={mod.id} className="mt-3">
            <button className="text-[var(--text-primary)] hover:underline" onClick={() => setActivePage(mod.id)}>{mod.label}</button>
            <ProgressIndicator lifecycle={lifecycle} compact />
            {'finishedAt' in lifecycle && <span className="text-[var(--text-muted)]" title={lifecycle.finishedAt}>Updated {new Date(lifecycle.finishedAt).toLocaleTimeString()}</span>}
          </div>
        ))}
        <p className="mt-3 text-[var(--text-muted)]">Opening a page prioritizes its scan. Cleanup scans, plugin comparisons, and AI analysis run when requested.</p>
      </div>
    </details>
  );
}
