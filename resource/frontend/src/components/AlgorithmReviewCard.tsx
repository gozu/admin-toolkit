import { useState } from 'react';
import { fetchJson, ApiRequestError } from '../utils/api';
import { useRedState } from '../state/redUnlockStore';

interface NotebookResult {
  file: string;
  notebookName: string;
  status: 'created' | 'updated' | 'failed' | string;
  error?: string;
}

interface AlgorithmReviewResult {
  projectKey: string;
  kernelEnv: string;
  kernelFallbackUsed: boolean;
  warnings: string[];
  library: { written: string[]; errors: Array<{ file: string; error: string }> };
  notebooks: NotebookResult[];
  createdCount: number;
  updatedCount: number;
  failedCount: number;
}

/**
 * Settings action: materialize a human-reviewable copy of the webapp's
 * Dataiku-API logic inside the project that hosts this webapp — the `adk_notebook`
 * shared libraries (written to the project Python library) plus one Jupyter notebook
 * per scan card, with the verbatim source. Server endpoint is @advanced-gated.
 */
export function AlgorithmReviewCard() {
  const { authed: unlocked } = useRedState();
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<AlgorithmReviewResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetchJson<AlgorithmReviewResult>('/api/algorithm-review/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      setResult(res);
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  return (
    <section className="glass-card p-4 space-y-3">
      <div>
        <h3 className="text-lg font-semibold text-[var(--text-primary)]">Algorithm review notebooks</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Writes the <code className="text-[var(--text-secondary)]">adk_notebook</code> shared libraries
          into this webapp's own project Python library and creates one Jupyter notebook per scan, with the
          exact source — so the Dataiku-API code can be reviewed and run inside DSS. Re-running updates in place.
        </p>
      </div>

      {!unlocked ? (
        <p className="text-xs text-[var(--text-muted)] italic">
          Unlock Advanced Actions above to use this.
        </p>
      ) : (
        <>
          <button
            type="button"
            onClick={() => void run()}
            disabled={running}
            className="px-3 py-1.5 rounded bg-[var(--accent)]/20 text-[var(--accent)] hover:bg-[var(--accent)]/30 text-sm transition-colors disabled:opacity-50"
          >
            {running ? 'Creating…' : 'Create review notebooks'}
          </button>

          {error && <p className="text-sm text-[var(--neon-red)] break-words">{error}</p>}

          {result && <AlgorithmReviewResultView result={result} />}
        </>
      )}
    </section>
  );
}

function AlgorithmReviewResultView({ result }: { result: AlgorithmReviewResult }) {
  const libErrors = result.library.errors ?? [];
  return (
    <div className="space-y-2 text-sm">
      <p className="text-[var(--text-secondary)]">
        Wrote <span className="font-medium">{result.library.written.length}</span> library file
        {result.library.written.length === 1 ? '' : 's'} ·{' '}
        <span className="font-medium">{result.createdCount}</span> created ·{' '}
        <span className="font-medium">{result.updatedCount}</span> updated
        {result.failedCount > 0 && (
          <>
            {' '}· <span className="font-medium text-[var(--neon-red)]">{result.failedCount}</span> failed
          </>
        )}
        {' '}· kernel <code className="text-[var(--text-secondary)]">{result.kernelEnv}</code>
        {' '}· project <code className="text-[var(--text-secondary)]">{result.projectKey}</code>
      </p>

      {result.warnings.map((w) => (
        <p key={w} className="text-xs text-[var(--warning)]">
          {w}
        </p>
      ))}

      {libErrors.map((e) => (
        <p key={e.file} className="text-xs text-[var(--neon-red)] break-words">
          {e.file}: {e.error}
        </p>
      ))}

      <details className="text-xs">
        <summary className="cursor-pointer text-[var(--text-muted)]">
          {result.notebooks.length} notebooks
        </summary>
        <ul className="mt-1 space-y-0.5">
          {result.notebooks.map((nb) => (
            <li key={nb.notebookName} className="flex items-center gap-2">
              <span
                className={
                  nb.status === 'failed'
                    ? 'text-[var(--neon-red)]'
                    : 'text-[var(--text-muted)]'
                }
              >
                {nb.status}
              </span>
              {nb.status === 'failed' ? (
                <span className="text-[var(--text-secondary)] break-words">
                  {nb.notebookName} — {nb.error}
                </span>
              ) : (
                <a
                  href={`${window.location.origin}/projects/${encodeURIComponent(result.projectKey)}/notebooks/jupyter/${encodeURIComponent(nb.notebookName)}/`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[var(--accent)] hover:underline break-all"
                >
                  {nb.notebookName}
                </a>
              )}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
