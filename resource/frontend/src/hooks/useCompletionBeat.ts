import { useEffect, useState } from 'react';
import type { Lifecycle } from '../types';

/** A presentation-only landing. Cached results, failures and new runs never celebrate. */
export function useCompletionBeat(lifecycle: Lifecycle) {
  const run = lifecycle.startedAt;
  const [seen, setSeen] = useState({ phase: lifecycle.phase, run, finishing: false });
  if (seen.phase !== lifecycle.phase || seen.run !== run) {
    setSeen({
      phase: lifecycle.phase,
      run,
      finishing: seen.phase === 'running' && lifecycle.phase === 'done' && seen.run === run,
    });
  }
  useEffect(() => {
    if (!seen.finishing) return;
    const timer = window.setTimeout(() => setSeen((s) => ({ ...s, finishing: false })), 760);
    return () => window.clearTimeout(timer);
  }, [seen.finishing]);
  return seen.finishing;
}
