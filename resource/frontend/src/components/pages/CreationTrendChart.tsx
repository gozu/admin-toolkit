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
  type Plugin,
  type TooltipItem,
} from 'chart.js';
import { BASE_TOOLTIP_STYLE, baseLegendLabels } from '../../utils/chartConfig';
import { TREND_GROUPS, type InventoryTrendPoint } from '../../utils/inventoryData';

// Stacked monthly "objects created" bars by family group, modeled on
// AdoptionTrendChart. This is the long persistent spine (config-tree
// creationTags span the instance's full history of surviving objects); the
// much shorter audit-log window is shaded as a background band so the two
// charts can be read against each other — the series themselves are NEVER
// merged (objects created vs audit events are different units).
ChartJS.register(BarController, BarElement, LinearScale, CategoryScale, Tooltip, Legend);

const MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function bucketLabel(key: string): string {
  const [y, m] = key.split('-');
  const idx = Number.parseInt(m ?? '', 10) - 1;
  return `${MONTH_ABBR[idx] ?? m ?? ''} '${(y ?? '').slice(2)}`;
}

function cssVar(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

const GROUP_COLOR_VARS = [
  '--viz-cat-1',
  '--viz-cat-2',
  '--viz-cat-3',
  '--viz-cat-4',
  '--viz-cat-5',
  '--viz-cat-6',
];

export function CreationTrendChart({
  points,
  auditWindow,
}: {
  points: InventoryTrendPoint[];
  /** Audit-log span (ms) to shade as a background band, when audit logs exist. */
  auditWindow?: { firstMs: number; lastMs: number } | null;
}) {
  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const tickColor = isDark ? 'rgba(160,160,176,0.85)' : 'rgba(60,60,80,0.7)';
  const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';

  const chartData = useMemo(
    () => ({
      labels: points.map((p) => p.month),
      datasets: TREND_GROUPS.map((group, gi) => ({
        type: 'bar' as const,
        label: group.label,
        data: points.map((p) => p.groups[gi] ?? 0),
        backgroundColor: cssVar(GROUP_COLOR_VARS[gi % GROUP_COLOR_VARS.length], '#888'),
        borderWidth: 0,
        borderRadius: 1,
        barPercentage: 0.9,
        categoryPercentage: 0.95,
        stack: 'created',
      })),
    }),
    [points],
  );

  // Month index range covered by the audit window (inclusive), or null when
  // no window overlaps the plotted span.
  const auditBand = useMemo(() => {
    if (!auditWindow || points.length === 0) return null;
    const firstMonth = new Date(auditWindow.firstMs).toISOString().slice(0, 7);
    const lastMonth = new Date(auditWindow.lastMs).toISOString().slice(0, 7);
    let start = -1;
    let end = -1;
    points.forEach((p, i) => {
      if (p.month >= firstMonth && p.month <= lastMonth) {
        if (start < 0) start = i;
        end = i;
      }
    });
    return start >= 0 ? { start, end } : null;
  }, [auditWindow, points]);

  const auditBandPlugin = useMemo<Plugin<'bar'>>(
    () => ({
      id: 'auditWindowBand',
      beforeDraw(chart) {
        if (!auditBand) return;
        const xScale = chart.scales.x;
        const { top, bottom } = chart.chartArea;
        if (!xScale) return;
        // Extend half a category on each side so the band covers whole bars.
        const half =
          points.length > 1
            ? (xScale.getPixelForValue(1) - xScale.getPixelForValue(0)) / 2
            : xScale.width / 2;
        const left = xScale.getPixelForValue(auditBand.start) - half;
        const right = xScale.getPixelForValue(auditBand.end) + half;
        const ctx = chart.ctx;
        ctx.save();
        ctx.fillStyle = isDark ? 'rgba(153, 123, 224, 0.10)' : 'rgba(153, 123, 224, 0.12)';
        ctx.fillRect(left, top, right - left, bottom - top);
        ctx.restore();
      },
    }),
    [auditBand, points.length, isDark],
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
              const point = points[items[0].dataIndex];
              const creators = point?.distinctCreators ?? 0;
              return `${bucketLabel(String(items[0].label))} · ${creators} ${creators === 1 ? 'creator' : 'creators'} that month`;
            },
            label: (ctx: TooltipItem<'bar'>) => `${ctx.dataset.label}: ${ctx.formattedValue}`,
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
            font: { size: 10, family: "'JetBrains Mono', monospace" },
            callback(_value: unknown, index: number) {
              return bucketLabel(points[index]?.month ?? '');
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
            font: { size: 10, family: "'JetBrains Mono', monospace" },
          },
          title: { display: true, text: 'Objects created', color: tickColor, font: { size: 10 } },
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
      <Chart type="bar" data={chartData} options={options} plugins={[auditBandPlugin]} />
    </div>
  );
}
