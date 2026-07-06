import type { CruProjectRow } from '../../../types';

// Five native-unit lenses — one per compute class the CRU parser attributes
// (local memory, local CPU, SQL engine time, K8s residency, LLM spend). Units
// are never mixed on one plot: the treemap/leaderboard resize per lens, and
// the daily strips are small multiples with their own scales.
export type CostLens = 'mem' | 'cpu' | 'sql' | 'k8s' | 'llm';

export type CostTone = 'ok' | 'warn' | 'crit' | 'neutral';

// Fixed class identity colors (viz.css categorical slots — assigned in slot
// order, never cycled; every mark using these carries a direct label).
export const LENS_COLOR: Record<CostLens, string> = {
  mem: 'var(--viz-cat-1)', // blue
  cpu: 'var(--viz-cat-2)', // aqua
  sql: 'var(--viz-cat-3)', // yellow
  k8s: 'var(--viz-cat-5)', // violet
  llm: 'var(--viz-cat-4)', // green
};

export const LENS_META: Record<CostLens, { label: string; short: string; unit: string }> = {
  mem: { label: 'Local memory', short: 'Mem', unit: 'GB·h' },
  cpu: { label: 'Local CPU', short: 'CPU', unit: 'CPU·h' },
  sql: { label: 'SQL engine', short: 'SQL', unit: 'engine·s' },
  k8s: { label: 'Kubernetes', short: 'K8s', unit: 'GB·h' },
  llm: { label: 'LLM', short: 'LLM', unit: '$' },
};

// K8s per-project cost: census actuals when the collector saw the pods,
// falling back to request×lifetime reservations (never summed — they measure
// the same residency two ways).
export function k8sGBh(row: CruProjectRow): number {
  return Math.max(row.k8sActualGBh ?? 0, row.k8sReservedGBh ?? 0);
}

export function lensValue(row: CruProjectRow, lens: CostLens): number {
  switch (lens) {
    case 'mem':
      return row.memGBh;
    case 'cpu':
      return row.cpuH;
    case 'sql':
      return row.sqlExecS ?? 0;
    case 'k8s':
      return k8sGBh(row);
    case 'llm':
      return row.llmUSD;
  }
}

export function formatSeconds(s: number): string {
  if (s >= 3600) return `${(s / 3600).toFixed(1)} h`;
  if (s >= 60) return `${(s / 60).toFixed(1)} min`;
  return `${s.toFixed(1)} s`;
}

export function formatLens(value: number, lens: CostLens): string {
  switch (lens) {
    case 'mem':
      return `${value.toFixed(1)} GB·h`;
    case 'cpu':
      return `${value.toFixed(2)} CPU·h`;
    case 'sql':
      return formatSeconds(value);
    case 'k8s':
      return `${value.toFixed(1)} GB·h`;
    case 'llm':
      return `$${value.toFixed(4)}`;
  }
}

// Efficiency signal (CRU.md §7 idle-resource finder): memory residency held at
// near-zero CPU is the "money pit". Tone a project by its GB·h-per-CPU·h ratio.
// Projects with negligible memory are neutral (nothing to flag).
export function projectTone(row: CruProjectRow): CostTone {
  if (row.memGBh < 1) return 'neutral';
  const ratio = row.memGBh / Math.max(row.cpuH, 0.001);
  if (ratio >= 200) return 'crit';
  if (ratio >= 50) return 'warn';
  return 'ok';
}
