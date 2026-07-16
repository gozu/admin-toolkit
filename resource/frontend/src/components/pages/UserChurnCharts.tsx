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
import type { ChurnYearPoint } from '../../utils/userChurn';

// Users → Churn charts. Everything on both charts counts the same unit
// (accounts / seats), so each chart has exactly ONE y axis. Fixed color
// assignment throughout the page: created = mint (growth), disabled = rose
// (loss), reassigned / balance = blue.
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

const BAR_STYLE = {
  type: 'bar' as const,
  borderWidth: 0,
  borderRadius: 2,
  barPercentage: 0.7,
  categoryPercentage: 0.9,
  maxBarThickness: 48,
};

function useAxisColors() {
  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  return {
    tickColor: isDark ? 'rgba(160,160,176,0.85)' : 'rgba(60,60,80,0.7)',
    gridColor: isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)',
  };
}

const MONO_TICK_FONT = { size: 12, family: "'JetBrains Mono', monospace" };

export type ChurnFlowMode = 'flow' | 'cumulative';

/** Chapter 01 — account flow per calendar year: created up, disabled down
 * (mirror bars sharing the zero baseline), with the net delta as a line.
 * Cumulative mode re-plots the same series as the running account balance. */
export function UserChurnFlowChart({
  years,
  mode = 'flow',
}: {
  years: ChurnYearPoint[];
  mode?: ChurnFlowMode;
}) {
  const { tickColor, gridColor } = useAxisColors();
  const cumulative = mode === 'cumulative';

  const chartData = useMemo(() => {
    const labels = years.map((y) => String(y.year));
    if (cumulative) {
      return {
        labels,
        datasets: [
          {
            type: 'line' as const,
            label: 'Account balance (created − disabled)',
            data: years.map((y) => y.cumulative),
            borderColor: CHART_PALETTE.blueBorder,
            backgroundColor: 'rgba(109, 163, 224, 0.10)',
            fill: 'origin' as const,
            tension: 0.25,
            borderWidth: 2,
            pointRadius: years.length === 1 ? 4 : 0,
            pointHoverRadius: 4,
            animations: lineTraceAnimation(years.length, 'y'),
          },
        ],
      };
    }
    return {
      labels,
      datasets: [
        {
          ...BAR_STYLE,
          label: 'Created',
          data: years.map((y) => y.created),
          backgroundColor: CHART_PALETTE.mint,
          stack: 'flow',
          order: 1,
          animations: barGrowAnimation('y'),
        },
        {
          ...BAR_STYLE,
          label: 'Disabled',
          // Mirror bars: churn plots below the shared zero baseline.
          data: years.map((y) => -y.churned),
          backgroundColor: CHART_PALETTE.rose,
          stack: 'flow',
          order: 2,
          animations: barGrowAnimation('y'),
        },
        {
          type: 'line' as const,
          label: 'Net',
          data: years.map((y) => y.net),
          borderColor: CHART_PALETTE.blueBorder,
          tension: 0.25,
          borderWidth: 2,
          pointRadius: 3,
          pointHoverRadius: 5,
          pointBackgroundColor: CHART_PALETTE.blueBorder,
          // Own stack group so the line reads from zero, not the bar stack.
          stack: 'net',
          order: 0,
          animations: lineTraceAnimation(years.length, 'y'),
        },
      ],
    };
  }, [years, cumulative]);

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
                ? cumulative
                  ? `${items[0].label} · end-of-year balance`
                  : `${items[0].label} · account flow`
                : '',
            // Mirror bars are stored negative — always report magnitudes.
            label: (ctx: TooltipItem<'line' | 'bar'>) => {
              const y = ctx.parsed.y ?? 0;
              const direction =
                !cumulative && ctx.dataset.label === 'Net' && y !== 0
                  ? y > 0
                    ? ' up'
                    : ' down'
                  : '';
              return `${ctx.dataset.label}: ${Math.abs(y).toLocaleString()}${direction}`;
            },
          },
        },
      },
      scales: {
        x: {
          stacked: true,
          grid: { color: gridColor },
          ticks: { color: tickColor, maxRotation: 0, autoSkip: true, font: MONO_TICK_FONT },
        },
        y: {
          stacked: !cumulative,
          beginAtZero: true,
          grid: { color: gridColor },
          ticks: {
            color: tickColor,
            precision: 0,
            font: MONO_TICK_FONT,
            // The disabled mirror is negative in data, not in meaning.
            callback: (value: string | number) => Math.abs(Number(value)).toLocaleString(),
          },
          title: {
            display: true,
            text: cumulative ? 'Accounts on the instance' : 'Accounts (created ↑ / disabled ↓)',
            color: tickColor,
            font: { size: 12 },
          },
        },
      },
    }),
    [cumulative, tickColor, gridColor],
  );

  if (years.length === 0) {
    return (
      <div className="flex h-[280px] items-center justify-center text-sm text-[var(--text-muted)]">
        No dated accounts yet.
      </div>
    );
  }

  return (
    <div className="chart-body" style={{ height: '280px' }}>
      <Chart type={cumulative ? 'line' : 'bar'} data={chartData} options={options} />
    </div>
  );
}

/** Chapter 02 — where the year's new accounts got their seat: recycled from a
 * previously-freed seat of the same profile (blue) vs brand new (mint). The
 * two stack to the year's created total, so this chart reconciles with the
 * flow chart above by construction. */
export function UserChurnReassignmentChart({ years }: { years: ChurnYearPoint[] }) {
  const { tickColor, gridColor } = useAxisColors();

  const chartData = useMemo(
    () => ({
      labels: years.map((y) => String(y.year)),
      datasets: [
        {
          ...BAR_STYLE,
          label: 'Reassigned seats (est.)',
          data: years.map((y) => y.reassigned),
          backgroundColor: CHART_PALETTE.blue,
          stack: 'seats',
          order: 0,
          animations: barGrowAnimation('y'),
        },
        {
          ...BAR_STYLE,
          label: 'Brand-new seats',
          data: years.map((y) => y.fresh),
          backgroundColor: CHART_PALETTE.mint,
          stack: 'seats',
          order: 1,
          animations: barGrowAnimation('y'),
        },
      ],
    }),
    [years],
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
            title: (items: TooltipItem<'bar'>[]) =>
              items.length ? `${items[0].label} · seats handed out` : '',
            label: (ctx: TooltipItem<'bar'>) => `${ctx.dataset.label}: ${ctx.formattedValue}`,
            footer: (items: TooltipItem<'bar'>[]) => {
              const total = items.reduce((s, it) => s + (it.parsed.y ?? 0), 0);
              return total > 0 ? `Total created: ${total.toLocaleString()}` : '';
            },
          },
        },
      },
      scales: {
        x: {
          stacked: true,
          grid: { color: gridColor },
          ticks: { color: tickColor, maxRotation: 0, autoSkip: true, font: MONO_TICK_FONT },
        },
        y: {
          stacked: true,
          beginAtZero: true,
          grid: { color: gridColor },
          ticks: { color: tickColor, precision: 0, font: MONO_TICK_FONT },
          title: {
            display: true,
            text: 'Accounts created',
            color: tickColor,
            font: { size: 12 },
          },
        },
      },
    }),
    [tickColor, gridColor],
  );

  if (years.length === 0) return null;

  return (
    <div className="chart-body" style={{ height: '240px' }}>
      <Chart type="bar" data={chartData} options={options} />
    </div>
  );
}
