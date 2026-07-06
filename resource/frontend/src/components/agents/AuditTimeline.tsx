import { useEffect, useRef, useState } from 'react';
import { fetchJson } from '../../utils/api';
import { DataGrid } from '../common/DataGrid';
import { InfoDot } from '../common/InfoDot';
import { dssLinkForAction, hostBaseUrl, humanTarget, targetTitle } from '../../utils/agentLinks';

interface AuditRow {
  id: number;
  ts: string;
  agent: string;
  host: string;
  action: string;
  target: unknown;
  params: unknown;
  status: string;
  result_snippet?: string;
}

function ExternalLinkIcon() {
  return (
    <svg
      className="inline-block h-3 w-3 shrink-0 opacity-60"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6M15 3h6v6M10 14L21 3" />
    </svg>
  );
}

function provenanceLabel(params: unknown): string | null {
  if (!params || typeof params !== 'object') return null;
  const p = params as Record<string, unknown>;
  if (p.itemId || p.batchId) {
    return [p.batchId, p.itemId].filter(Boolean).join(' / ');
  }
  return null;
}

/**
 * Audit trail table. Action/target cells deep-link into the native DSS UI
 * where a page exists; the params column surfaces action-item provenance.
 * `focusAuditId` (from an ExecutionCard click) expands the panel, refetches,
 * and scroll+flashes the row.
 */
export function AuditTimeline({ focusAuditId }: { focusAuditId: number | null }) {
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  const [reason, setReason] = useState<string | null>(null);
  const [manualOpen, setManualOpen] = useState<boolean | null>(null);
  const [flashId, setFlashId] = useState<number | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // A focus request (execution card "audit #N" click) force-opens the panel
  // until the user toggles it themselves.
  const open = manualOpen ?? focusAuditId != null;

  // Refetch on every new focus request — the focused row is brand new.
  useEffect(() => {
    fetchJson<{ available: boolean; actions: AuditRow[]; reason?: string }>('/api/agents/actions?limit=50')
      .then((data) => {
        setRows(data.actions || []);
        setReason(data.available ? null : data.reason || null);
      })
      .catch((err) => setReason(String(err)));
  }, [focusAuditId]);

  useEffect(() => {
    if (focusAuditId == null || !open || !rows) return;
    const el = containerRef.current?.querySelector(`.audit-row-${focusAuditId}`);
    if (el) {
      el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      setFlashId(focusAuditId);
      const timer = setTimeout(() => setFlashId(null), 1600);
      return () => clearTimeout(timer);
    }
  }, [focusAuditId, open, rows]);

  if (reason || !rows || rows.length === 0) return null;

  return (
    <div className="glass-card p-3" ref={containerRef}>
      <div className="flex w-full items-center justify-between gap-2">
        <button onClick={() => setManualOpen(!open)} className="flex flex-1 items-center gap-1.5 text-left">
          <span className="text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
            Action audit trail
          </span>
        </button>
        <InfoDot eduId="concept.audit-trail" />
        <button onClick={() => setManualOpen(!open)} className="text-xs text-[var(--text-tertiary)]">
          {rows.length} action{rows.length === 1 ? '' : 's'} {open ? '▾' : '▸'}
        </button>
      </div>
      {open && (
        <div className="mt-2">
          <DataGrid<AuditRow>
            rows={rows}
            rowKey={(row) => String(row.id)}
            scroll={{ maxH: 'max-h-64' }}
            rowClassName={(row) =>
              `audit-row-${row.id}${row.id === flashId ? ' animate-pulse bg-[var(--accent-muted)]' : ''}`
            }
            columns={[
              { id: 'id', label: '#', mono: true, render: (row) => `#${row.id}`,
                sortValue: (row) => row.id, defaultSortDir: 'desc' },
              { id: 'ts', label: 'When', render: (row) => row.ts.slice(0, 16).replace('T', ' '),
                sortValue: (row) => row.ts },
              { id: 'action', label: 'Action', mono: true,
                render: (row) => {
                  const link = dssLinkForAction(row.action, row.target, row.host);
                  return link ? (
                    <a
                      href={link}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 hover:text-[var(--accent)] hover:underline"
                    >
                      {row.action} <ExternalLinkIcon />
                    </a>
                  ) : (
                    row.action
                  );
                },
                sortValue: (row) => row.action },
              { id: 'target', label: 'Target',
                render: (row) => {
                  const link = dssLinkForAction(row.action, row.target, row.host);
                  const inner = (
                    <span className="block max-w-[16rem] truncate" title={targetTitle(row.target)}>
                      {humanTarget(row.target)}
                    </span>
                  );
                  return link ? (
                    <a href={link} target="_blank" rel="noreferrer" className="hover:text-[var(--accent)] hover:underline">
                      {inner}
                    </a>
                  ) : (
                    inner
                  );
                } },
              { id: 'provenance', label: 'From item',
                render: (row) => {
                  const label = provenanceLabel(row.params);
                  return label ? (
                    <span className="font-mono text-[10px] text-[var(--text-muted)]" title={JSON.stringify(row.params)}>
                      {label}
                    </span>
                  ) : (
                    ''
                  );
                } },
              { id: 'host', label: 'Host',
                render: (row) => (
                  <a
                    href={hostBaseUrl(row.host)}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 hover:text-[var(--accent)] hover:underline"
                  >
                    {row.host} <ExternalLinkIcon />
                  </a>
                ),
                sortValue: (row) => row.host },
              { id: 'status', label: 'Status', render: (row) => row.status,
                sortValue: (row) => row.status,
                cellClassName: (row) =>
                  row.status === 'ok' ? 'text-[var(--accent)]' : 'text-[var(--danger)]' },
            ]}
            defaultSortColumnId="id"
            defaultSortDir="desc"
          />
        </div>
      )}
    </div>
  );
}
