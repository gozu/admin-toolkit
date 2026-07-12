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
  Legend,
  type TooltipItem,
} from 'chart.js';
import { CHART_PALETTE } from '../../utils/chartColors';
import {
  BASE_TOOLTIP_STYLE,
  baseLegendLabels,
  barGrowAnimation,
  lineTraceAnimation,
} from '../../utils/chartConfig';
import { quarterLabel } from '../../utils/inventoryData';

// Flagship: cumulative "only goes up" adoption lines — people who ever built,
// projects ever touched, total commits. A partial month on a cumulative line
// is just the last point mid-climb (never a fake decline), so the current
// month is plotted honestly, unlike the rate charts elsewhere on the page.
// Trimester mode re-plots the SAME series as per-quarter deltas (bars) —
// complete quarters only, and "new builders" is a first-commit cohort, never
// an active-user count. No date adapter is bundled — plain CategoryScale.
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
  Legend,
);

const COMMITS_LINE = 'rgba(153, 123, 224, 0.9)';
const COMMITS_FILL = 'rgba(153, 123, 224, 0.78)';
const PROJECTS_LINE = CHART_PALETTE.mintBorder;

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

export interface CumulativePoint {
  month: string; // 'YYYY-MM'
  builders: number; // people who had built by the end of this month
  projects: number; // projects with any human commit by then
  commits: number; // total human commits by then
}

export interface QuarterlyAdoptionPoint {
  quarter: string; // 'YYYY-Qn' — complete quarters only
  newBuilders: number; // first-commit cohort: people whose FIRST commit fell here
  newProjects: number; // projects first touched this quarter
  commits: number; // commit volume this quarter
}

const BAR_STYLE = {
  type: 'bar' as const,
  borderWidth: 0,
  borderRadius: 2,
  barPercentage: 0.7,
  categoryPercentage: 0.9,
  maxBarThickness: 48,
};

export function CumulativeAdoptionChart({
  points,
  mode = 'cumulative',
  quarterly = [],
}: {
  points: CumulativePoint[];
  mode?: 'cumulative' | 'trimester';
  quarterly?: QuarterlyAdoptionPoint[];
}) {
  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const tickColor = isDark ? 'rgba(160,160,176,0.85)' : 'rgba(60,60,80,0.7)';
  const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';
  const trimester = mode === 'trimester';

  const chartData = useMemo(() => {
    if (trimester) {
      return {
        labels: quarterly.map((q) => q.quarter),
        datasets: [
          {
            ...BAR_STYLE,
            label: 'New builders (first commit)',
            data: quarterly.map((q) => q.newBuilders),
            backgroundColor: CHART_PALETTE.blue,
            yAxisID: 'y',
            order: 0,
            animations: barGrowAnimation('y'),
          },
          {
            ...BAR_STYLE,
            label: 'Newly touched projects',
            data: quarterly.map((q) => q.newProjects),
            backgroundColor: CHART_PALETTE.mint,
            yAxisID: 'y',
            order: 1,
            animations: barGrowAnimation('y'),
          },
          {
            ...BAR_STYLE,
            label: 'Commits',
            data: quarterly.map((q) => q.commits),
            backgroundColor: COMMITS_FILL,
            yAxisID: 'y1',
            order: 2,
            animations: barGrowAnimation('y1'),
          },
        ],
      };
    }
    const lineBase = {
      type: 'line' as const,
      tension: 0.25,
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 4,
    };
    return {
      labels: points.map((p) => p.month),
      datasets: [
        {
          ...lineBase,
          label: 'People who ever built',
          data: points.map((p) => p.builders),
          borderColor: CHART_PALETTE.blueBorder,
          backgroundColor: 'rgba(109, 163, 224, 0.10)',
          fill: 'origin' as const,
          yAxisID: 'y',
          order: 0,
          animations: lineTraceAnimation(points.length, 'y'),
        },
        {
          ...lineBase,
          label: 'Projects ever active',
          data: points.map((p) => p.projects),
          borderColor: PROJECTS_LINE,
          yAxisID: 'y',
          order: 1,
          animations: lineTraceAnimation(points.length, 'y'),
        },
        {
          ...lineBase,
          label: 'Total commits',
          data: points.map((p) => p.commits),
          borderColor: COMMITS_LINE,
          borderDash: [5, 3],
          yAxisID: 'y1',
          order: 2,
          animations: lineTraceAnimation(points.length, 'y1'),
        },
      ],
    };
  }, [points, quarterly, trimester]);

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
            title: (items: TooltipItem<'line' | 'bar'>[]) =>
              items.length
                ? trimester
                  ? `${quarterLabel(String(items[0].label))} · new this quarter`
                  : `${monthLabel(String(items[0].label))} · running totals`
                : '',
            label: (ctx: TooltipItem<'line' | 'bar'>) =>
              `${ctx.dataset.label}: ${ctx.formattedValue}`,
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
            font: { size: 12, family: "'JetBrains Mono', monospace" },
            callback(_value: unknown, index: number) {
              return trimester
                ? quarterLabel(quarterly[index]?.quarter ?? '')
                : monthLabel(points[index]?.month ?? '');
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
            font: { size: 12, family: "'JetBrains Mono', monospace" },
          },
          title: {
            display: true,
            text: trimester ? 'New people / projects' : 'People / projects',
            color: tickColor,
            font: { size: 12 },
          },
        },
        y1: {
          beginAtZero: true,
          position: 'right' as const,
          grid: { drawOnChartArea: false },
          ticks: {
            color: tickColor,
            precision: 0,
            font: { size: 12, family: "'JetBrains Mono', monospace" },
          },
          title: { display: true, text: 'Commits', color: tickColor, font: { size: 12 } },
        },
      },
    }),
    [points, quarterly, trimester, tickColor, gridColor],
  );

  if (trimester ? quarterly.length === 0 : points.length === 0) {
    return (
      <div className="flex h-[300px] items-center justify-center text-sm text-[var(--text-muted)]">
        {trimester ? 'No complete quarters yet.' : 'No git history yet.'}
      </div>
    );
  }

  return (
    <div className="chart-body" style={{ height: '300px' }}>
      <Chart type={trimester ? 'bar' : 'line'} data={chartData} options={options} />
    </div>
  );
}
