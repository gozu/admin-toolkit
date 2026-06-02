/**
 * Shared tone helpers for the K8S Insights views.
 *
 * Used by K8sNodeDetail (per-pod / per-node usage chips) and
 * K8sFindingEvidence (per-field highlight). The thresholds match
 * those documented in docs/ui-ux-contracts.md — keep in sync.
 */

export type Tone = 'red' | 'yellow' | 'green' | null;

/** Percent usage: >85 = red (hot), <15 = yellow (over-provisioned), else null. */
export function pctTone(v: unknown): Tone {
  const n = typeof v === 'number' ? v : Number(v);
  if (!Number.isFinite(n)) return null;
  if (n > 85) return 'red';
  if (n < 15) return 'yellow';
  return null;
}

/**
 * Actual-vs-requested ratio: red when actual < 20% (way over-requested)
 * OR actual > 200% (under-requested, OOM risk). When either side is
 * null/zero, we can't compute a meaningful ratio — return null.
 */
export function ratioTone(actual: unknown, requested: unknown): Tone {
  const a = typeof actual === 'number' ? actual : Number(actual);
  const r = typeof requested === 'number' ? requested : Number(requested);
  if (!Number.isFinite(a) || !Number.isFinite(r) || r <= 0) return null;
  const ratio = a / r;
  if (ratio < 0.2) return 'red';
  if (ratio > 2.0) return 'red';
  return null;
}

/** Pod phase → tone. Running green, Pending yellow, Failed/Unknown red. */
export function phaseTone(phase: string | null | undefined): Tone {
  switch (phase) {
    case 'Running':
    case 'Succeeded':
      return 'green';
    case 'Pending':
      return 'yellow';
    case 'Failed':
    case 'Unknown':
      return 'red';
    default:
      return null;
  }
}

/** Tailwind class map for tone → text colour. */
export const TONE_TEXT: Record<NonNullable<Tone>, string> = {
  red: 'text-red-300',
  yellow: 'text-yellow-300',
  green: 'text-emerald-300',
};

/** Tailwind class map for tone → background tint (for chips). */
export const TONE_BG: Record<NonNullable<Tone>, string> = {
  red: 'bg-red-500/15 border-red-500/40 text-red-200',
  yellow: 'bg-yellow-400/15 border-yellow-500/40 text-yellow-200',
  green: 'bg-emerald-500/15 border-emerald-500/40 text-emerald-200',
};

/** Tailwind class map for tone → solid bar fill (usage bars). */
export const TONE_BAR: Record<NonNullable<Tone>, string> = {
  red: 'bg-red-500/70',
  yellow: 'bg-yellow-400/70',
  green: 'bg-emerald-500/70',
};
