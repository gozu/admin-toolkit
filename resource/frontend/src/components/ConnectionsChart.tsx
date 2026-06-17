import { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { Doughnut } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  ArcElement,
  Tooltip,
  Legend,
  type ActiveElement,
  type ChartEvent,
  type TooltipItem,
  type Plugin,
} from 'chart.js';
import { useDiag } from '../context/DiagContext';
import { useTableFilter } from '../hooks/useTableFilter';
import { BASE_TOOLTIP_STYLE, baseLegendLabels } from '../utils/chartConfig';

ChartJS.register(ArcElement, Tooltip, Legend);

// Muted categorical palette for readability
const COLORS = [
  'rgba(109, 163, 224, 0.8)',  // blue
  'rgba(153, 123, 224, 0.8)',  // violet
  'rgba(99, 198, 157, 0.8)',   // mint
  'rgba(224, 181, 97, 0.8)',   // amber
  'rgba(224, 109, 131, 0.8)',  // rose
  'rgba(101, 194, 217, 0.8)',  // cyan
  'rgba(132, 205, 116, 0.8)',  // green
  'rgba(224, 146, 106, 0.8)',  // orange
  'rgba(132, 149, 220, 0.8)',  // indigo
  'rgba(189, 130, 204, 0.8)',  // orchid
];

const BORDER_COLORS = COLORS.map((c) => c.replace('0.7)', '1)'));

const EMPTY_OBJ: Record<string, never> = {};

export function ConnectionsChart() {
  const { state, setFocusedConnectionFilter, setActivePage } = useDiag();
  const { isVisible } = useTableFilter();
  const { parsedData } = state;
  const connections = parsedData.connections ?? EMPTY_OBJ;

  const navigateToInsightsForType = (type: string) => {
    setFocusedConnectionFilter({ type });
    setActivePage('connections-insights');
  };

  const chartData = useMemo(() => {
    const sortedConnections = Object.entries(connections).sort(
      (a, b) => b[1] - a[1]
    );

    const total = sortedConnections.reduce((sum, [, count]) => sum + count, 0);

    // Truncate labels for pie chart display (longer threshold for JDBC types)
    const labels = sortedConnections.map(([type]) => {
      // For JDBC types with driver info, show a shorter version in the legend
      if (type.startsWith('JDBC (') && type.length > 24) {
        // Extract driver class and show just the last part
        const match = type.match(/JDBC \(([^)]+)\)/);
        if (match) {
          const driverParts = match[1].split('.');
          const shortDriver = driverParts[driverParts.length - 1];
          return `JDBC (${shortDriver.length > 16 ? shortDriver.substring(0, 13) + '...' : shortDriver})`;
        }
      }
      return type.length > 20 ? type.substring(0, 17) + '...' : type;
    });
    const data = sortedConnections.map(([, count]) => count);

    return {
      labels,
      datasets: [
        {
          data,
          backgroundColor: COLORS.slice(0, data.length),
          borderColor: BORDER_COLORS.slice(0, data.length),
          borderWidth: 2,
        },
      ],
      total,
      sortedConnections,
    };
  }, [connections]);

  const centerTextPlugin: Plugin<'doughnut'> = useMemo(() => ({
    id: 'connectionsCenterText',
    afterDraw(chart) {
      const { ctx } = chart;
      const isDark = document.documentElement.getAttribute('data-theme') !== 'light';

      ctx.save();
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';

      const { left, right, top, bottom } = chart.chartArea;
      const centerX = (left + right) / 2;
      const centerY = (top + bottom) / 2;

      // Main value
      ctx.font = 'bold 22px "JetBrains Mono", monospace';
      ctx.fillStyle = isDark ? '#ffffff' : '#1a1a2e';
      if (isDark) {
        ctx.shadowColor = 'rgba(0, 168, 157, 0.4)';
        ctx.shadowBlur = 8;
      }
      ctx.fillText(String(chartData.total), centerX, centerY - 8);

      // Label
      ctx.shadowBlur = 0;
      ctx.font = '11px "JetBrains Mono", monospace';
      ctx.fillStyle = isDark ? 'rgba(255,255,255,0.5)' : 'rgba(0,0,0,0.45)';
      ctx.fillText('Total', centerX, centerY + 14);

      ctx.restore();
    },
  }), [chartData.total]);

  if (!isVisible('connections-chart') || Object.keys(connections).length === 0) {
    return null;
  }

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    cutout: '65%',
    onHover: (event: ChartEvent, elements: ActiveElement[]) => {
      const target = event.native?.target as HTMLElement | undefined;
      if (target && 'style' in target) {
        target.style.cursor = elements.length > 0 ? 'pointer' : 'default';
      }
    },
    onClick: (_event: ChartEvent, elements: ActiveElement[]) => {
      if (elements.length === 0) return;
      const idx = elements[0].index;
      const entry = chartData.sortedConnections[idx];
      if (!entry) return;
      navigateToInsightsForType(entry[0]);
    },
    plugins: {
      legend: {
        position: 'right' as const,
        labels: baseLegendLabels({ padding: 12, font: { size: 11, family: "'JetBrains Mono', monospace" } }),
      },
      tooltip: {
        ...BASE_TOOLTIP_STYLE,
        callbacks: {
          label: (context: TooltipItem<'doughnut'>) => {
            const fullName = chartData.sortedConnections[context.dataIndex][0];
            const raw = context.raw as number;
            const percentage = Math.round((raw / chartData.total) * 100);
            return `${fullName}: ${raw} (${percentage}%)`;
          },
        },
      },
    },
    hoverOffset: 8,
  };

  return (
    <motion.div
      className="chart-container"
      id="connections-chart"
      initial={{ opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '-50px' }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="chart-header">
        <div className="flex items-center gap-2">
          <h4>Connection Types</h4>
          <span className="badge badge-info font-mono">
            {chartData.sortedConnections.length} types
          </span>
        </div>
      </div>

      <div className="chart-body" style={{ height: '280px' }}>
        <Doughnut data={chartData} options={options} plugins={[centerTextPlugin]} />
      </div>

      <ConnectionsSummaryTable
        sortedConnections={chartData.sortedConnections}
        total={chartData.total}
        onTypeClick={navigateToInsightsForType}
      />
    </motion.div>
  );
}

function ConnectionsSummaryTable({
  sortedConnections,
  total,
  onTypeClick,
}: {
  sortedConnections: [string, number][];
  total: number;
  onTypeClick: (type: string) => void;
}) {
  const visibleConnections = sortedConnections;

  const containerRef = useRef<HTMLDivElement | null>(null);
  const cellRef = useRef<HTMLButtonElement | null>(null);
  const [columns, setColumns] = useState(2);

  const recompute = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const rows = visibleConnections.length;
    if (rows === 0) return;

    const containerTop = container.getBoundingClientRect().top;
    const bottomPadding = 24;
    const availableHeight = Math.max(0, window.innerHeight - containerTop - bottomPadding);
    const measuredRowHeight = cellRef.current?.getBoundingClientRect().height || 28;
    const rowHeight = measuredRowHeight > 0 ? measuredRowHeight : 28;

    let chosen = 4;
    for (const n of [2, 3, 4] as const) {
      const rowsPerColumn = Math.ceil(rows / n);
      if (rowsPerColumn * rowHeight <= availableHeight) {
        chosen = n;
        break;
      }
    }
    setColumns(chosen);
  }, [visibleConnections.length]);

  useLayoutEffect(() => {
    recompute();
    const container = containerRef.current;
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => recompute()) : null;
    if (ro && container) ro.observe(container);
    window.addEventListener('resize', recompute);
    return () => {
      window.removeEventListener('resize', recompute);
      ro?.disconnect();
    };
  }, [recompute]);

  return (
    <div className="chart-summary">
      <div
        ref={containerRef}
        style={{
          display: 'grid',
          gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
          columnGap: '12px',
        }}
      >
        {visibleConnections.map(([type, count], idx) => (
          <button
            key={idx}
            ref={idx === 0 ? cellRef : undefined}
            type="button"
            onClick={() => onTypeClick(type)}
            title={`Show ${type} connections in Insights`}
            className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-2 px-2 py-1 text-left cursor-pointer hover:bg-[var(--bg-glass)] transition-colors rounded text-sm bg-transparent border-none w-full"
          >
            <span className="truncate text-[var(--text-secondary)]">{type}</span>
            <span className="tabular-nums font-mono text-[#7fb3ea]">{count}</span>
            <span className="tabular-nums font-mono text-[var(--text-muted)] w-10 text-right">
              {Math.round((count / total) * 100)}%
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
