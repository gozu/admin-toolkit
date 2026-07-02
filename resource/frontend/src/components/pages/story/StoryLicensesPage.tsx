/**
 * Story Licenses — latest license snapshot per instance + profile caps vs
 * usage over time, from story.license_snapshots / story.license_profile_caps.
 */
import { useEffect } from 'react';
import { DataGrid } from '../../common/DataGrid';
import type { ColumnDef } from '../../../utils/dataGridTypes';
import {
  storyStore,
  type StoryLicenseCapRow,
  type StoryLicenseLatest,
} from '../../../state/storyStore';
import { StoryCard, StoryNotConfiguredNotice } from './storyShared';
import { slotLifecycle } from './storyUtils';

function utilizationColor(used: number, cap: number | null): string {
  if (cap == null || cap <= 0) return 'var(--text-secondary)';
  const ratio = used / cap;
  if (ratio >= 1) return 'var(--neon-red, #ef4444)';
  if (ratio >= 0.8) return 'var(--neon-yellow, #eab308)';
  return 'var(--success)';
}

const CAP_COLUMNS: ColumnDef<StoryLicenseCapRow>[] = [
  { id: 'date', label: 'Snapshot', render: (r) => <span className="font-mono text-xs">{r.snapshot_date}</span>, sortValue: (r) => r.snapshot_date },
  { id: 'instance', label: 'Instance', render: (r) => <span className="font-mono text-xs">{r.instance_id}</span>, sortValue: (r) => r.instance_id },
  { id: 'profile', label: 'Profile', render: (r) => r.profile, sortValue: (r) => r.profile },
  { id: 'used', label: 'Used', align: 'right', mono: true, render: (r) => (r.used ?? 0).toLocaleString(), sortValue: (r) => r.used ?? 0 },
  {
    id: 'cap', label: 'Cap', align: 'right', mono: true,
    render: (r) => r.cap == null ? <span className="text-[var(--text-muted)]">unlimited</span> : r.cap.toLocaleString(),
    sortValue: (r) => r.cap ?? Number.MAX_SAFE_INTEGER,
  },
  {
    id: 'utilization', label: 'Utilization', align: 'right', mono: true,
    sortValue: (r) => (r.cap ? (r.used ?? 0) / r.cap : -1),
    render: (r) => r.cap
      ? (
        <span style={{ color: utilizationColor(r.used ?? 0, r.cap) }}>
          {(((r.used ?? 0) / r.cap) * 100).toFixed(0)}%
        </span>
      )
      : <span className="text-[var(--text-muted)]">—</span>,
  },
];

function LatestSnapshotCard({ snapshot }: { snapshot: StoryLicenseLatest }) {
  let addons: string[] = [];
  try {
    const parsed: unknown = snapshot.addons ? JSON.parse(snapshot.addons) : {};
    if (parsed && typeof parsed === 'object') {
      addons = Object.entries(parsed as Record<string, unknown>)
        .filter(([, v]) => String(v).toLowerCase() === 'true')
        .map(([k]) => k);
    }
  } catch {
    addons = [];
  }
  return (
    <div className="p-3 rounded border border-[var(--border-default)] space-y-1 min-w-[16rem]">
      <div className="flex items-center gap-2">
        <span className="font-mono text-sm text-[var(--text-primary)]">{snapshot.instance_id}</span>
        <span className="text-xs text-[var(--text-muted)]">as of {snapshot.snapshot_date}</span>
      </div>
      <div className="text-sm text-[var(--text-secondary)]">
        DSS {snapshot.dss_version ?? '?'} · {snapshot.license_kind ?? 'unknown license'}
      </div>
      <div className="text-sm text-[var(--text-secondary)]">
        {snapshot.users_total ?? 0} users · expires {snapshot.expires_on ?? 'n/a'}
      </div>
      {addons.length > 0 && (
        <div className="text-xs text-[var(--text-muted)]">addons: {addons.join(', ')}</div>
      )}
    </div>
  );
}

export function StoryLicensesPage() {
  const { licenses } = storyStore.use();

  useEffect(() => {
    void storyStore.loadLicenses();
  }, []);

  if (licenses.error?.includes('story-not-configured')) {
    return <div className="w-full py-4"><StoryNotConfiguredNotice /></div>;
  }

  const latest = licenses.data?.latest ?? [];
  const caps = licenses.data?.caps ?? [];

  return (
    <div className="w-full py-4 flex flex-col gap-4">
      <StoryCard
        title="Licenses"
        subtitle="Latest license snapshot per instance; the grid below keeps every daily snapshot so cap changes and creeping utilization stay visible."
      >
        {latest.length === 0 ? (
          <p className="text-sm text-[var(--text-muted)]">
            No license snapshots yet — run a collection from Story Setup.
          </p>
        ) : (
          <div className="flex flex-wrap gap-3">
            {latest.map((snapshot) => (
              <LatestSnapshotCard key={snapshot.instance_id} snapshot={snapshot} />
            ))}
          </div>
        )}
      </StoryCard>

      <DataGrid
        rows={caps}
        columns={CAP_COLUMNS}
        rowKey={(r) => `${r.snapshot_date}:${r.instance_id}:${r.profile}`}
        title="Profile caps vs usage"
        lifecycle={slotLifecycle(licenses, caps.length === 0)}
        emptyMessage="No profile cap data collected yet."
        defaultSortColumnId="date"
        scroll={{ maxH: '28rem' }}
        showRowCount
      />
    </div>
  );
}
