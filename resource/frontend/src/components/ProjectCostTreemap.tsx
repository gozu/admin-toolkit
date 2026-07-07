import { useMemo } from 'react';
import { motion } from 'framer-motion';
import { Chart as ChartJS, Tooltip, Legend } from 'chart.js';
import { TreemapController, TreemapElement } from 'chartjs-chart-treemap';
import { Chart } from 'react-chartjs-2';
import type { CruProjectRow } from '../types';
import type { CostLens } from './pages/projectCost/lens';
import {
  formatSeconds,
  k8sGBh,
  lensValue,
  projectTone,
  type CostTone,
} from './pages/projectCost/lens';

// Register treemap components (idempotent — Chart.js dedupes re-registration).
ChartJS.register(TreemapController, TreemapElement, Tooltip, Legend);

// Efficiency-signal colors. Canvas can't read CSS vars, so these mirror the
// DirTreemap palette / tone tokens as literal rgba.
const TONE_FILL: Record<CostTone, string> = {
  ok: 'rgba(99, 198, 157, 0.72)', // mint — compute-active / efficient
  warn: 'rgba(224, 181, 97, 0.72)', // amber — idle-leaning
  crit: 'rgba(224, 109, 131, 0.72)', // rose — idle-resident (reaper candidate)
  neutral: 'rgba(128, 128, 128, 0.5)',
};
const TONE_BORDER: Record<CostTone, string> = {
  ok: 'rgba(99, 198, 157, 1)',
  warn: 'rgba(224, 181, 97, 1)',
  crit: 'rgba(224, 109, 131, 1)',
  neutral: 'rgba(128, 128, 128, 0.85)',
};

interface ProjectCostTreemapProps {
  rows: CruProjectRow[];
  lens: CostLens;
  selectedKey: string | null;
  onSelect: (projectKey: string) => void;
}

interface TreeDatum {
  name: string;
  size: number;
  tone: CostTone;
  row: CruProjectRow;
}

export function ProjectCostTreemap({ rows, lens, selectedKey, onSelect }: ProjectCostTreemapProps) {
  const items = useMemo<TreeDatum[]>(() => {
    return rows
      .map((row) => ({
        name: row.projectKey,
        size: lensValue(row, lens),
        tone: projectTone(row),
        row,
      }))
      .filter((it) => it.size > 0)
      .sort((a, b) => b.size - a.size);
  }, [rows, lens]);

  const chartData = useMemo(
    () => ({
      datasets: [
        {
          tree: items,
          key: 'size',
          backgroundColor: (ctx: { dataIndex: number }) => {
            const it = items[ctx.dataIndex];
            if (!it) return TONE_FILL.neutral;
            return TONE_FILL[it.tone];
          },
          borderColor: (ctx: { dataIndex: number }) => {
            const it = items[ctx.dataIndex];
            if (!it) return TONE_BORDER.neutral;
            // Emphasize the selected rectangle with a brighter border.
            if (selectedKey && it.name === selectedKey) return 'rgba(0, 245, 255, 1)';
            return TONE_BORDER[it.tone];
          },
          borderWidth: (ctx: { dataIndex: number }) => {
            const it = items[ctx.dataIndex];
            return selectedKey && it && it.name === selectedKey ? 3 : 2;
          },
          spacing: 2,
          labels: {
            display: true,
            align: 'center' as const,
            position: 'middle' as const,
            formatter: (ctx: { raw?: { _data?: { name?: string } } }) => ctx.raw?._data?.name || '',
            color: '#f0f0f5',
            font: { family: "'JetBrains Mono', monospace", size: 11, weight: 'bold' as const },
          },
        },
      ],
    }),
    [items, selectedKey],
  );

  const options = useMemo(
    () => ({
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: 'rgba(18, 18, 26, 0.95)',
          titleFont: { size: 13, family: "'Roboto', sans-serif" },
          bodyFont: { size: 12, family: "'JetBrains Mono', monospace" },
          padding: 12,
          cornerRadius: 8,
          borderColor: 'rgba(0, 245, 255, 0.3)',
          borderWidth: 1,
          callbacks: {
            title: () => '',
            label: (ctx: { raw?: { _data?: TreeDatum } }) => {
              const d = ctx.raw?._data;
              if (!d) return '';
              const r = d.row;
              const lines = [
                r.projectKey,
                `Memory: ${r.memGBh.toFixed(1)} GB·h`,
                `CPU: ${r.cpuH.toFixed(2)} CPU·h`,
              ];
              if ((r.sqlExecS ?? 0) > 0) lines.push(`SQL engine: ${formatSeconds(r.sqlExecS)}`);
              if (k8sGBh(r) > 0) lines.push(`K8s: ${k8sGBh(r).toFixed(1)} GB·h`);
              if (r.llmUSD > 0) lines.push(`LLM: $${r.llmUSD.toFixed(4)}`);
              lines.push(`Records: ${r.records.toLocaleString()}`, 'Click to open the drilldown below');
              return lines;
            },
          },
        },
      },
      onClick: (_event: unknown, elements: Array<{ index: number }>) => {
        if (elements.length > 0) {
          const it = items[elements[0].index];
          if (it) onSelect(it.name);
        }
      },
    }),
    [items, onSelect],
  );

  if (items.length === 0) {
    return (
      <div className="glass-card p-5 flex items-center justify-center h-[360px]">
        <span className="text-[var(--text-muted)]">No projects with usage on this lens.</span>
      </div>
    );
  }

  return (
    <motion.div
      className="glass-card p-5"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="flex items-center justify-between mb-4 gap-3">
        <h3 className="text-lg font-semibold text-neon-subtle">Compute by Project</h3>
        <div className="flex flex-wrap items-center gap-3 text-xs text-[var(--text-muted)]">
          <span className="flex items-center gap-1">
            <span className="h-2 w-2 rounded-sm" style={{ background: TONE_FILL.ok }} />
            active
          </span>
          <span className="flex items-center gap-1">
            <span className="h-2 w-2 rounded-sm" style={{ background: TONE_FILL.warn }} />
            idle-leaning
          </span>
          <span className="flex items-center gap-1">
            <span className="h-2 w-2 rounded-sm" style={{ background: TONE_FILL.crit }} />
            idle-resident
          </span>
          <span className="text-[var(--text-tertiary)]">
            size = {lens.toUpperCase()} · click a tile to drill down ↓
          </span>
        </div>
      </div>
      <div style={{ height: '360px' }} className="relative">
        {/* updateMode="none": selection/lens changes swap data in place without
            replaying the layout animation (a full re-animation reads as a page
            reload and hides that the click actually did something). */}
        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
        <Chart type="treemap" data={chartData as any} options={options as any} updateMode="none" />
      </div>
    </motion.div>
  );
}
