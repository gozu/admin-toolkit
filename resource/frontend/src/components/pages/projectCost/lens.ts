import type { CruProjectRow } from '../../../types';

// The three v1 native-unit lenses. SQL (connection-join) and K8s (pod census)
// are an explicit fast-follow — see CRU.md §5 and the module plan.
export type CostLens = 'mem' | 'cpu' | 'llm';

export type CostTone = 'ok' | 'warn' | 'crit' | 'neutral';

export const LENS_META: Record<CostLens, { label: string; short: string; unit: string }> = {
  mem: { label: 'Memory', short: 'Mem', unit: 'GB·h' },
  cpu: { label: 'CPU', short: 'CPU', unit: 'CPU·h' },
  llm: { label: 'LLM', short: 'LLM', unit: '$' },
};

export function lensValue(row: CruProjectRow, lens: CostLens): number {
  switch (lens) {
    case 'mem':
      return row.memGBh;
    case 'cpu':
      return row.cpuH;
    case 'llm':
      return row.llmUSD;
  }
}

export function formatLens(value: number, lens: CostLens): string {
  switch (lens) {
    case 'mem':
      return `${value.toFixed(1)} GB·h`;
    case 'cpu':
      return `${value.toFixed(2)} CPU·h`;
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
