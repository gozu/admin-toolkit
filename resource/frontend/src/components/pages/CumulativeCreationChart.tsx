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
} from 'chart.js';
import { BASE_TOOLTIP_STYLE, baseLegendLabels } from '../../utils/chartConfig';
import {
  TREND_GROUPS,
  TREND_GROUP_COLORS,
  type InventoryTrendPoint,
} from '../../utils/inventoryData';

// "What gets built here" as racing cumulative lines — one per family group,
// same slot colors as everywhere else. Cumulative curves only go up, so the
// in-progress month is plotted honestly (it's the last point mid-climb).
// Config-tree provenance: surviving tagged objects only.
ChartJS.register(
  LineController,
  LineElement,
  PointElement,
  LinearScale,
  CategoryScale,
  Tooltip,
  Legend,
);

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

export function CumulativeCreationChart({ points }: { points: InventoryTrendPoint[] }) {
  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const tickColor = isDark ? 'rgba(160,160,176,0.85)' : 'rgba(60,60,80,0.7)';
  const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';

  const chartData = useMemo(() => {
    // Running totals per family group across the whole plotted span.
    const running = TREND_GROUPS.map(() => 0);
    const series: number[][] = TREND_GROUPS.map(() => []);
    for (const p of points) {
      TREND_GROUPS.forEach((_, gi) => {
        running[gi] += p.groups[gi] ?? 0;
        series[gi].push(running[gi]);
      });
    }
    return {
      labels: points.map((p) => p.month),
      datasets: TREND_GROUPS.map((group, gi) => ({
        type: 'line' as const,
        label: group.label,
        data: series[gi],
        borderColor: resolveVar(TREND_GROUP_COLORS[gi], '#888'),
        borderWidth: 2,
        tension: 0.2,
        pointRadius: 0,
        pointHoverRadius: 4,
      })),
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
              items.length ? `${monthLabel(String(items[0].label))} · built so far` : '',
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
          grid: { color: gridColor },
          ticks: {
            color: tickColor,
            precision: 0,
            font: { size: 10, family: "'JetBrains Mono', monospace" },
          },
          title: {
            display: true,
            text: 'Objects built (cumulative)',
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
      <div className="flex h-[300px] items-center justify-center text-sm text-[var(--text-muted)]">
        No creation history found in the config tree.
      </div>
    );
  }

  return (
    <div className="chart-body" style={{ height: '300px' }}>
      <Chart type="line" data={chartData} options={options} />
    </div>
  );
}
