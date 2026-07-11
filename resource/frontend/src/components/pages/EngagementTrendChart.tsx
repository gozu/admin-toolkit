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
  Filler,
  Tooltip,
  type TooltipItem,
} from 'chart.js';
import { CHART_PALETTE } from '../../utils/chartColors';
import { BASE_TOOLTIP_STYLE } from '../../utils/chartConfig';
import type { AdoptionMonthPoint } from '../../types';

// Flagship engagement view: RATE panels, not cumulative vanity curves. One
// canvas, two vertically stacked axes sharing the x axis (chart.js scale
// stacking), so the panels can never drift out of alignment:
//   top    — distinct active builders per complete month (bars)
//   bottom — human commit volume per complete month (filled line)
// Same-direction movement is visible without a dual axis inviting false
// unit comparisons; a decline is visible, which cumulative lines structurally
// hide. Complete months only — the caller pre-drops the running month.
ChartJS.register(
  LineController,
  BarController,
  LineElement,
  PointElement,
  BarElement,
  LinearScale,
  CategoryScale,
  Filler,
  Tooltip,
);

const COMMITS_LINE = 'rgba(153, 123, 224, 0.9)';
const COMMITS_FILL = 'rgba(153, 123, 224, 0.14)';

const MONTH_ABBR = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

function monthLabel(ym: string): string {
  const [y, m] = ym.split('-');
  const idx = Number.parseInt(m ?? '', 10) - 1;
  return `${MONTH_ABBR[idx] ?? m ?? ''} '${(y ?? '').slice(2)}`;
}

export function EngagementTrendChart({ points }: { points: AdoptionMonthPoint[] }) {
  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const tickColor = isDark ? 'rgba(170,170,186,0.9)' : 'rgba(60,60,80,0.75)';
  const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';

  const chartData = useMemo(
    () => ({
      labels: points.map((p) => p.month),
      datasets: [
        {
          type: 'bar' as const,
          label: 'Active builders',
          data: points.map((p) => p.activeBuilders),
          backgroundColor: CHART_PALETTE.blue,
          borderWidth: 0,
          borderRadius: 2,
          maxBarThickness: 22,
          yAxisID: 'y',
        },
        {
          type: 'line' as const,
          label: 'Commits',
          data: points.map((p) => p.commits),
          borderColor: COMMITS_LINE,
          backgroundColor: COMMITS_FILL,
          fill: 'origin' as const,
          borderWidth: 1.5,
          tension: 0.25,
          pointRadius: 0,
          pointHoverRadius: 4,
          yAxisID: 'y2',
        },
      ],
    }),
    [points],
  );

  const options = useMemo(
    () => ({
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index' as const, intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          ...BASE_TOOLTIP_STYLE,
          callbacks: {
            title: (items: TooltipItem<'line' | 'bar'>[]) =>
              items.length ? monthLabel(String(items[0].label)) : '',
            label: (ctx: TooltipItem<'line' | 'bar'>) =>
              ctx.datasetIndex === 0
                ? `${ctx.formattedValue} ${ctx.parsed.y === 1 ? 'person' : 'people'} built this month`
                : `${ctx.formattedValue} commits`,
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
            maxTicksLimit: 14,
            font: { size: 11, family: "'JetBrains Mono', monospace" },
            callback(_value: unknown, index: number) {
              return monthLabel(points[index]?.month ?? '');
            },
          },
        },
        // Two stacked panels on one canvas — the x axis is shared by
        // construction, so the panels can never misalign. Scale stacking
        // places later-defined axes ABOVE earlier ones, so commits (context)
        // is declared first and people (headline) second → people on top.
        y2: {
          stack: 'panels',
          stackWeight: 2,
          offset: true,
          beginAtZero: true,
          grid: { color: gridColor },
          ticks: {
            color: tickColor,
            precision: 0,
            maxTicksLimit: 4,
            font: { size: 11, family: "'JetBrains Mono', monospace" },
          },
          title: { display: true, text: 'Commits', color: tickColor, font: { size: 10 } },
        },
        y: {
          stack: 'panels',
          stackWeight: 3,
          offset: true,
          beginAtZero: true,
          grid: { color: gridColor },
          ticks: {
            color: tickColor,
            precision: 0,
            maxTicksLimit: 5,
            font: { size: 11, family: "'JetBrains Mono', monospace" },
          },
          title: { display: true, text: 'People building', color: tickColor, font: { size: 10 } },
        },
      },
    }),
    [points, tickColor, gridColor],
  );

  if (points.length === 0) {
    return (
      <div className="flex h-[320px] items-center justify-center text-sm text-[var(--text-muted)]">
        No complete months of git history yet.
      </div>
    );
  }

  return (
    <div className="chart-body" style={{ height: '320px' }}>
      <Chart type="bar" data={chartData} options={options} />
    </div>
  );
}
