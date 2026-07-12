import { useMemo } from 'react';
import { Chart } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  LineController,
  LineElement,
  PointElement,
  LinearScale,
  CategoryScale,
  Filler,
  Tooltip,
  Legend,
  type TooltipItem,
} from 'chart.js';
import { CHART_PALETTE } from '../../utils/chartColors';
import { BASE_TOOLTIP_STYLE, baseLegendLabels } from '../../utils/chartConfig';

// Flagship: cumulative "only goes up" adoption lines — people who ever built,
// projects ever touched, total commits. A partial month on a cumulative line
// is just the last point mid-climb (never a fake decline), so the current
// month is plotted honestly, unlike the rate charts elsewhere on the page.
// No date adapter is bundled — plain CategoryScale over 'YYYY-MM' buckets.
ChartJS.register(
  LineController,
  LineElement,
  PointElement,
  LinearScale,
  CategoryScale,
  Filler,
  Tooltip,
  Legend,
);

const COMMITS_LINE = 'rgba(153, 123, 224, 0.9)';
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

export function CumulativeAdoptionChart({ points }: { points: CumulativePoint[] }) {
  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const tickColor = isDark ? 'rgba(160,160,176,0.85)' : 'rgba(60,60,80,0.7)';
  const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';

  const chartData = useMemo(() => {
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
        },
        {
          ...lineBase,
          label: 'Projects ever active',
          data: points.map((p) => p.projects),
          borderColor: PROJECTS_LINE,
          yAxisID: 'y',
          order: 1,
        },
        {
          ...lineBase,
          label: 'Total commits',
          data: points.map((p) => p.commits),
          borderColor: COMMITS_LINE,
          borderDash: [5, 3],
          yAxisID: 'y1',
          order: 2,
        },
      ],
    };
  }, [points]);

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
            title: (items: TooltipItem<'line'>[]) =>
              items.length ? `${monthLabel(String(items[0].label))} · running totals` : '',
            label: (ctx: TooltipItem<'line'>) => `${ctx.dataset.label}: ${ctx.formattedValue}`,
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
              return monthLabel(points[index]?.month ?? '');
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
          title: { display: true, text: 'People / projects', color: tickColor, font: { size: 10 } },
        },
        y1: {
          beginAtZero: true,
          position: 'right' as const,
          grid: { drawOnChartArea: false },
          ticks: {
            color: tickColor,
            precision: 0,
            font: { size: 10, family: "'JetBrains Mono', monospace" },
          },
          title: { display: true, text: 'Commits', color: tickColor, font: { size: 10 } },
        },
      },
    }),
    [points, tickColor, gridColor],
  );

  if (points.length === 0) {
    return (
      <div className="flex h-[300px] items-center justify-center text-sm text-[var(--text-muted)]">
        No git history yet.
      </div>
    );
  }

  return (
    <div className="chart-body" style={{ height: '300px' }}>
      <Chart type="line" data={chartData} options={options} />
    </div>
  );
}
