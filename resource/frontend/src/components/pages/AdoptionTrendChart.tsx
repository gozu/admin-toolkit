import { useMemo } from 'react';
import { Line } from 'react-chartjs-2';
import {
  Chart as ChartJS,
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
import type { AdoptionMonthPoint } from '../../types';

// First LineElement registration in the codebase (every other chart is a
// doughnut/bar/treemap). No date adapter is bundled, so the x-axis is a plain
// CategoryScale over 'YYYY-MM' buckets — discrete months, which also reads more
// honestly than an interpolated time axis for a monthly count.
ChartJS.register(LineElement, PointElement, LinearScale, CategoryScale, Filler, Tooltip, Legend);

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
          label: 'Active builders',
          data: points.map((p) => p.activeBuilders),
          borderColor: CHART_PALETTE.blueBorder,
          backgroundColor: CHART_PALETTE.blue,
          fill: 'origin' as const,
          tension: 0.35,
          borderWidth: 2,
          pointRadius: points.length > 24 ? 0 : 2,
          pointHoverRadius: 4,
          yAxisID: 'y',
          order: 2,
        },
        {
          label: 'Commits',
          data: points.map((p) => p.commits),
          borderColor: CHART_PALETTE.mintBorder,
          backgroundColor: 'transparent',
          fill: false,
          tension: 0.35,
          borderWidth: 1.5,
          borderDash: [4, 3],
          pointRadius: 0,
          pointHoverRadius: 3,
          yAxisID: 'y1',
          order: 1,
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
            title: (items: TooltipItem<'line'>[]) =>
              items.length ? monthLabel(String(items[0].label)) : '',
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
      <Line data={chartData} options={options} />
    </div>
  );
}
