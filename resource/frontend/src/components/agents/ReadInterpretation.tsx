import { useEffect, useMemo, useState } from 'react';
import { fetchJson } from '../../utils/api';
import { ProgressIndicator } from '../common/ProgressIndicator';
import type { Lifecycle } from '../../types';

interface Job {
  status: string;
  turnId?: string;
  viewTicket?: string;
  summary?: string;
  message?: string;
}
interface Route {
  host: string;
  submittedAt: number;
  returnSeconds: number;
  interpretation: Job;
}

/** Component-local state is discarded with the session's transcript. Polling
 * explicitly pins the originating host and aborts on unmount/host switch. */
export function ReadInterpretation({
  name,
  result,
}: {
  name: string;
  result: Record<string, unknown>;
}) {
  const route = result.executionRoute as Route;
  const [job, setJob] = useState<Job>(route.interpretation);
  const [lifecycle, setLifecycle] = useState<Lifecycle>({
    phase: 'queued',
    message: 'Cobuild interpretation queued',
  });
  const raw = useMemo(() => {
    const { executionRoute: _route, ...data } = result;
    return JSON.stringify(data, null, 2);
  }, [result]);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const startedAt = new Date(route.submittedAt * 1000).toISOString();
    function settle(next: Job) {
      if (controller.signal.aborted) return;
      setJob(next);
      const finishedAt = new Date().toISOString();
      if (next.status === 'completed') {
        setLifecycle({
          phase: 'done',
          startedAt,
          finishedAt,
          isEmpty: false,
          message: 'Cobuild interpretation ready',
        });
      } else if (next.status === 'pending') {
        setLifecycle({
          phase: 'running',
          startedAt,
          updatedAt: finishedAt,
          progressPct: 0,
          message: 'Cobuild interpreting…',
        });
      } else if (next.status === 'unknown') {
        setLifecycle({ phase: 'queued', message: 'Cobuild interpretation unavailable or expired' });
      } else {
        setLifecycle({
          phase: 'error',
          startedAt,
          finishedAt,
          progressPct: 0,
          error: next.message || 'Cobuild interpretation failed',
        });
      }
    }
    async function poll() {
      try {
        if (Date.now() - route.submittedAt * 1000 > 240_000) {
          settle({
            status: 'unknown',
            message: 'Interpretation wait expired; the read result remains available.',
          });
          return;
        }
        const next = await fetchJson<Job>('/api/agents/cobuild-read-status', {
          method: 'POST',
          signal: controller.signal,
          headers: { 'Content-Type': 'application/json', 'X-DSS-Host-Id': route.host },
          body: JSON.stringify({
            turnId: route.interpretation.turnId,
            viewTicket: route.interpretation.viewTicket,
          }),
        });
        settle(next);
        if (!controller.signal.aborted && next.status === 'pending')
          timer = setTimeout(() => void poll(), 1000);
      } catch {
        settle({
          status: 'failed',
          message: 'Could not retrieve the Cobuild interpretation. Read data is retained.',
        });
      }
    }
    settle(route.interpretation);
    if (route.interpretation.status === 'pending') void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [route]);
  return (
    <div className="my-1 max-w-[60rem] rounded border border-[var(--border-default)] px-3 py-2 text-xs">
      <div className="flex items-center justify-between gap-3 text-[var(--text-secondary)]">
        <span>
          {name} ·{' '}
          {result.error
            ? 'Read returned an error'
            : result.truncated || result.partial
              ? 'Partial data ready'
              : 'Data ready'}
        </span>
        <span className="tabular-nums">{route.returnSeconds.toFixed(2)}s</span>
      </div>
      <div className="min-h-6 py-1">
        <ProgressIndicator lifecycle={lifecycle} compact />
      </div>
      {job.status === 'completed' && (
        <p className="py-1 text-[var(--text-primary)]">{job.summary}</p>
      )}
      <details className="text-[var(--text-muted)]">
        <summary className="cursor-pointer">Read data</summary>
        <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap">{raw}</pre>
      </details>
    </div>
  );
}
