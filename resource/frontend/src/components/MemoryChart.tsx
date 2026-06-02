import { useMemo } from 'react';
import { motion } from 'framer-motion';
import { Doughnut } from 'react-chartjs-2';
import { Chart as ChartJS, ArcElement, Tooltip, Legend, type TooltipItem, type Plugin } from 'chart.js';
import { useDiag } from '../context/DiagContext';
import { useTableFilter } from '../hooks/useTableFilter';
import { parseNumericValue, formatMemory } from '../utils/formatters';
import { CHART_PALETTE } from '../utils/chartColors';
import { BASE_TOOLTIP_STYLE, baseLegendLabels } from '../utils/chartConfig';

ChartJS.register(ArcElement, Tooltip, Legend);

const EMPTY_OBJ: Record<string, never> = {};

const CHART_COLORS = {
  used: CHART_PALETTE.rose,
  usedBorder: CHART_PALETTE.roseBorder,
  free: CHART_PALETTE.mint,
  freeBorder: CHART_PALETTE.mintBorder,
  buffers: CHART_PALETTE.blue,
  buffersBorder: CHART_PALETTE.blueBorder,
};

export function MemoryChart() {
  const { state } = useDiag();
  const { isVisible } = useTableFilter();
  const { parsedData } = state;
  const memoryInfo = parsedData.memoryInfo ?? EMPTY_OBJ;

  const chartData = useMemo(() => {
    const parseMemory = (value: string | undefined): number => {
      if (!value) return 0;
      const numValue = parseNumericValue(value);
      if (value.includes('GB')) return numValue * 1024;
      return numValue;
    };

    const usedMemory = parseMemory(memoryInfo.used);
    const freeMemory = parseMemory(memoryInfo.free);
    const buffersMemory = parseMemory(memoryInfo['buff/cache']);

    return {
      labels: ['Used', 'Free', 'Buffers/Cache'],
      datasets: [
        {
          data: [usedMemory, freeMemory, buffersMemory],
          backgroundColor: [
            CHART_COLORS.used,
            CHART_COLORS.free,
            CHART_COLORS.buffers,
          ],
          borderColor: [
            CHART_COLORS.usedBorder,
            CHART_COLORS.freeBorder,
            CHART_COLORS.buffersBorder,
          ],
          borderWidth: 2,
        },
      ],
      total: usedMemory + freeMemory + buffersMemory,
    };
  }, [memoryInfo]);

  const centerTextPlugin: Plugin<'doughnut'> = useMemo(() => ({
    id: 'memoryCenterText',
    afterDraw(chart) {
      const { ctx, width, height } = chart;
      const totalMemory = memoryInfo.total || formatMemory(chartData.total);
      const isDark = document.documentElement.getAttribute('data-theme') !== 'light';

      ctx.save();
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';

      const centerX = width / 2;
      const centerY = height / 2;

      // Main value
      ctx.font = 'bold 18px "JetBrains Mono", monospace';
      ctx.fillStyle = isDark ? '#ffffff' : '#1a1a2e';
      if (isDark) {
        ctx.shadowColor = 'rgba(0, 168, 157, 0.4)';
        ctx.shadowBlur = 8;
      }
      ctx.fillText(totalMemory, centerX, centerY - 8);

      // Label
      ctx.shadowBlur = 0;
      ctx.font = '11px "JetBrains Mono", monospace';
      ctx.fillStyle = isDark ? 'rgba(255,255,255,0.5)' : 'rgba(0,0,0,0.45)';
      ctx.fillText('Total', centerX, centerY + 12);

      ctx.restore();
    },
  }), [chartData.total, memoryInfo.total]);

  if (!isVisible('memory-chart') || Object.keys(memoryInfo).length === 0) {
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
            const percentage = Math.round((raw / chartData.total) * 100);
            return `${context.label}: ${formatMemory(raw)} (${percentage}%)`;
          },
        },
      },
    },
    hoverOffset: 8,
  };

  return (
    <motion.div
      className="chart-container h-full"
      id="memory-chart"
      initial={{ opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '-50px' }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="chart-header">
        <h4>System Memory</h4>
      </div>

      <div className="chart-body" style={{ height: '280px' }}>
        <Doughnut data={chartData} options={options} plugins={[centerTextPlugin]} />
      </div>

      {/* Summary table */}
      <div className="chart-summary">
        <table>
          <tbody>
            <MemoryRow label="Total Memory" value={memoryInfo.total} />
            <MemoryRow label="Used Memory" value={memoryInfo.used} color="text-[#e06d83]" />
            <MemoryRow label="Free Memory" value={memoryInfo.free} color="text-[#63c69d]" />
            <MemoryRow label="Available Memory" value={memoryInfo.available} />
            {memoryInfo['buff/cache'] && (
              <MemoryRow
                label="Buffers/Cache"
                value={memoryInfo['buff/cache']}
                color="text-[#6da3e0]"
              />
            )}
            {memoryInfo['Swap total'] &&
              memoryInfo['Swap total'] !== 'Not configured' && (
                <>
                  <MemoryRow label="Swap Total" value={memoryInfo['Swap total']} />
                  <MemoryRow label="Swap Used" value={memoryInfo['Swap used']} />
                  <MemoryRow label="Swap Free" value={memoryInfo['Swap free']} />
                </>
              )}
          </tbody>
        </table>
      </div>
    </motion.div>
  );
}

function MemoryRow({ label, value, color }: { label: string; value?: string; color?: string }) {
  if (!value) return null;
  return (
    <tr>
      <td>{label}</td>
      <td className={color}>{value}</td>
    </tr>
  );
}
