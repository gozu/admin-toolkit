import { Chart, type Plugin } from 'chart.js';

/** Shared Chart.js tooltip styling used across chart components */
export const BASE_TOOLTIP_STYLE = {
  backgroundColor: 'rgba(22, 28, 36, 0.95)',
  titleFont: { size: 13, family: "'Roboto', sans-serif" },
  bodyFont: { size: 12, family: "'JetBrains Mono', monospace" },
  padding: 12,
  cornerRadius: 8,
  borderColor: 'rgba(107, 167, 210, 0.5)',
  borderWidth: 1,
  titleColor: '#f0f0f5',
  bodyColor: '#a0a0b0',
} as const;

/** Build legend label config with optional overrides */
export function baseLegendLabels(overrides?: Record<string, unknown>) {
  return {
    padding: 16,
    usePointStyle: true,
    pointStyle: 'circle' as const,
    font: { size: 12, family: "'JetBrains Mono', monospace" },
    color: '#a0a0b0',
    ...overrides,
  };
}

/** Animation-callback context (chart.js passes a richer object than the
 * Scriptable typings admit — per-element `index`, mutable started flags). */
interface TraceAnimationCtx {
  type: string;
  index: number;
  datasetIndex: number;
  chart: Chart;
  xStarted?: boolean;
  yStarted?: boolean;
}

const TRACE_TOTAL_MS = 900;

/** Left-to-right trace reveal for a line dataset (the chart.js progressive
 * pattern): each point animates in after the previous one, so the line draws
 * itself across the chart. Attach per-dataset (`animations:`) so co-plotted
 * bar datasets keep the default grow-in sweep. */
export function lineTraceAnimation(pointCount: number, axisId = 'y') {
  const perPoint = TRACE_TOTAL_MS / Math.max(1, pointCount);
  const previousY = (raw: unknown): number => {
    const ctx = raw as TraceAnimationCtx;
    const base = ctx.chart.scales[axisId]?.getPixelForValue(0) ?? 0;
    if (ctx.index === 0) return base;
    const prev = ctx.chart.getDatasetMeta(ctx.datasetIndex).data[ctx.index - 1];
    const y = prev?.getProps(['y'], true).y;
    return typeof y === 'number' && Number.isFinite(y) ? y : base;
  };
  const staggerDelay = (flag: 'xStarted' | 'yStarted') => (raw: unknown) => {
    const ctx = raw as TraceAnimationCtx;
    if (ctx.type !== 'data' || ctx[flag]) return 0;
    ctx[flag] = true;
    return ctx.index * perPoint;
  };
  return {
    x: {
      type: 'number' as const,
      easing: 'linear' as const,
      duration: perPoint,
      from: Number.NaN, // point is skipped until its slot in the trace
      delay: staggerDelay('xStarted'),
    },
    y: {
      type: 'number' as const,
      easing: 'linear' as const,
      duration: perPoint,
      from: previousY,
      delay: staggerDelay('yStarted'),
    },
  };
}

/* Chart draw-in: the first render of each chart sweeps in (650ms easeOutQuart,
 * small per-bar stagger); subsequent data/theme updates use a fast 200ms
 * transition. Applied by mutating Chart.defaults so every chart component
 * inherits it without per-chart options changes; a chart that sets its own
 * `options.animation` overrides this. NOTE: mutate duration/easing/delay in
 * place — `Animations.configure` derives its per-property option list from
 * `Object.keys(defaults.animation)`, so replacing the object breaks the
 * built-in color/visibility animation specs. */

// Computed once at module load; reduced-motion users get instant rendering.
const PREFERS_REDUCED_MOTION =
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const INITIAL_SWEEP_MS = 650;
const UPDATE_MS = 200;
const BAR_STAGGER_STEP_MS = 12;
const BAR_STAGGER_CAP_MS = 400;

/** Datasets whose draw-in has played, keyed by chart type + labels signature
 * rather than Chart instance: pages unmount/remount their charts on every
 * navigation, so an instance key would replay the full sweep on each cache
 * re-display. New data (different labels) sweeps again; the Set stays tiny
 * (a handful of charts per session). */
const sweepDoneKeys = new Set<string>();
const sweepKey = (chart: Chart): string => {
  const type = 'type' in chart.config ? String(chart.config.type) : 'mixed';
  return `${type}:${(chart.data.labels ?? []).join(' ')}`;
};

const rootAnimation = Chart.defaults.animation;
if (rootAnimation) {
  if (PREFERS_REDUCED_MOTION) {
    rootAnimation.duration = 0;
  } else {
    rootAnimation.easing = 'easeOutQuart';
    rootAnimation.duration = (ctx) =>
      sweepDoneKeys.has(sweepKey(ctx.chart)) ? UPDATE_MS : INITIAL_SWEEP_MS;
    rootAnimation.delay = (ctx) => {
      if (ctx.type !== 'data' || ctx.mode !== 'default') return 0;
      if (sweepDoneKeys.has(sweepKey(ctx.chart))) return 0;
      const config = ctx.chart.config;
      // Stagger only bar charts; pies/doughnuts/treemaps sweep as one.
      if (!('type' in config) || config.type !== 'bar') return 0;
      return Math.min(ctx.dataIndex * BAR_STAGGER_STEP_MS, BAR_STAGGER_CAP_MS);
    };
    // afterUpdate fires once all element animations of an update have been
    // resolved, so even a mid-sweep streaming update transitions fast instead
    // of replaying the full sweep.
    const sweepGuard: Plugin = {
      id: 'adkChartSweepGuard',
      afterUpdate(chart) {
        sweepDoneKeys.add(sweepKey(chart));
      },
    };
    Chart.register(sweepGuard);
  }
}
