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

// Builder lifecycle by quarter — the honest direction-of-travel story the
// cumulative curves could never tell. Every unit is a PERSON:
//   new       — first-ever commit fell in this quarter
//   returning — committed this quarter, had already built before
//   lapsed    — built in the previous quarter but not this one (dashed line)
// All derived from per-builder monthly git activity (full persistent span).
// Complete quarters only — the caller pre-drops the running quarter.
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

const LAPSED_LINE = 'rgba(224, 109, 131, 0.85)';

export interface LifecycleQuarterPoint {
  quarter: string; // 'YYYY-Qn'
  newBuilders: number;
  returning: number;
  lapsed: number;
}

export function BuilderLifecycleChart({ points }: { points: LifecycleQuarterPoint[] }) {
  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const tickColor = isDark ? 'rgba(170,170,186,0.9)' : 'rgba(60,60,80,0.75)';
  const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';

  const chartData = useMemo(
    () => ({
      labels: points.map((p) => p.quarter),
      datasets: [
        {
          type: 'bar' as const,
          label: 'New builders',
          data: points.map((p) => p.newBuilders),
          backgroundColor: CHART_PALETTE.mint,
          borderWidth: 0,
          borderRadius: 2,
          maxBarThickness: 28,
          stack: 'active',
          order: 2,
        },
        {
          type: 'bar' as const,
          label: 'Returning builders',
          data: points.map((p) => p.returning),
          backgroundColor: CHART_PALETTE.blue,
          borderWidth: 0,
          borderRadius: 2,
          maxBarThickness: 28,
          stack: 'active',
          order: 3,
        },
        {
          type: 'line' as const,
          label: 'Lapsed (built prev. quarter, not this one)',
          data: points.map((p) => p.lapsed),
          borderColor: LAPSED_LINE,
          borderWidth: 1.5,
          borderDash: [4, 3],
          tension: 0.25,
          pointRadius: 2,
          pointHoverRadius: 4,
          pointBackgroundColor: LAPSED_LINE,
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
            title: (items: TooltipItem<'line' | 'bar'>[]) => {
              if (!items.length) return '';
              const p = points[items[0].dataIndex];
              return p
                ? `${quarterLabel(p.quarter)} · ${p.newBuilders + p.returning} active ${p.newBuilders + p.returning === 1 ? 'builder' : 'builders'}`
                : '';
            },
            label: (ctx: TooltipItem<'line' | 'bar'>) =>
              `${ctx.dataset.label}: ${ctx.formattedValue}`,
          },
        },
      },
      scales: {
        x: {
          stacked: true,
          grid: { color: gridColor },
          ticks: {
            color: tickColor,
            maxRotation: 0,
            autoSkip: true,
            maxTicksLimit: 12,
            font: { size: 11, family: "'JetBrains Mono', monospace" },
            callback(_value: unknown, index: number) {
              return quarterLabel(points[index]?.quarter ?? '');
            },
          },
        },
        y: {
          stacked: true,
          beginAtZero: true,
          grid: { color: gridColor },
          ticks: {
            color: tickColor,
            precision: 0,
            font: { size: 11, family: "'JetBrains Mono', monospace" },
          },
          title: { display: true, text: 'People', color: tickColor, font: { size: 10 } },
        },
      },
    }),
    [points, tickColor, gridColor],
  );

  if (points.length === 0) {
    return (
      <div className="flex h-[240px] items-center justify-center text-sm text-[var(--text-muted)]">
        Not enough complete quarters yet.
      </div>
    );
  }

  return (
    <div className="chart-body" style={{ height: '240px' }}>
      <Chart type="bar" data={chartData} options={options} />
    </div>
  );
}
