import { useMemo } from 'react';
import { Chart } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  LineController,
  LineElement,
  PointElement,
  LinearScale,
  CategoryScale,
  Tooltip,
  Legend,
  type TooltipItem,
  type ScriptableLineSegmentContext,
} from 'chart.js';
import { CHART_PALETTE } from '../../utils/chartColors';
import { BASE_TOOLTIP_STYLE, baseLegendLabels, lineTraceAnimation } from '../../utils/chartConfig';
import { quarterLabel } from '../../utils/inventoryData';

// Onboarding & activation on ONE trimester axis: white line = new accounts
// per quarter (persistent user snapshot), directional line = median days from
// signup to first surviving build for that signup cohort. Complete
// quarters only — the running quarter is footnoted by the caller, never
// plotted. Quarters where TTFB isn't measurable leave a gap (spanGaps joins
// across them, dashed by the sparse data itself).
ChartJS.register(
  LineController,
  LineElement,
  PointElement,
  LinearScale,
  CategoryScale,
  Tooltip,
  Legend,
);

// Neutral fallback for the TTFB line: legend swatch, flat segments, and
// spanGaps joins. Direction is painted per segment — green when the median
// drops (faster onboarding = the win), red when it climbs (a warning use of
// red, the only one this page allows).
const TTFB_NEUTRAL = 'rgba(148, 148, 166, 0.9)';

export interface OnboardingQuarterPoint {
  quarter: string; // 'YYYY-Qn'
  newUsers: number;
  /** Median days to first surviving build for this signup cohort, when measurable. */
  medianDays: number | null;
  /** Cohort users with a measurable first build (tooltip denominator). */
  builders: number;
}

export function OnboardingChart({
  points,
  showTtfb,
}: {
  points: OnboardingQuarterPoint[];
  /** Line hidden below the page's minimum measured-user sample. */
  showTtfb: boolean;
}) {
  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const tickColor = isDark ? 'rgba(160,160,176,0.85)' : 'rgba(60,60,80,0.7)';
  const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';
  // "White" tracks the theme's ink — pure white vanishes on the light canvas.
  const accountsLine = isDark ? 'rgba(255,255,255,0.92)' : 'rgba(35,35,50,0.9)';

  const chartData = useMemo(() => {
    // Per-point marker colors mirror the incoming segment (walking back over
    // null quarters, same path spanGaps joins); the first measured point has
    // no direction yet, so it stays neutral.
    const pointColors: string[] = [];
    let prev: number | null = null;
    for (const p of points) {
      const v = p.medianDays;
      if (v == null) {
        pointColors.push(TTFB_NEUTRAL);
        continue;
      }
      pointColors.push(
        prev == null || v === prev
          ? TTFB_NEUTRAL
          : v < prev
            ? CHART_PALETTE.mintBorder
            : CHART_PALETTE.roseBorder,
      );
      prev = v;
    }
    return {
      labels: points.map((p) => p.quarter),
      datasets: [
        ...(showTtfb
          ? [
              {
                type: 'line' as const,
                label: 'Median days to first build (green ↓ = faster)',
                data: points.map((p) => p.medianDays),
                borderColor: TTFB_NEUTRAL,
                segment: {
                  // undefined = fall back to the neutral dataset color.
                  borderColor: (ctx: ScriptableLineSegmentContext) => {
                    const y0 = ctx.p0.parsed.y;
                    const y1 = ctx.p1.parsed.y;
                    if (y0 == null || y1 == null) return undefined;
                    return y1 < y0
                      ? CHART_PALETTE.mintBorder
                      : y1 > y0
                        ? CHART_PALETTE.roseBorder
                        : undefined;
                  },
                },
                borderWidth: 2,
                tension: 0.25,
                spanGaps: true,
                pointRadius: 3,
                pointHoverRadius: 5,
                pointBackgroundColor: pointColors,
                pointBorderColor: pointColors,
                yAxisID: 'y1',
                order: 0,
                animations: lineTraceAnimation(points.length, 'y1'),
              },
            ]
          : []),
        {
          type: 'line' as const,
          label: 'New accounts',
          data: points.map((p) => p.newUsers),
          borderColor: accountsLine,
          backgroundColor: accountsLine,
          borderWidth: 2,
          tension: 0.25,
          pointRadius: 2,
          pointHoverRadius: 4,
          pointBackgroundColor: accountsLine,
          pointBorderColor: accountsLine,
          yAxisID: 'y',
          order: 2,
          animations: lineTraceAnimation(points.length, 'y'),
        },
      ],
    };
  }, [points, showTtfb, accountsLine]);

  const options = useMemo(
    () => ({
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index' as const, intersect: false },
      plugins: {
        legend: { position: 'bottom' as const, labels: baseLegendLabels() },
        tooltip: {
          ...BASE_TOOLTIP_STYLE,
          callbacks: {
            title: (items: TooltipItem<'line'>[]) => {
              if (!items.length) return '';
              const p = points[items[0].dataIndex];
              return p
                ? `${quarterLabel(p.quarter)} · ${p.builders}/${p.newUsers} built something that survives`
                : '';
            },
            label: (ctx: TooltipItem<'line'>) =>
              ctx.parsed.y == null
                ? `${ctx.dataset.label}: not measurable`
                : `${ctx.dataset.label}: ${ctx.formattedValue}${ctx.dataset.yAxisID === 'y1' ? 'd' : ''}`,
          },
        },
      },
      scales: {
        x: {
          grid: { color: gridColor },
          ticks: {
            color: tickColor,
            maxRotation: 0,
            autoSkip: true,
            maxTicksLimit: 12,
            font: { size: 10, family: "'JetBrains Mono', monospace" },
            callback(_value: unknown, index: number) {
              return quarterLabel(points[index]?.quarter ?? '');
            },
          },
        },
        y: {
          beginAtZero: true,
          position: 'left' as const,
          grid: { color: gridColor },
          ticks: {
            color: tickColor,
            precision: 0,
            font: { size: 10, family: "'JetBrains Mono', monospace" },
          },
          title: { display: true, text: 'New accounts', color: tickColor, font: { size: 10 } },
        },
        ...(showTtfb
          ? {
              y1: {
                beginAtZero: true,
                position: 'right' as const,
                grid: { drawOnChartArea: false },
                ticks: {
                  color: tickColor,
                  precision: 0,
                  font: { size: 10, family: "'JetBrains Mono', monospace" },
                },
                title: {
                  display: true,
                  text: 'Days to first build — down is the win',
                  color: tickColor,
                  font: { size: 10 },
                },
              },
            }
          : {}),
      },
    }),
    [points, showTtfb, tickColor, gridColor],
  );

  if (points.length === 0) {
    return (
      <div className="flex h-[240px] items-center justify-center text-sm text-[var(--text-muted)]">
        No user creation dates.
      </div>
    );
  }

  return (
    <div className="chart-body" style={{ height: '240px' }}>
      <Chart type="line" data={chartData} options={options} />
    </div>
  );
}
