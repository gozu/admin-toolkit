/**
 * Story User Activity — DAU trend + per-user daily rows + Event Mix, from the
 * Postgres story.user_activity_daily / story.audit_event_counts tables.
 *
 * Bootstrap: one mount effect → storyStore.loadActivity(). The window select
 * and instance filter are user-input-keyed refetches/filters (contract-exempt).
 */
import { useEffect, useMemo, useState } from 'react';
import { DataGrid } from '../../common/DataGrid';
import type { ColumnDef } from '../../../utils/dataGridTypes';
import {
  storyStore,
  type StoryActivityDay,
  type StoryActivityUserRow,
  type StoryEventCountRow,
} from '../../../state/storyStore';
import { StoryCard, StoryNotConfiguredNotice, WindowSelect } from './storyShared';
import { slotLifecycle } from './storyUtils';

const USER_COLUMNS: ColumnDef<StoryActivityUserRow>[] = [
  { id: 'day', label: 'Day', render: (r) => <span className="font-mono text-xs">{r.day}</span>, sortValue: (r) => r.day },
  { id: 'instance', label: 'Instance', render: (r) => <span className="font-mono text-xs">{r.instance_id}</span>, sortValue: (r) => r.instance_id },
  { id: 'login', label: 'User', render: (r) => r.login, sortValue: (r) => r.login },
  { id: 'project', label: 'Project', render: (r) => r.project_key || <span className="text-[var(--text-muted)]">(none)</span>, sortValue: (r) => r.project_key },
  { id: 'viewing', label: 'Viewing', align: 'right', mono: true, render: (r) => r.viewing_actions.toLocaleString(), sortValue: (r) => r.viewing_actions },
  { id: 'developing', label: 'Developing', align: 'right', mono: true, render: (r) => r.developing_actions.toLocaleString(), sortValue: (r) => r.developing_actions },
];

const EVENT_COLUMNS: ColumnDef<StoryEventCountRow>[] = [
  { id: 'day', label: 'Day', render: (r) => <span className="font-mono text-xs">{r.day}</span>, sortValue: (r) => r.day },
  { id: 'instance', label: 'Instance', render: (r) => <span className="font-mono text-xs">{r.instance_id}</span>, sortValue: (r) => r.instance_id },
  { id: 'taxonomy', label: 'Category', render: (r) => r.taxonomy, sortValue: (r) => r.taxonomy },
  { id: 'msgType', label: 'Event Type', render: (r) => <span className="font-mono text-xs">{r.msg_type}</span>, sortValue: (r) => r.msg_type },
  { id: 'count', label: 'Count', align: 'right', mono: true, render: (r) => r.event_count.toLocaleString(), sortValue: (r) => r.event_count },
];

/** Minimal dependency-free trend: one bar per day, height ∝ active users. */
function DauBars({ days }: { days: StoryActivityDay[] }) {
  const byDay = useMemo(() => {
    const map = new Map<string, { activeUsers: number; developingUsers: number }>();
    for (const row of days) {
      const entry = map.get(row.day) ?? { activeUsers: 0, developingUsers: 0 };
      entry.activeUsers += row.active_users;
      entry.developingUsers += row.developing_users;
      map.set(row.day, entry);
    }
    return [...map.entries()].sort(([a], [b]) => (a < b ? -1 : 1));
  }, [days]);

  if (byDay.length === 0) {
    return <p className="text-sm text-[var(--text-muted)]">No activity recorded in this window yet.</p>;
  }
  const max = Math.max(...byDay.map(([, v]) => v.activeUsers), 1);
  return (
    <div className="flex items-end gap-[2px] h-28" role="img" aria-label="Daily active users">
      {byDay.map(([day, value]) => (
        <div
          key={day}
          className="flex-1 min-w-[3px] rounded-t bg-[var(--accent)]/60 hover:bg-[var(--accent)] relative group"
          style={{ height: `${Math.max(4, (value.activeUsers / max) * 100)}%` }}
          title={`${day}: ${value.activeUsers} active (${value.developingUsers} developing)`}
        />
      ))}
    </div>
  );
}

export function StoryUserActivityPage() {
  const state = storyStore.use();
  const { activity, eventCounts, activityDays } = state;
  const [tab, setTab] = useState<'activity' | 'events'>('activity');
  const [instance, setInstance] = useState('');

  useEffect(() => {
    void storyStore.loadActivity();
  }, []);

  const notConfigured = activity.error?.includes('story-not-configured');
  if (notConfigured) {
    return <div className="w-full py-4"><StoryNotConfiguredNotice /></div>;
  }

  const dayRows = (activity.data?.days ?? []).filter((r) => !instance || r.instance_id === instance);
  const userRows = (activity.data?.users ?? []).filter((r) => !instance || r.instance_id === instance);
  const eventRows = (eventCounts.data?.rows ?? []).filter((r) => !instance || r.instance_id === instance);
  const instances = [...new Set((activity.data?.days ?? []).map((r) => r.instance_id))].sort();

  const totals = dayRows.reduce(
    (acc, row) => {
      acc.viewing += row.viewing_actions;
      acc.developing += row.developing_actions;
      return acc;
    },
    { viewing: 0, developing: 0 },
  );
  const uniqueDays = new Set(dayRows.map((r) => r.day)).size;

  return (
    <div className="w-full py-4 flex flex-col gap-4">
      <StoryCard
        title="User Activity"
        subtitle="Human UI actions from the audit logs, aggregated per UTC day. Viewing counts every retained action; developing counts vocabulary-matched build actions."
      >
        <div className="flex flex-wrap items-center gap-4">
          <WindowSelect
            value={activityDays}
            options={[7, 30, 90]}
            onChange={(days) => void storyStore.loadActivity(days)}
          />
          <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
            Instance
            <select value={instance} onChange={(e) => setInstance(e.target.value)} className="input-glass text-sm">
              <option value="">All</option>
              {instances.map((id) => <option key={id} value={id}>{id}</option>)}
            </select>
          </label>
          <div className="flex gap-1 ml-auto">
            {(['activity', 'events'] as const).map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => setTab(key)}
                className={`px-3 py-1 rounded text-sm transition-colors ${
                  tab === key
                    ? 'bg-[var(--accent)]/20 text-[var(--accent)]'
                    : 'text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)]'
                }`}
              >
                {key === 'activity' ? 'Activity' : 'Event Mix'}
              </button>
            ))}
          </div>
        </div>
        <div className="text-sm text-[var(--text-secondary)]">
          {uniqueDays} day(s) · {totals.viewing.toLocaleString()} viewing ·{' '}
          {totals.developing.toLocaleString()} developing actions
        </div>
        <DauBars days={dayRows} />
      </StoryCard>

      {tab === 'activity' ? (
        <DataGrid
          rows={userRows}
          columns={USER_COLUMNS}
          rowKey={(r) => `${r.day}:${r.instance_id}:${r.login}:${r.project_key}`}
          title="Per-user daily activity"
          lifecycle={slotLifecycle(activity, userRows.length === 0)}
          emptyMessage="No user activity collected yet — run a collection from Story Setup."
          defaultSortColumnId="day"
          scroll={{ maxH: '28rem' }}
          showRowCount
        />
      ) : (
        <DataGrid
          rows={eventRows}
          columns={EVENT_COLUMNS}
          rowKey={(r) => `${r.day}:${r.instance_id}:${r.msg_type}`}
          title="Event mix (taxonomy applied at query time)"
          lifecycle={slotLifecycle(eventCounts, eventRows.length === 0)}
          emptyMessage="No audit events collected yet."
          defaultSortColumnId="count"
          scroll={{ maxH: '28rem' }}
          showRowCount
        />
      )}
    </div>
  );
}
