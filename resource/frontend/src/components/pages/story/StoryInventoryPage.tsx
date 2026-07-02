/**
 * Story Inventory — object-count trends per instance/type and the latest
 * per-project breakdown, from story.object_inventory_daily.
 */
import { useEffect, useMemo, useState } from 'react';
import { DataGrid } from '../../common/DataGrid';
import type { ColumnDef } from '../../../utils/dataGridTypes';
import {
  storyStore,
  type StoryInventoryProjectRow,
  type StoryInventoryTrendRow,
} from '../../../state/storyStore';
import { StoryCard, StoryNotConfiguredNotice } from './storyShared';
import { slotLifecycle } from './storyUtils';

const TREND_COLUMNS: ColumnDef<StoryInventoryTrendRow>[] = [
  { id: 'date', label: 'Snapshot', render: (r) => <span className="font-mono text-xs">{r.snapshot_date}</span>, sortValue: (r) => r.snapshot_date },
  { id: 'instance', label: 'Instance', render: (r) => <span className="font-mono text-xs">{r.instance_id}</span>, sortValue: (r) => r.instance_id },
  { id: 'type', label: 'Object Type', render: (r) => r.object_type, sortValue: (r) => r.object_type },
  { id: 'count', label: 'Count', align: 'right', mono: true, render: (r) => r.object_count.toLocaleString(), sortValue: (r) => r.object_count },
];

const PROJECT_COLUMNS: ColumnDef<StoryInventoryProjectRow>[] = [
  { id: 'instance', label: 'Instance', render: (r) => <span className="font-mono text-xs">{r.instance_id}</span>, sortValue: (r) => r.instance_id },
  { id: 'project', label: 'Project', render: (r) => <span className="font-mono text-xs">{r.project_key}</span>, sortValue: (r) => r.project_key },
  { id: 'type', label: 'Object Type', render: (r) => r.object_type, sortValue: (r) => r.object_type },
  { id: 'count', label: 'Count', align: 'right', mono: true, render: (r) => r.object_count.toLocaleString(), sortValue: (r) => r.object_count },
  { id: 'date', label: 'As Of', render: (r) => <span className="font-mono text-xs">{r.snapshot_date}</span>, sortValue: (r) => r.snapshot_date },
];

/** Latest totals per object type with delta vs the earliest snapshot in window. */
function TypeSummary({ trends }: { trends: StoryInventoryTrendRow[] }) {
  const summary = useMemo(() => {
    const byType = new Map<string, StoryInventoryTrendRow[]>();
    for (const row of trends) {
      const list = byType.get(row.object_type) ?? [];
      list.push(row);
      byType.set(row.object_type, list);
    }
    return [...byType.entries()].map(([type, rows]) => {
      const dates = [...new Set(rows.map((r) => r.snapshot_date))].sort();
      const first = dates[0];
      const last = dates[dates.length - 1];
      const sum = (d: string) => rows.filter((r) => r.snapshot_date === d)
        .reduce((acc, r) => acc + r.object_count, 0);
      const latest = sum(last);
      return { type, latest, delta: latest - sum(first), asOf: last };
    }).sort((a, b) => b.latest - a.latest);
  }, [trends]);

  if (summary.length === 0) {
    return <p className="text-sm text-[var(--text-muted)]">No inventory snapshots yet — run a collection from Story Setup.</p>;
  }
  return (
    <div className="flex flex-wrap gap-3">
      {summary.map(({ type, latest, delta, asOf }) => (
        <div key={type} className="p-3 rounded border border-[var(--border-default)] min-w-[9rem]">
          <div className="text-xs uppercase tracking-wide text-[var(--text-muted)]">{type}</div>
          <div className="text-xl font-semibold text-[var(--text-primary)] tabular-nums">
            {latest.toLocaleString()}
          </div>
          <div className="text-xs text-[var(--text-secondary)]">
            {delta === 0 ? 'no change' : `${delta > 0 ? '+' : ''}${delta.toLocaleString()} in window`}
            <span className="block text-[var(--text-muted)]">as of {asOf}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

export function StoryInventoryPage() {
  const { inventory } = storyStore.use();
  const [instance, setInstance] = useState('');

  useEffect(() => {
    void storyStore.loadInventory();
  }, []);

  if (inventory.error?.includes('story-not-configured')) {
    return <div className="w-full py-4"><StoryNotConfiguredNotice /></div>;
  }

  const trends = (inventory.data?.trends ?? []).filter((r) => !instance || r.instance_id === instance);
  const latest = (inventory.data?.latestByProject ?? []).filter((r) => !instance || r.instance_id === instance);
  const instances = [...new Set((inventory.data?.trends ?? []).map((r) => r.instance_id))].sort();

  return (
    <div className="w-full py-4 flex flex-col gap-4">
      <StoryCard
        title="Object Inventory"
        subtitle={`Daily object counts per instance and type over the last ${inventory.data?.windowDays ?? 90} days. Counts are kept forever; item-level rows are pruned per the retention setting.`}
      >
        <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
          Instance
          <select value={instance} onChange={(e) => setInstance(e.target.value)} className="input-glass text-sm">
            <option value="">All</option>
            {instances.map((id) => <option key={id} value={id}>{id}</option>)}
          </select>
        </label>
        <TypeSummary trends={trends} />
      </StoryCard>

      <DataGrid
        rows={latest}
        columns={PROJECT_COLUMNS}
        rowKey={(r) => `${r.instance_id}:${r.project_key}:${r.object_type}`}
        title="Latest per-project breakdown"
        lifecycle={slotLifecycle(inventory, latest.length === 0)}
        emptyMessage="No inventory collected yet."
        defaultSortColumnId="count"
        scroll={{ maxH: '24rem' }}
        showRowCount
      />

      <DataGrid
        rows={trends}
        columns={TREND_COLUMNS}
        rowKey={(r) => `${r.snapshot_date}:${r.instance_id}:${r.object_type}`}
        title="Count trend (per instance / type / day)"
        lifecycle={slotLifecycle(inventory, trends.length === 0)}
        emptyMessage="No inventory trend rows yet."
        defaultSortColumnId="date"
        scroll={{ maxH: '24rem' }}
        showRowCount
      />
    </div>
  );
}
