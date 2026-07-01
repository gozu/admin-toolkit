import { useMemo } from 'react';
import { Chart } from 'react-chartjs-2';
import {
  Chart as ChartJS,
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
import { BASE_TOOLTIP_STYLE, baseLegendLabels } from '../../utils/chartConfig';
import type { AdoptionMonthPoint } from '../../types';

// First LineElement registration in the codebase (every other chart is a
// doughnut/bar/treemap). No date adapter is bundled, so the x-axis is a plain
// CategoryScale over 'YYYY-MM' buckets — discrete months, which also reads more
// honestly than an interpolated time axis for a monthly count.
ChartJS.register(
  LineElement,
  PointElement,
  BarElement,
  LinearScale,
  CategoryScale,
  Filler,
  Tooltip,
  Legend,
);

// Commit volume renders as muted violet bars BEHIND the builders line — a
// second line (dashed mint over the blue area) was near-invisible; bars keep
// the two series in separate visual channels (volume vs people).
const COMMITS_BAR = 'rgba(153, 123, 224, 0.32)';
const COMMITS_BAR_HOVER = 'rgba(153, 123, 224, 0.6)';

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
  const abbr = MONTH_ABBR[idx] ?? m ?? '';
  return `${abbr} '${(y ?? '').slice(2)}`;
}

export function AdoptionTrendChart({ points }: { points: AdoptionMonthPoint[] }) {
  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const tickColor = isDark ? 'rgba(160,160,176,0.85)' : 'rgba(60,60,80,0.7)';
  const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';

  const chartData = useMemo(
    () => ({
      labels: points.map((p) => p.month),
      datasets: [
        {
          type: 'line' as const,
          label: 'Active builders',
          data: points.map((p) => p.activeBuilders),
          borderColor: CHART_PALETTE.blueBorder,
          backgroundColor: 'rgba(109, 163, 224, 0.16)',
          fill: 'origin' as const,
          tension: 0.35,
          borderWidth: 2,
          pointRadius: points.length > 24 ? 0 : 2,
          pointHoverRadius: 4,
          yAxisID: 'y',
          order: 0, // drawn last — always on top of the bars
        },
        {
          type: 'bar' as const,
          label: 'Commits',
          data: points.map((p) => p.commits),
          backgroundColor: COMMITS_BAR,
          hoverBackgroundColor: COMMITS_BAR_HOVER,
          borderWidth: 0,
          borderRadius: 2,
          barPercentage: 0.85,
          categoryPercentage: 0.95,
          yAxisID: 'y1',
          order: 2,
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
        legend: { position: 'bottom' as const, labels: baseLegendLabels() },
        tooltip: {
          ...BASE_TOOLTIP_STYLE,
          callbacks: {
            title: (items: TooltipItem<'line' | 'bar'>[]) =>
              items.length ? monthLabel(String(items[0].label)) : '',
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
          title: { display: true, text: 'Active builders', color: tickColor, font: { size: 10 } },
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
