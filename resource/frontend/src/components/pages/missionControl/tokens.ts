// Shared non-component tokens for the Mission Control wall (kept out of the
// component files so react-refresh sees component-only modules).

export type Tone = 'ok' | 'warn' | 'crit' | 'info' | 'neutral';

export const TONE_COLOR: Record<Tone, string> = {
  ok: 'var(--neon-green)',
  warn: 'var(--neon-amber)',
  crit: 'var(--neon-red)',
  info: 'var(--accent)',
  neutral: 'var(--text-tertiary)',
};

// Categorical palette — theme-scoped CSS vars defined in viz.css, validated
// per mode against the real surfaces (dataviz six-checks). Fixed slot order is
// the CVD-safety mechanism: assign in order, never cycle past 6 — fold the
// tail into "other".
export const CATEGORICAL_COLORS = [
  'var(--viz-cat-1)', // blue
  'var(--viz-cat-2)', // aqua
  'var(--viz-cat-3)', // yellow
  'var(--viz-cat-4)', // green
  'var(--viz-cat-5)', // violet
  'var(--viz-cat-6)', // red
];

// Tile entrance variants — names are inherited from the page-level stagger
// container.
export const TILE_VARIANTS = {
  hidden: { opacity: 0, y: 8, scale: 0.985 },
  show: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { duration: 0.35, ease: [0.16, 1, 0.3, 1] as [number, number, number, number] },
  },
};
