import { useMemo } from 'react';
import { Chart } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  LineController,
  BarController,
  LineElement,
  PointElement,
  BarElement,
  LinearScale,
  CategoryScale,
  Tooltip,
  Legend,
  type TooltipItem,
} from 'chart.js';
import { CHART_PALETTE } from '../../utils/chartColors';
import { BASE_TOOLTIP_STYLE, baseLegendLabels } from '../../utils/chartConfig';
import { quarterLabel } from '../../utils/inventoryData';

// Onboarding & activation on ONE trimester axis: bars = new accounts per
// quarter (persistent user snapshot), line = median days from signup to first
// surviving build for that signup cohort (config-tree provenance). Complete
// quarters only — the running quarter is footnoted by the caller, never
// plotted. Quarters where TTFB isn't measurable leave a gap (spanGaps joins
// across them, dashed by the sparse data itself).
ChartJS.register(
  LineController,
  BarController,
  LineElement,
  PointElement,
  BarElement,
  LinearScale,
  CategoryScale,
  Tooltip,
  Legend,
);

const TTFB_LINE = 'rgba(153, 123, 224, 0.95)';

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

  const chartData = useMemo(
    () => ({
      labels: points.map((p) => p.quarter),
      datasets: [
        ...(showTtfb
          ? [
              {
                type: 'line' as const,
                label: 'Median days to first build (lower = better)',
                data: points.map((p) => p.medianDays),
                borderColor: TTFB_LINE,
                borderWidth: 2,
                tension: 0.25,
                spanGaps: true,
                pointRadius: 3,
                pointHoverRadius: 5,
                pointBackgroundColor: TTFB_LINE,
                yAxisID: 'y1',
                order: 0,
              },
            ]
          : []),
        {
          type: 'bar' as const,
          label: 'New accounts',
          data: points.map((p) => p.newUsers),
          backgroundColor: CHART_PALETTE.blue,
          borderWidth: 0,
          borderRadius: 2,
          barPercentage: 0.7,
          categoryPercentage: 0.9,
          maxBarThickness: 48,
          yAxisID: 'y',
          order: 2,
        },
      ],
    }),
    [points, showTtfb],
  );

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
            title: (items: TooltipItem<'line' | 'bar'>[]) => {
              if (!items.length) return '';
              const p = points[items[0].dataIndex];
              return p
                ? `${quarterLabel(p.quarter)} · ${p.builders}/${p.newUsers} built something that survives`
                : '';
            },
            label: (ctx: TooltipItem<'line' | 'bar'>) =>
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
      <Chart type="bar" data={chartData} options={options} />
    </div>
  );
}
