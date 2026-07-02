/**
 * Story Setup — provisioning + collection health for the Story analytics layer.
 *
 * Bootstrap per the Module Bootstrap Contract: a single mount effect calls
 * storyStore.loadStatus(); Provision / Run Now are user actions on the store.
 */
import { useEffect } from 'react';
import { DataGrid } from '../../common/DataGrid';
import type { ColumnDef } from '../../../utils/dataGridTypes';
import { storyStore, type StoryIngestRun } from '../../../state/storyStore';
import { StatusPill, StoryCard } from './storyShared';
import { STORY_PRIMARY_BUTTON, STORY_SECONDARY_BUTTON, slotLifecycle } from './storyUtils';

const INGEST_COLUMNS: ColumnDef<StoryIngestRun>[] = [
  { id: 'instance', label: 'Instance', render: (r) => <span className="font-mono text-xs">{r.instance_id}</span>, sortValue: (r) => r.instance_id },
  { id: 'source', label: 'Source', render: (r) => r.source, sortValue: (r) => r.source },
  { id: 'cursor', label: 'Cursor', render: (r) => <span className="font-mono text-xs">{r.cursor_value ?? '—'}</span>, sortValue: (r) => r.cursor_value ?? '' },
  { id: 'lastRun', label: 'Last Run', render: (r) => r.last_run_at ? new Date(r.last_run_at).toLocaleString() : '—', sortValue: (r) => r.last_run_at ?? '' },
  {
    id: 'status', label: 'Status', sortValue: (r) => r.last_status ?? '',
    render: (r) => r.last_status
      ? <StatusPill ok={r.last_status === 'ok'} okLabel="ok" badLabel={r.last_status} />
      : '—',
  },
  { id: 'rows', label: 'Rows', align: 'right', mono: true, render: (r) => (r.last_rows_written ?? 0).toLocaleString(), sortValue: (r) => r.last_rows_written ?? 0 },
  {
    id: 'error', label: 'Last Error', width: '34%',
    render: (r) => r.last_error
      ? <span className="text-xs text-[var(--neon-red)] break-all">{r.last_error}</span>
      : <span className="text-[var(--text-muted)]">—</span>,
  },
];

export function StorySetupPage() {
  const state = storyStore.use();
  const { status, provision, runNow } = state;
  const data = status.data;

  useEffect(() => {
    void storyStore.loadStatus();
  }, []);

  const scenario = data?.scenario;

  return (
    <div className="w-full py-4 flex flex-col gap-4">
      <StoryCard
        title="Story Setup"
        subtitle="Experimental: scheduled, Postgres-persisted analytics (user activity, licenses, inventory) collected on this hub from every configured host."
      >
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <div className="p-3 rounded border border-[var(--border-default)] space-y-1">
            <div className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Configuration</div>
            <StatusPill ok={!!data?.configured} okLabel="Configured" badLabel="Not configured" />
            <div className="text-xs text-[var(--text-secondary)] font-mono">
              {data?.connection || 'no connection selected'}
            </div>
          </div>
          <div className="p-3 rounded border border-[var(--border-default)] space-y-1">
            <div className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Database</div>
            <StatusPill ok={!!data?.dbOk} okLabel="Reachable" badLabel="Unreachable" />
            <div className="text-xs text-[var(--text-secondary)]">
              schema v{data?.schemaVersion ?? 0}
              {data?.dbError && <span className="block text-[var(--neon-red)] break-all">{data.dbError}</span>}
            </div>
          </div>
          <div className="p-3 rounded border border-[var(--border-default)] space-y-1">
            <div className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Scenario</div>
            <StatusPill
              ok={!!scenario?.exists && !!scenario?.active}
              okLabel="Active"
              badLabel={scenario?.exists ? 'Inactive' : 'Missing'}
            />
            <div className="text-xs text-[var(--text-secondary)]">
              {scenario?.triggerHour != null ? `daily @ ${String(scenario.triggerHour).padStart(2, '0')}:00` : 'no trigger'}
              {scenario?.lastRun?.outcome && <span className="block">last outcome: {scenario.lastRun.outcome}</span>}
            </div>
          </div>
          <div className="p-3 rounded border border-[var(--border-default)] space-y-1">
            <div className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Failure Email</div>
            <StatusPill ok={!!scenario?.reporterVerified} okLabel="Verified" badLabel="Unverified" />
            <div className="text-xs text-[var(--text-secondary)]">
              {data?.alertEmail}
              {scenario?.reporterShape && <span className="block">shape: {scenario.reporterShape}</span>}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 pt-1">
          <button
            type="button"
            className={STORY_PRIMARY_BUTTON}
            disabled={provision.running}
            onClick={() => void storyStore.provision()}
          >
            {provision.running ? 'Provisioning…' : 'Provision'}
          </button>
          <button
            type="button"
            className={STORY_SECONDARY_BUTTON}
            disabled={!scenario?.exists || runNow.running || runNow.polling}
            onClick={() => void storyStore.runNow()}
          >
            {runNow.running ? 'Starting…' : runNow.polling ? 'Running… (watching status)' : 'Run Now'}
          </button>
          {runNow.polling && (
            <button type="button" className={STORY_SECONDARY_BUTTON} onClick={() => storyStore.stopPolling()}>
              Stop watching
            </button>
          )}
          <button
            type="button"
            className={STORY_SECONDARY_BUTTON}
            onClick={() => void storyStore.loadStatus(true)}
          >
            Refresh status
          </button>
          {(provision.error || runNow.error) && (
            <span className="text-xs text-[var(--neon-red)]">{provision.error || runNow.error}</span>
          )}
        </div>

        {provision.result && (
          <div className="text-sm space-y-1">
            <div className="font-medium text-[var(--text-primary)]">
              Provision {provision.result.ok ? 'succeeded' : 'had errors'}
            </div>
            <ul className="text-xs text-[var(--text-secondary)] space-y-0.5">
              {provision.result.steps.map((step) => (
                <li key={step.step} className="font-mono">
                  {step.step}: <span className={step.status === 'error' ? 'text-[var(--neon-red)]' : ''}>{step.status}</span>
                  {step.message ? ` — ${step.message}` : ''}
                </li>
              ))}
            </ul>
          </div>
        )}

        <p className="text-xs text-[var(--text-muted)]">
          Failure emails: the scenario emails <span className="font-mono">{data?.alertEmail}</span> whenever
          any host or source fails at any level (the collect macro raises, the single step does not proceed
          on failure, and one END_OF_RUN reporter fires on outcome ≠ SUCCESS). Known limitation: a trigger
          that never fires sends no email — check “Last Run” above if the scenario looks idle. Hosts in the
          fleet: {(data?.hosts ?? []).map((h) => h.label).join(', ') || '—'}.
        </p>
      </StoryCard>

      <DataGrid
        rows={data?.ingest ?? []}
        columns={INGEST_COLUMNS}
        rowKey={(r) => `${r.instance_id}:${r.source}`}
        title="Collection status (story.ingest_runs)"
        lifecycle={slotLifecycle(status, (data?.ingest ?? []).length === 0)}
        emptyMessage="No collections recorded yet — press Run Now (or wait for the daily trigger)."
        defaultSortColumnId="instance"
        defaultSortDir="asc"
        scroll={{ maxH: '24rem' }}
        showRowCount
      />
    </div>
  );
}
