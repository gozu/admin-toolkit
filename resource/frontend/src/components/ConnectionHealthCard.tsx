import { useCallback, useMemo, useRef, useState } from 'react';
import { useDiag } from '../context/DiagContext';
import { fetchRaw, getBackendUrl } from '../utils/api';
import { parseSseStream } from '../utils/sseStream';
import { DataGrid } from './common/DataGrid';
import { Spinner } from './common/Spinner';
import { StatTile } from './common/StatTile';
import type { ColumnDef } from '../utils/dataGridTypes';
import type { ConnectionHealthResult, ConnectionAuditResult } from '../types';

type ErrorCategory =
  | 'missing_config'
  | 'missing_credentials'
  | 'invalid_credentials'
  | 'unreachable';

const CATEGORY_LABELS: Record<ErrorCategory, string> = {
  missing_config: 'Missing Configuration',
  missing_credentials: 'Missing Credentials',
  invalid_credentials: 'Invalid Credentials',
  unreachable: 'Unreachable / Driver Error',
};

const CATEGORY_COLORS: Record<ErrorCategory, string> = {
  missing_config: 'var(--neon-red)',
  missing_credentials: 'amber-400',
  invalid_credentials: 'var(--neon-red)',
  unreachable: 'var(--text-muted)',
};

const CATEGORY_ORDER: ErrorCategory[] = [
  'missing_config',
  'missing_credentials',
  'invalid_credentials',
  'unreachable',
];

type AuditSeverity = 'critical' | 'warning' | 'info';

const AUDIT_SEVERITY_ORDER: AuditSeverity[] = ['critical', 'warning', 'info'];

const AUDIT_SEVERITY_LABELS: Record<AuditSeverity, string> = {
  critical: 'Critical',
  warning: 'Warning',
  info: 'Info',
};

const AUDIT_SEVERITY_COLORS: Record<AuditSeverity, string> = {
  critical: 'var(--neon-red)',
  warning: 'amber-400',
  info: 'var(--text-muted)',
};

/** One row of the merged issues table: a health-test failure or an audit finding. */
interface HealthIssueRow {
  name: string;
  type: string;
  source: 'Health test' | 'Config audit';
  category: string;
  categoryColor: string;
  categoryRank: number;
  /** Error text (health) or joined findings (audit) — sort/search value. */
  details: string;
  /** Audit findings rendered as a bullet list; empty for health failures. */
  issues: string[];
}

function classifyError(error: string): ErrorCategory {
  const lower = error.toLowerCase();
  if (/does not have credentials|user .* does not have/.test(lower)) return 'missing_credentials';
  if (/should not be left blank|missing .* parameter|no models selected|not defined/.test(lower))
    return 'missing_config';
  if (
    /password authentication failed|incorrect username or password|invalid.*credentials|security token.*invalid|expired|failed to get access token|unauthorized_client|trial has ended|cannot invoke.*null/.test(
      lower,
    )
  )
    return 'invalid_credentials';
  return 'unreachable';
}

export function ConnectionHealthCard() {
  const { state, setParsedData, setFocusedConnectionFilter, setActivePage } = useDiag();
  const { parsedData } = state;

  const navigateToInsights = useCallback(
    (name: string) => {
      setFocusedConnectionFilter({ name });
      setActivePage('connections-insights');
    },
    [setFocusedConnectionFilter, setActivePage],
  );

  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const results = useMemo(() => parsedData.connectionHealth || [], [parsedData.connectionHealth]);
  const total = parsedData.connectionHealthTotal ?? null;

  const dssBaseUrl = useMemo(() => {
    const bUrl = getBackendUrl('/');
    try {
      const u = new URL(bUrl, window.location.origin);
      return `${u.protocol}//${u.host}`;
    } catch {
      return '';
    }
  }, []);

  const rescan = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setScanning(true);
    setError(null);
    setParsedData({ connectionHealth: [], connectionHealthTotal: null });

    try {
      const response = await fetchRaw('/api/connections/health', { signal: controller.signal });

      if (!response.ok || !response.body) {
        const body = await response.text();
        let msg = `Scan failed: ${response.status} ${response.statusText}`;
        try {
          msg = (JSON.parse(body) as { error?: string }).error || msg;
        } catch {
          /* body not JSON — keep default message */
        }
        throw new Error(msg);
      }

      const collected: ConnectionHealthResult[] = [];
      for await (const { event, payload } of parseSseStream(response.body)) {
        const data = payload as Record<string, unknown>;
        if (event === 'error') {
          throw new Error(String(data.error || 'Scan error'));
        } else if (event === 'init') {
          setParsedData({ connectionHealthTotal: Number(data.total) });
        } else if (event === 'conn') {
          collected.push(data as unknown as ConnectionHealthResult);
          setParsedData({ connectionHealth: [...collected] });
        }
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setScanning(false);
      abortRef.current = null;
    }
  }, [setParsedData]);

  const abortScan = useCallback(() => {
    abortRef.current?.abort();
    setScanning(false);
  }, []);

  const failedConnections = results.filter((r) => r.status === 'fail');
  const okCount = results.filter((r) => r.status === 'ok').length;
  const skippedCount = results.filter((r) => r.status === 'skipped').length;
  const hasResults = results.length > 0;
  const isLoading = scanning || (hasResults && total !== null && results.length < total);

  const auditFindings: ConnectionAuditResult[] = useMemo(
    () => parsedData.connectionAudit || [],
    [parsedData.connectionAudit],
  );
  const hasAudit = auditFindings.some((f) => f.configIssues.length > 0);

  // One merged table: health-test failures ∪ config-audit findings.
  const issueRows = useMemo<HealthIssueRow[]>(() => {
    const rows: HealthIssueRow[] = [];
    for (const c of results) {
      if (c.status !== 'fail') continue;
      const cat = classifyError(c.error || '');
      rows.push({
        name: c.name,
        type: c.type,
        source: 'Health test',
        category: CATEGORY_LABELS[cat],
        categoryColor: CATEGORY_COLORS[cat],
        categoryRank: CATEGORY_ORDER.indexOf(cat),
        details: c.error || '',
        issues: [],
      });
    }
    for (const f of auditFindings) {
      if (f.configIssues.length === 0) continue;
      rows.push({
        name: f.name,
        type: f.type,
        source: 'Config audit',
        category: AUDIT_SEVERITY_LABELS[f.severity],
        categoryColor: AUDIT_SEVERITY_COLORS[f.severity],
        categoryRank: CATEGORY_ORDER.length + AUDIT_SEVERITY_ORDER.indexOf(f.severity),
        details: f.configIssues.join('; '),
        issues: f.configIssues,
      });
    }
    return rows;
  }, [results, auditFindings]);

  const issueColumns = useMemo<ColumnDef<HealthIssueRow>[]>(
    () => [
      {
        id: 'name',
        label: 'Connection',
        defaultSortDir: 'asc',
        render: (r) => (
          <span className="whitespace-nowrap">
            <button
              type="button"
              onClick={() => navigateToInsights(r.name)}
              className="bg-transparent p-0 text-[var(--neon-cyan)] hover:underline focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--neon-cyan)] rounded-sm"
              title={`Open ${r.name} in Insights`}
            >
              {r.name}
            </button>
            <a
              href={`${dssBaseUrl}/admin/connections/${encodeURIComponent(r.name)}/`}
              target="_blank"
              rel="noopener noreferrer"
              title="Open in DSS"
              aria-label={`Open ${r.name} in DSS`}
              className="ml-1 text-[var(--text-muted)] hover:text-[var(--neon-cyan)]"
            >
              ↗
            </a>
          </span>
        ),
        sortValue: (r) => r.name.toLowerCase(),
      },
      {
        id: 'type',
        label: 'Type',
        defaultSortDir: 'asc',
        render: (r) => (
          <span className="text-[var(--text-secondary)] whitespace-nowrap">{r.type}</span>
        ),
        sortValue: (r) => r.type,
      },
      {
        id: 'source',
        label: 'Source',
        defaultSortDir: 'asc',
        render: (r) => (
          <span className="text-xs text-[var(--text-secondary)] whitespace-nowrap">{r.source}</span>
        ),
        sortValue: (r) => r.source,
      },
      {
        id: 'category',
        label: 'Category',
        defaultSortDir: 'asc',
        render: (r) => (
          <span
            className="text-sm font-semibold whitespace-nowrap"
            style={{ color: r.categoryColor }}
          >
            {r.category}
          </span>
        ),
        sortValue: (r) => r.categoryRank,
      },
      {
        id: 'details',
        label: 'Details',
        defaultSortDir: 'asc',
        render: (r) =>
          r.issues.length > 0 ? (
            <ul className="list-disc list-inside space-y-0.5 text-xs leading-relaxed text-[var(--text-muted)]">
              {r.issues.map((issue, idx) => (
                <li key={idx}>{issue}</li>
              ))}
            </ul>
          ) : (
            <span className="block max-w-[500px] text-xs leading-relaxed text-[var(--text-muted)]">
              {r.details}
            </span>
          ),
        sortValue: (r) => r.details.toLowerCase(),
      },
    ],
    [dssBaseUrl, navigateToInsights],
  );

  return (
    <div id="connection-health-card" className="space-y-4">
      {/* Header */}
      <section className="glass-card p-4">
        <h3 className="text-lg font-semibold text-[var(--text-primary)]">Connection Health</h3>
        <div className="mt-3 flex items-center gap-3">
          {/* Rescan kept as a re-trigger only — deemphasized. */}
          <button
            onClick={rescan}
            disabled={scanning}
            className="px-3 py-1 rounded-md text-xs font-medium text-[var(--text-secondary)] border border-[var(--text-tertiary)]/30 hover:bg-[var(--bg-glass-hover)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {scanning ? 'Scanning…' : 'Rescan'}
          </button>
        </div>
      </section>

      {/* Progress */}
      {isLoading && (
        <section className="glass-card p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
              <Spinner />
              {total !== null
                ? `Testing connections\u2026 ${results.length} / ${total}`
                : 'Discovering connections\u2026'}
            </div>
            <button
              onClick={abortScan}
              className="px-3 py-1 rounded-md text-xs font-medium text-[var(--text-secondary)] border border-[var(--text-tertiary)]/30 hover:bg-[var(--bg-glass-hover)] transition-colors"
            >
              Abort
            </button>
          </div>
        </section>
      )}

      {/* Error */}
      {error && (
        <section className="glass-card p-4">
          <div className="text-sm text-[var(--neon-red)]">
            <span className="font-medium">Scan error:</span> {error}
          </div>
        </section>
      )}

      {/* Stats */}
      {hasResults && (
        <section className="glass-card p-4">
          <div className="grid grid-cols-4 gap-4">
            <StatTile
              value={total !== null && isLoading ? `${results.length} / ${total}` : results.length}
              label="Tested"
              valueClassName="tabular-nums text-[var(--text-primary)]"
            />
            <StatTile
              value={okCount}
              label="Healthy"
              valueClassName="tabular-nums text-[var(--neon-green)]"
            />
            <StatTile
              value={failedConnections.length}
              label="Failed"
              valueClassName="tabular-nums text-[var(--neon-red)]"
            />
            <StatTile
              value={skippedCount}
              label="Skipped"
              valueClassName="tabular-nums text-[var(--text-muted)]"
            />
          </div>
        </section>
      )}

      {/* Merged issues table: health-test failures + configuration-audit findings */}
      {(hasResults || hasAudit) && (
        <section className="glass-card p-4">
          <h4 className="text-sm font-semibold text-[var(--text-primary)] mb-1">Issues</h4>
          <p className="text-xs text-[var(--text-muted)] mb-3">
            Health-test failures and configuration-audit findings (recommended settings for
            fast-write, details readability, HDFS interface, and default connections).
          </p>
          {issueRows.length === 0 ? (
            <div className="py-6 text-center text-sm text-[var(--text-muted)]">
              {isLoading
                ? 'Scanning. Failed connections will appear here as they are found.'
                : 'All testable connections are healthy.'}
            </div>
          ) : (
            <DataGrid
              rows={issueRows}
              columns={issueColumns}
              rowKey={(r) => `${r.source}:${r.name}`}
              defaultSortColumnId="category"
              defaultSortDir="asc"
              rowClassName={() => '[&>td]:align-top'}
              scroll={{ maxH: '60vh' }}
            />
          )}
        </section>
      )}
    </div>
  );
}
