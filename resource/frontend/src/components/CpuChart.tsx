import { useMemo, useSyncExternalStore } from 'react';
import { motion } from 'framer-motion';
import { Doughnut } from 'react-chartjs-2';
import { Chart as ChartJS, ArcElement, Tooltip, Legend, type TooltipItem, type Plugin } from 'chart.js';
import { useDiag } from '../context/DiagContext';
import { useTableFilter } from '../hooks/useTableFilter';
import { parseNumericValue, formatCpuCoresLong } from '../utils/formatters';
import { CHART_PALETTE } from '../utils/chartColors';
import { BASE_TOOLTIP_STYLE, baseLegendLabels } from '../utils/chartConfig';
import { getProcessMetrics, subscribeProcessMetrics } from '../state/processMetrics';

ChartJS.register(ArcElement, Tooltip, Legend);

const CHART_COLORS = {
  used: CHART_PALETTE.rose,
  usedBorder: CHART_PALETTE.roseBorder,
  idle: CHART_PALETTE.mint,
  idleBorder: CHART_PALETTE.mintBorder,
};

// Center text reads its value from options.plugins.cpuCenterText —
// react-chartjs-2 registers inline plugins only at chart CREATION, so a
// closure over props would freeze the number at its mount-time value while
// the arcs keep streaming. Options, unlike plugins, are updated reactively.
const CPU_CENTER_PLUGIN: Plugin<'doughnut'> = {
  id: 'cpuCenterText',
  afterDraw(chart) {
    const { ctx, width, height } = chart;
    const pluginOpts = (chart.options.plugins ?? {}) as Record<string, { text?: string } | undefined>;
    const text = pluginOpts['cpuCenterText']?.text;
    if (!text) return;
    const themeAttr = document.documentElement.getAttribute('data-theme');
    const isDark = themeAttr !== 'light';

    ctx.save();
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    const centerX = width / 2;
    const centerY = height / 2;

    // Main value
    ctx.font = 'bold 18px "JetBrains Mono", monospace';
    ctx.fillStyle = isDark ? '#ffffff' : '#1a1a2e';
    if (themeAttr === 'dark') {
      // Glow is regular dark's personality only — dss-dark stays flat.
      ctx.shadowColor = 'rgba(0, 168, 157, 0.4)';
      ctx.shadowBlur = 8;
    }
    ctx.fillText(text, centerX, centerY - 8);

    // Label
    ctx.shadowBlur = 0;
    ctx.font = '11px "JetBrains Mono", monospace';
    ctx.fillStyle = isDark ? 'rgba(255,255,255,0.5)' : 'rgba(0,0,0,0.45)';
    ctx.fillText('Used', centerX, centerY + 12);

    ctx.restore();
  },
};

/** Parse logical thread count from a `cpuCores` string like
 * "4 Cores / 8 Threads" (→ 8). Falls back to the leading plain number. */
function parseLogicalThreads(cpuCores: string | undefined): number {
  if (!cpuCores) return 0;
  const threadsMatch = cpuCores.match(/(\d+)\s*Threads/i);
  if (threadsMatch) return parseInt(threadsMatch[1], 10);
  const leading = parseInt(cpuCores, 10);
  if (Number.isFinite(leading) && leading > 0) return leading;
  const n = parseNumericValue(cpuCores);
  return Number.isFinite(n) ? n : 0;
}

/** CPU% (where 100 == one fully-used logical core) → cores-equivalent label. */
function formatCores(cpuPercent: number): string {
  return `${(cpuPercent / 100).toFixed(1)} cores`;
}

/**
 * Used vs Idle CPU doughnut — the CPU-page mirror of `MemoryChart`. Capacity is
 * logical threads × 100 (one core fully used == 100% CPU, as `ps` reports it);
 * "Used" is the sum of per-process %CPU from the shared process-metrics store
 * (the same store the CPU usage table drives — no extra fetch).
 */
export function CpuChart() {
  const { state } = useDiag();
  const { isVisible } = useTableFilter();
  const { parsedData } = state;
  const scan = useSyncExternalStore(subscribeProcessMetrics, getProcessMetrics, getProcessMetrics);

  const logicalThreads = parseLogicalThreads(parsedData.cpuCores);
  const capacity = logicalThreads * 100;

  const { used, idle } = useMemo(() => {
    const usedPct = scan.processes.reduce((sum, p) => sum + p.cpuPercent, 0);
    return { used: usedPct, idle: Math.max(0, capacity - usedPct) };
  }, [scan.processes, capacity]);

  const chartData = useMemo(
    () => ({
      labels: ['Used', 'Idle'],
      datasets: [
        {
          data: [used, idle],
          backgroundColor: [CHART_COLORS.used, CHART_COLORS.idle],
          borderColor: [CHART_COLORS.usedBorder, CHART_COLORS.idleBorder],
          borderWidth: 2,
        },
      ],
      total: used + idle,
    }),
    [used, idle],
  );

  const utilizationPct = capacity > 0 ? Math.round((used / capacity) * 100) : 0;

  if (!isVisible('cpu-chart') || scan.processes.length === 0 || capacity <= 0) {
    return null;
  }

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    cutout: '65%',
    plugins: {
      legend: {
        position: 'bottom' as const,
        labels: baseLegendLabels(),
      },
      tooltip: {
        ...BASE_TOOLTIP_STYLE,
        callbacks: {
          label: (context: TooltipItem<'doughnut'>) => {
            const raw = context.raw as number;
            const percentage = chartData.total > 0 ? Math.round((raw / chartData.total) * 100) : 0;
            return `${context.label}: ${formatCores(raw)} (${percentage}%)`;
          },
        },
      },
      cpuCenterText: { text: `${utilizationPct}%` },
    },
    hoverOffset: 8,
  };

  return (
    <motion.div
      className="chart-container h-full"
      id="cpu-chart"
      initial={{ opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '-50px' }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="chart-header">
        <h4>CPU Utilization</h4>
      </div>

      <div className="chart-body" style={{ height: '280px' }}>
        <Doughnut data={chartData} options={options} plugins={[CPU_CENTER_PLUGIN]} />
      </div>

      {/* Summary table */}
      <div className="chart-summary">
        <table>
          <tbody>
            <tr>
              <td>Used</td>
              <td className="text-[#e06d83]">{formatCores(used)}</td>
            </tr>
            <tr>
              <td>Idle</td>
              <td className="text-[#63c69d]">{formatCores(idle)}</td>
            </tr>
            <tr>
              <td>Capacity</td>
              <td>{formatCores(capacity)}</td>
            </tr>
            <tr>
              <td>Cores</td>
              <td>{formatCpuCoresLong(parsedData.cpuCores)}</td>
            </tr>
          </tbody>
        </table>
        <p className="mt-2 text-[10px] leading-snug text-[var(--text-muted)]">
          On the local host per-process %CPU streams from /proc once per second
          (a true per-interval reading). Remote hosts use `ps`, which reports a
          lifetime average per process — a coarser snapshot.
        </p>
      </div>
    </motion.div>
  );
}
