import { useMemo } from 'react';
import { motion } from 'framer-motion';
import { Bar } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
  Legend,
  type TooltipItem,
} from 'chart.js';
import { useDiag } from '../context/DiagContext';
import { useTableFilter } from '../hooks/useTableFilter';
import { parseSizeToGB } from '../utils/formatters';
import { CHART_PALETTE } from '../utils/chartColors';
import { BASE_TOOLTIP_STYLE, baseLegendLabels } from '../utils/chartConfig';

ChartJS.register(
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
  Legend
);

const EMPTY_ARR: never[] = [];

const CHART_COLORS = {
  used: CHART_PALETTE.rose,
  usedBorder: CHART_PALETTE.roseBorder,
  available: CHART_PALETTE.mint,
  availableBorder: CHART_PALETTE.mintBorder,
};

export function FilesystemChart() {
  const { state } = useDiag();
  const { isVisible } = useTableFilter();
  const { parsedData } = state;
  const filesystemInfo = parsedData.filesystemInfo ?? EMPTY_ARR;

  const chartData = useMemo(() => {
    // Filter and sort filesystems
    const sortedFilesystems = [...filesystemInfo]
      .filter((fs) => parseInt(fs['Use%']) > 0 && fs['Size'])
      .sort((a, b) => parseInt(b['Use%']) - parseInt(a['Use%']));

    const labels: string[] = [];
    const usedData: number[] = [];
    const availableData: number[] = [];

    for (const fs of sortedFilesystems) {
      const sizeGB = parseSizeToGB(fs['Size']);
      if (sizeGB < 0.1) continue;

      const usedGB = parseSizeToGB(fs['Used']);
      const availableGB = parseSizeToGB(fs['Available']);

      labels.push(`${fs['Mounted on'] || fs['Filesystem']} (${fs['Use%']})`);
      usedData.push(usedGB);
      availableData.push(availableGB);
    }

    return {
      labels,
      datasets: [
        {
          label: 'Used Space (GB)',
          data: usedData,
          backgroundColor: CHART_COLORS.used,
          borderColor: CHART_COLORS.usedBorder,
          borderWidth: 2,
        },
        {
          label: 'Available Space (GB)',
          data: availableData,
          backgroundColor: CHART_COLORS.available,
          borderColor: CHART_COLORS.availableBorder,
          borderWidth: 2,
        },
      ],
      raw: sortedFilesystems,
    };
  }, [filesystemInfo]);

  if (!isVisible('filesystem-table') || filesystemInfo.length === 0) {
    return null;
  }

  const options = {
    indexAxis: 'y' as const,
    responsive: true,
    maintainAspectRatio: false,
    scales: {
      x: {
        stacked: true,
        title: {
          display: true,
          text: 'Size (GB)',
          font: { size: 12, family: "'JetBrains Mono', monospace" },
          color: '#606070',
        },
        grid: {
          color: 'rgba(148, 163, 184, 0.18)',
        },
        ticks: {
          color: '#a0a0b0',
          font: { family: "'JetBrains Mono', monospace" },
        },
      },
      y: {
        stacked: true,
        title: {
          display: true,
          text: 'Filesystem',
          font: { size: 12, family: "'JetBrains Mono', monospace" },
          color: '#606070',
        },
        grid: {
          display: false,
        },
        ticks: {
          color: '#c9d2de',
          font: { family: "'JetBrains Mono', monospace" },
        },
      },
    },
    plugins: {
      legend: {
        display: true,
        position: 'top' as const,
        labels: baseLegendLabels(),
      },
      tooltip: {
        ...BASE_TOOLTIP_STYLE,
        callbacks: {
          label: (context: TooltipItem<'bar'>) => {
            const fs = chartData.raw[context.dataIndex];
            const raw = context.raw as number;
            const label = context.dataset.label || '';
            if (label.includes('Used')) {
              return [
                `${label}: ${raw.toFixed(2)} GB`,
                `Usage: ${fs['Use%']}`,
                `Size: ${fs['Size']}`,
              ];
            }
            return `${label}: ${raw.toFixed(2)} GB`;
          },
        },
      },
    },
  };

  return (
    <motion.div
      className="chart-container card-2x1"
      id="filesystem-table"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="chart-header">
        <h4>Filesystem Usage</h4>
      </div>

      <div className="chart-body" style={{ height: `${Math.max(120, chartData.labels.length * 38 + 50)}px` }}>
        <Bar data={chartData} options={options} />
      </div>
    </motion.div>
  );
}
