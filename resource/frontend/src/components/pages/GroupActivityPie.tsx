import { useMemo } from 'react';
import { Doughnut } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  ArcElement,
  Tooltip,
  Legend,
  type Plugin,
  type TooltipItem,
} from 'chart.js';
import { BASE_TOOLTIP_STYLE, baseLegendLabels } from '../../utils/chartConfig';
import { anonText } from '../../utils/anonymize';

ChartJS.register(ArcElement, Tooltip, Legend);

// Most-active-groups ring — commit share per DSS group. Named slices wear the
// validated --viz-cat slots in fixed order; the tail folds into a grey "Other"
// slice. The fold cap lives with the caller (4 named): in a ring the last
// slice wraps around to touch the first, and violet↔blue is the one viz-cat
// pair that fails the CVD check — grey between green and the wrap back to
// blue keeps every slice adjacency validated. Legend + tooltips carry
// identity, so color is never the only channel.

export interface GroupPieSlice {
  label: string;
  /** Commits — sets the arc size. */
  value: number;
  /** Tooltip body lines under the group-name title. */
  lines: string[];
  /** Grey catch-all slice ("Other") — recedes; red stays reserved. */
  muted?: boolean;
}

const VIZ_SLOTS = ['--viz-cat-1', '--viz-cat-2', '--viz-cat-3', '--viz-cat-4', '--viz-cat-5'];

export function GroupActivityPie({
  slices,
  centerValue,
  centerLabel,
}: {
  slices: GroupPieSlice[];
  centerValue: string;
  centerLabel: string;
}) {
  // Canvas can't resolve var() — read the computed slot values (theme-aware:
  // a theme flip re-renders the page, so these re-resolve).
  const rootStyle = getComputedStyle(document.documentElement);
  const palette = VIZ_SLOTS.map((name) => rootStyle.getPropertyValue(name).trim());
  const grey = rootStyle.getPropertyValue('--text-tertiary').trim();
  let hue = 0;
  const colors = slices.map((s) => (s.muted ? grey : palette[Math.min(hue++, VIZ_SLOTS.length - 1)]));

  // Canvas text is out of the DOM rewriter's reach — alias group names here
  // (identity function while screenshot mode is off).
  const chartData = {
    labels: slices.map((s) => {
      const label = anonText(s.label);
      return label.length > 22 ? `${label.slice(0, 19)}…` : label;
    }),
    datasets: [
      {
        data: slices.map((s) => s.value),
        backgroundColor: colors,
        // Transparent 2px border = the surface gap between adjacent slices.
        borderColor: 'transparent',
        borderWidth: 2,
        hoverOffset: 8,
      },
    ],
  };

  const centerText: Plugin<'doughnut'> = useMemo(
    () => ({
      id: 'groupPieCenterText',
      afterDraw(chart) {
        const { ctx, chartArea } = chart;
        const themeAttr = document.documentElement.getAttribute('data-theme');
        const isDark = themeAttr !== 'light';
        const centerX = (chartArea.left + chartArea.right) / 2;
        const centerY = (chartArea.top + chartArea.bottom) / 2;

        ctx.save();
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';

        ctx.font = 'bold 22px "JetBrains Mono", monospace';
        ctx.fillStyle = isDark ? '#ffffff' : '#1a1a2e';
        if (themeAttr === 'dark') {
          // Glow is regular dark's personality only — dss-dark stays flat.
          ctx.shadowColor = 'rgba(0, 168, 157, 0.4)';
          ctx.shadowBlur = 8;
        }
        ctx.fillText(centerValue, centerX, centerY - 8);

        ctx.shadowBlur = 0;
        ctx.font = '11px "JetBrains Mono", monospace';
        ctx.fillStyle = isDark ? 'rgba(255,255,255,0.5)' : 'rgba(0,0,0,0.45)';
        ctx.fillText(centerLabel, centerX, centerY + 14);

        ctx.restore();
      },
    }),
    [centerValue, centerLabel],
  );

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    cutout: '62%',
    plugins: {
      legend: {
        position: 'right' as const,
        labels: baseLegendLabels({
          padding: 10,
          font: { size: 11, family: "'JetBrains Mono', monospace" },
        }),
      },
      tooltip: {
        ...BASE_TOOLTIP_STYLE,
        displayColors: false,
        callbacks: {
          title: (items: TooltipItem<'doughnut'>[]) =>
            items.length ? anonText(slices[items[0].dataIndex]?.label ?? '') : '',
          label: (ctx: TooltipItem<'doughnut'>) =>
            (slices[ctx.dataIndex]?.lines ?? []).map(anonText),
        },
      },
    },
  };

  return (
    <div style={{ height: '280px' }}>
      <Doughnut data={chartData} options={options} plugins={[centerText]} />
    </div>
  );
}
