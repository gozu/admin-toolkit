import { useMemo } from 'react';
import { Chart } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  BarController,
  BarElement,
  LinearScale,
  CategoryScale,
  Tooltip,
  Legend,
  type TooltipItem,
} from 'chart.js';
import { BASE_TOOLTIP_STYLE, baseLegendLabels } from '../../utils/chartConfig';
import {
  TREND_GROUPS,
  TREND_GROUP_COLORS,
  type InventoryTrendPoint,
} from '../../utils/inventoryData';

// Monthly creation, one stacked bar per complete month, family-group colored —
// replaces both the flat cumulative racing lines and the six axis-less small
// multiples: one shared y axis, one legend, actual readable magnitudes.
// Config-tree provenance: surviving tagged objects only.
ChartJS.register(BarController, BarElement, LinearScale, CategoryScale, Tooltip, Legend);

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

function cssVar(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function resolveVar(value: string, fallback: string): string {
  const name = value.startsWith('var(') ? value.slice(4, -1) : value;
  return cssVar(name, fallback);
}

export function MonthlyCreationChart({ points }: { points: InventoryTrendPoint[] }) {
  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const tickColor = isDark ? 'rgba(170,170,186,0.9)' : 'rgba(60,60,80,0.75)';
  const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';

  const chartData = useMemo(
    () => ({
      labels: points.map((p) => p.month),
      datasets: TREND_GROUPS.map((group, gi) => ({
        type: 'bar' as const,
        label: group.label,
        data: points.map((p) => p.groups[gi] ?? 0),
        backgroundColor: resolveVar(TREND_GROUP_COLORS[gi], '#888'),
        borderWidth: 0,
        maxBarThickness: 30,
        stack: 'families',
      })),
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
            title: (items: TooltipItem<'bar'>[]) => {
              if (!items.length) return '';
              const p = points[items[0].dataIndex];
              return p ? `${monthLabel(p.month)} · ${p.total.toLocaleString()} objects created` : '';
            },
            label: (ctx: TooltipItem<'bar'>) =>
              (ctx.parsed.y ?? 0) > 0 ? `${ctx.dataset.label}: ${ctx.formattedValue}` : '',
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
            maxTicksLimit: 14,
            font: { size: 11, family: "'JetBrains Mono', monospace" },
            callback(_value: unknown, index: number) {
              return monthLabel(points[index]?.month ?? '');
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
          title: {
            display: true,
            text: 'Objects created',
            color: tickColor,
            font: { size: 10 },
          },
        },
      },
    }),
    [points, tickColor, gridColor],
  );

  if (points.length === 0) {
    return (
      <div className="flex h-[260px] items-center justify-center text-sm text-[var(--text-muted)]">
        No creation history found in the config tree.
      </div>
    );
  }

  return (
    <div className="chart-body" style={{ height: '260px' }}>
      <Chart type="bar" data={chartData} options={options} />
    </div>
  );
}
