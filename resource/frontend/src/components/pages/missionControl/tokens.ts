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

// Muted categorical palette — mirrors the readability palette used by the
// full-page charts (ConnectionsChart) so type colors feel familiar.
export const CATEGORICAL_COLORS = [
  'rgba(109, 163, 224, 0.85)', // blue
  'rgba(153, 123, 224, 0.85)', // violet
  'rgba(99, 198, 157, 0.85)', // mint
  'rgba(224, 181, 97, 0.85)', // amber
  'rgba(224, 109, 131, 0.85)', // rose
  'rgba(101, 194, 217, 0.85)', // cyan
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
