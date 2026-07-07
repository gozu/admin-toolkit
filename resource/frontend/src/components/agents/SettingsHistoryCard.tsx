import { useEffect, useState } from 'react';
import { fetchJson } from '../../utils/api';
import { DataGrid } from '../common/DataGrid';

interface SettingsChangeRow {
  id: number;
  ts: string;
  host: string;
  item_key: string;
  before: unknown;
  after: unknown;
  agent?: string | null;
  audit_id?: number | null;
}

function fmt(value: unknown): string {
  if (value === null || value === undefined) return 'unset';
  return String(value);
}

/** Prefill for the ops-actuator composer: restoring = planning a normal
 *  k8s-exec-config-tune whose change is the history row's `before` value —
 *  the usual plan → confirm → execute flow applies (nothing runs from here). */
function restorePrompt(row: SettingsChangeRow): string {
  const [kind, name, key] = row.item_key.split(':');
  if (kind !== 'execConfig' || !name || !key) {
    return `Plan a restore of setting ${row.item_key} back to ${fmt(row.before)} (history row #${row.id}).`;
  }
  const restoreValue = row.before === null || row.before === undefined ? -1 : row.before;
  return (
    `Restore a setting from history: plan a k8s-exec-config-tune on execution config "${name}" ` +
    `setting ${key} back to ${restoreValue}${restoreValue === -1 ? ' (unset)' : ''}. ` +
    `This reverts settings-change history row #${row.id} (host ${row.host}), which changed it ` +
    `${fmt(row.before)} → ${fmt(row.after)}. If the plan's current value differs from ${fmt(row.after)}, ` +
    `the setting has drifted since that change — call that out before I confirm.`
  );
}

/**
 * Settings-change history (agents.settings_changes): every settings-mutating
 * agent action records prior value + new value, restorable via the normal
 * actuator plan/confirm flow. "Restore…" prefills the actuator composer.
 */
export function SettingsHistoryCard({ onRestore }: { onRestore: (prompt: string) => void }) {
  const [rows, setRows] = useState<SettingsChangeRow[] | null>(null);
  const [reason, setReason] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    fetchJson<{ available: boolean; changes: SettingsChangeRow[]; reason?: string }>(
      '/api/agents/settings-history',
    )
      .then((data) => {
        setRows(data.changes || []);
        setReason(data.available ? null : data.reason || null);
      })
      .catch((err) => setReason(String(err)));
  }, []);

  if (reason || !rows || rows.length === 0) return null;

  return (
    <div className="glass-card p-3">
      <div className="flex w-full items-center justify-between gap-2">
        <button onClick={() => setOpen(!open)} className="flex flex-1 items-center gap-1.5 text-left">
          <span className="text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
            Settings-change history
          </span>
        </button>
        <button onClick={() => setOpen(!open)} className="text-xs text-[var(--text-tertiary)]">
          {rows.length} change{rows.length === 1 ? '' : 's'} {open ? '▾' : '▸'}
        </button>
      </div>
      {open && (
        <div className="mt-2">
          <DataGrid<SettingsChangeRow>
            rows={rows}
            rowKey={(row) => String(row.id)}
            scroll={{ maxH: 'max-h-64' }}
            columns={[
              { id: 'id', label: '#', mono: true, render: (row) => `#${row.id}`,
                sortValue: (row) => row.id, defaultSortDir: 'desc' },
              { id: 'ts', label: 'When', render: (row) => row.ts.slice(0, 16).replace('T', ' '),
                sortValue: (row) => row.ts },
              { id: 'item', label: 'Item', mono: true,
                render: (row) => (
                  <span className="block max-w-[16rem] truncate" title={row.item_key}>
                    {row.item_key}
                  </span>
                ),
                sortValue: (row) => row.item_key },
              { id: 'change', label: 'Change', mono: true,
                render: (row) => `${fmt(row.before)} → ${fmt(row.after)}` },
              { id: 'host', label: 'Host', render: (row) => row.host, sortValue: (row) => row.host },
              { id: 'agent', label: 'By', render: (row) => row.agent || '' },
              { id: 'restore', label: '',
                render: (row) => (
                  <button
                    onClick={() => onRestore(restorePrompt(row))}
                    className="rounded px-2 py-0.5 text-xs text-[var(--accent)] transition-colors hover:bg-[var(--accent-muted)]"
                    title="Prefill the agent with a plan restoring this item to its prior value"
                  >
                    Restore…
                  </button>
                ) },
            ]}
            defaultSortColumnId="id"
            defaultSortDir="desc"
          />
          <p className="mt-1 text-[10px] text-[var(--text-muted)]">
            Restore prefills the agent with a plan reverting the item to its prior value —
            the usual plan → approve → execute flow applies. Last 50 changes kept per item.
          </p>
        </div>
      )}
    </div>
  );
}
