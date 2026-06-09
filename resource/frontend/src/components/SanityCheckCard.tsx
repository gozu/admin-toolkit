import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useDiag } from '../context/DiagContext';
import { getBackendUrl } from '../utils/api';
import { runSanityCheck } from '../state/sanityCheckScan';
import { Spinner } from './common/Spinner';
import { StatTile } from './common/StatTile';
import type { SanityCheckMessage } from '../types';

type Severity = 'ERROR' | 'WARNING' | 'INFO' | 'SUCCESS';

const SEVERITY_RANK: Record<Severity, number> = {
  ERROR: 0,
  WARNING: 1,
  INFO: 2,
  SUCCESS: 3,
};

const SEVERITY_COLORS: Record<Severity, string> = {
  ERROR: 'var(--neon-red)',
  WARNING: 'var(--neon-yellow)',
  INFO: 'var(--neon-cyan)',
  SUCCESS: 'var(--neon-green)',
};

function normalizeSeverity(s: string): Severity {
  return (['ERROR', 'WARNING', 'INFO', 'SUCCESS'] as Severity[]).includes(s as Severity)
    ? (s as Severity)
    : 'INFO';
}

// --- Grouping -----------------------------------------------------------

interface GroupedOccurrence {
  details: string;
  extraInfoSummary?: string | null;
  extraInfoDetails?: string | null;
}

interface GroupedMessage {
  code: string;
  severity: Severity;
  title: string;
  count: number;
  occurrences: GroupedOccurrence[];
}

function groupByCode(messages: SanityCheckMessage[]): GroupedMessage[] {
  const order: string[] = [];
  const map = new Map<string, GroupedMessage>();

  for (const m of messages) {
    const key = m.code || '(no-code)';
    let g = map.get(key);
    if (!g) {
      g = {
        code: key,
        severity: normalizeSeverity(m.severity),
        title: m.title || key,
        count: 0,
        occurrences: [],
      };
      map.set(key, g);
      order.push(key);
    }
    g.count += 1;
    g.occurrences.push({
      details: m.details,
      extraInfoSummary: m.extraInfoSummary,
      extraInfoDetails: m.extraInfoDetails,
    });
    const sev = normalizeSeverity(m.severity);
    // Lower rank = more severe; promote to highest seen.
    if (SEVERITY_RANK[sev] < SEVERITY_RANK[g.severity]) {
      g.severity = sev;
    }
    if (!g.title && m.title) g.title = m.title;
  }

  return order.map((k) => map.get(k)!);
}

// --- HTML / text helpers ------------------------------------------------

function rewriteLinks(html: string, dssBaseUrl: string): string {
  return html.replace(/<a\b([^>]*)>/gi, (_match, attrs: string) => {
    let newAttrs = attrs;
    if (dssBaseUrl) {
      newAttrs = newAttrs.replace(
        /\bhref\s*=\s*"(\/[^"]*)"/i,
        (_m: string, h: string) => `href="${dssBaseUrl}${h}"`,
      );
      newAttrs = newAttrs.replace(
        /\bhref\s*=\s*'(\/[^']*)'/i,
        (_m: string, h: string) => `href='${dssBaseUrl}${h}'`,
      );
    }
    newAttrs = newAttrs.replace(/\btarget\s*=\s*("[^"]*"|'[^']*')/i, '');
    newAttrs = newAttrs.replace(/\brel\s*=\s*("[^"]*"|'[^']*')/i, '');
    return `<a${newAttrs} target="_blank" rel="noopener noreferrer">`;
  });
}

function stripOuterPre(html: string): string {
  return html.replace(/<\/?pre\b[^>]*>/gi, '').replace(/\r\n/g, '\n');
}

function capitalize(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

// If every details string has exactly one pair of single quotes AND the
// non-quoted scaffolding is identical across occurrences, return the list
// of quoted tokens. Otherwise return null.
function extractQuotedTokens(detailsList: string[]): string[] | null {
  if (detailsList.length === 0) return null;
  const split = detailsList.map((d) => d.split("'"));
  if (!split.every((s) => s.length === 3)) return null;
  const refBefore = split[0][0];
  const refAfter = split[0][2];
  for (const s of split) {
    if (s[0] !== refBefore || s[2] !== refAfter) return null;
  }
  return split.map((s) => s[1]);
}

// Parse the <pre>-block GIT-unmigrated layout. Preserves anything we
// don't recognise verbatim so DSS truncation markers (e.g. "(... 402 more)")
// are not silently dropped.
function parseGitUnmigrated(
  occs: GroupedOccurrence[],
  projectKeys: Set<string>,
  dssBaseUrl: string,
): ReactNode[] {
  const items: ReactNode[] = [];
  for (const o of occs) {
    const raw = stripOuterPre(o.extraInfoDetails || '').trim();
    if (!raw) continue;
    let currentProject = '';
    for (const rawLine of raw.split('\n')) {
      const l = rawLine.replace(/\r$/, '');
      if (!l.trim()) continue;
      const header = l.match(/^(\S.*):\s*$/);
      if (header) {
        currentProject = header[1];
        continue;
      }
      const branch = l.match(/^\s*-\s*(\S+)\s+(\(.*\))\s*$/);
      if (branch && currentProject) {
        const suffix = `: ${capitalize(branch[1])} ${branch[2]}`;
        if (dssBaseUrl && projectKeys.has(currentProject)) {
          items.push(
            <>
              <a
                href={`${dssBaseUrl}/projects/${encodeURIComponent(currentProject)}/`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[var(--neon-cyan)] hover:underline"
              >
                {currentProject}
              </a>
              {suffix}
            </>,
          );
        } else {
          items.push(`${currentProject}${suffix}`);
        }
      } else {
        items.push(l.trim());
      }
    }
  }
  return items;
}

// --- Per-code rendering -------------------------------------------------

function renderTokenLink(
  code: string,
  token: string,
  projectNameToKey: Map<string, string>,
  dssBaseUrl: string,
): ReactNode {
  const linkClass = 'text-[var(--neon-cyan)] hover:underline';

  if (!dssBaseUrl) {
    if (code === 'WARN_CLUSTERS_NONE_SELECTED_PROJECT') {
      const key = projectNameToKey.get(token) || token;
      return <code>{key}</code>;
    }
    return <span>{token}</span>;
  }

  if (/^(WARN_CONNECTION_|ERR_CONNECTION_)/.test(code)) {
    return (
      <a
        href={`${dssBaseUrl}/admin/connections/edit/${encodeURIComponent(token)}`}
        target="_blank"
        rel="noopener noreferrer"
        className={linkClass}
      >
        {token}
      </a>
    );
  }
  if (code === 'WARN_CLUSTERS_NONE_SELECTED_PROJECT') {
    const key = projectNameToKey.get(token) || token;
    return (
      <a
        href={`${dssBaseUrl}/projects/${encodeURIComponent(key)}/`}
        target="_blank"
        rel="noopener noreferrer"
        className={linkClass}
      >
        <code>{key}</code>
      </a>
    );
  }
  if (/^WARN_CLUSTERS_/.test(code)) {
    return (
      <a
        href={`${dssBaseUrl}/admin/clusters/${encodeURIComponent(token)}/`}
        target="_blank"
        rel="noopener noreferrer"
        className={linkClass}
      >
        {token}
      </a>
    );
  }
  if (/^(WARN_CODEENV_|ERR_CODEENV_)/.test(code)) {
    return (
      <a
        href={`${dssBaseUrl}/admin/code-envs/`}
        target="_blank"
        rel="noopener noreferrer"
        className={linkClass}
      >
        {token}
      </a>
    );
  }
  if (/^(WARN_USER_|WARN_USERS_|ERR_LICENSE_USER_)/.test(code)) {
    return (
      <a
        href={`${dssBaseUrl}/admin/users/${encodeURIComponent(token)}/`}
        target="_blank"
        rel="noopener noreferrer"
        className={linkClass}
      >
        {token}
      </a>
    );
  }
  if (/^(WARN_PROJECT_|ERR_PROJECT_)/.test(code)) {
    const key = projectNameToKey.get(token) || token;
    return (
      <a
        href={`${dssBaseUrl}/projects/${encodeURIComponent(key)}/`}
        target="_blank"
        rel="noopener noreferrer"
        className={linkClass}
      >
        <code>{key}</code>
      </a>
    );
  }
  return <span>{token}</span>;
}

function renderDetails(
  g: GroupedMessage,
  projectNameToKey: Map<string, string>,
  projectKeys: Set<string>,
  dssBaseUrl: string,
): ReactNode {
  if (g.occurrences.length === 0) {
    return <span className="text-[var(--text-muted)]">—</span>;
  }

  if (g.code === 'WARN_GIT_PROJECT_NOT_MIGRATED') {
    const items = parseGitUnmigrated(g.occurrences, projectKeys, dssBaseUrl);
    if (items.length > 0) {
      return (
        <ul className="list-disc pl-5 space-y-0.5 marker:text-[var(--text-muted)]">
          {items.map((item, i) => (
            <li key={i} className="font-mono text-sm break-all">
              {item}
            </li>
          ))}
        </ul>
      );
    }
  }

  const detailsOnly = g.occurrences.map((o) => o.details || '');
  const tokens = extractQuotedTokens(detailsOnly);
  const mono = tokens !== null;

  return (
    <ul className="list-disc pl-5 space-y-0.5 marker:text-[var(--text-muted)]">
      {g.occurrences.map((o, i) => {
        let head: ReactNode;
        if (tokens) {
          head = renderTokenLink(g.code, tokens[i], projectNameToKey, dssBaseUrl);
        } else {
          head = (
            <span
              className="sanity-html-content"
              dangerouslySetInnerHTML={{ __html: rewriteLinks(o.details || '', dssBaseUrl) }}
            />
          );
        }

        return (
          <li key={i} className={mono ? 'font-mono text-sm break-all' : 'break-words text-sm'}>
            {head}
            {o.extraInfoSummary && (
              <div className="pl-3 mt-1 text-xs font-sans font-medium text-[var(--text-primary)] opacity-80">
                {o.extraInfoSummary}
              </div>
            )}
            {o.extraInfoDetails && (
              <div
                className="pl-3 mt-0.5 text-xs font-sans sanity-html-content"
                dangerouslySetInnerHTML={{ __html: rewriteLinks(o.extraInfoDetails, dssBaseUrl) }}
              />
            )}
          </li>
        );
      })}
    </ul>
  );
}

// --- Component ----------------------------------------------------------

export function SanityCheckCard() {
  const { state, setParsedData } = useDiag();
  const { parsedData } = state;

  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const autoRanRef = useRef(false);

  const results = useMemo(() => parsedData.sanityCheck || [], [parsedData.sanityCheck]);
  const hasResults = results.length > 0;

  const projectNameToKey = useMemo(() => {
    const m = new Map<string, string>();
    for (const p of parsedData.projects || []) {
      if (p.name && p.key) m.set(p.name, p.key);
    }
    return m;
  }, [parsedData.projects]);

  const projectKeys = useMemo(() => {
    const s = new Set<string>();
    for (const p of parsedData.projects || []) {
      if (p.key) s.add(p.key);
    }
    return s;
  }, [parsedData.projects]);

  const dssBaseUrl = useMemo(() => {
    const bUrl = getBackendUrl('/');
    try {
      const u = new URL(bUrl, window.location.origin);
      return `${u.protocol}//${u.host}`;
    } catch {
      return '';
    }
  }, []);

  const rescan = useCallback(
    async (opts: { force?: boolean } = {}) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setScanning(true);
      setError(null);
      const startedAt = new Date().toISOString();
      setParsedData({
        sanityCheckLoading: {
          phase: 'running',
          startedAt,
          progressPct: 0,
          message: 'Running sanity check',
          updatedAt: startedAt,
        },
      });
      if (opts.force) {
        setParsedData({ sanityCheck: [], sanityCheckMaxSeverity: null });
      }

      try {
        const result = await runSanityCheck({ force: opts.force, signal: controller.signal });
        setParsedData({
          sanityCheck: result.messages,
          sanityCheckMaxSeverity: result.maxSeverity,
          sanityCheckLoading: {
            phase: 'done',
            startedAt,
            finishedAt: new Date().toISOString(),
            isEmpty: result.messages.length === 0,
            message: `${result.messages.length} message(s)`,
          },
        });
      } catch (err) {
        if ((err as Error).name === 'AbortError') {
          setParsedData({ sanityCheckLoading: { phase: 'queued' } });
          return;
        }
        const msg = err instanceof Error ? err.message : String(err);
        setError(msg);
        setParsedData({
          sanityCheckLoading: {
            phase: 'error',
            startedAt,
            finishedAt: new Date().toISOString(),
            error: msg,
            progressPct: 0,
          },
        });
      } finally {
        setScanning(false);
        abortRef.current = null;
      }
    },
    [setParsedData],
  );

  useEffect(() => {
    if (autoRanRef.current) return;
    if (hasResults) return;
    autoRanRef.current = true;
    void rescan();
  }, [hasResults, rescan]);

  const counts = useMemo(() => {
    const c: Record<Severity, number> = { ERROR: 0, WARNING: 0, INFO: 0, SUCCESS: 0 };
    for (const m of results) {
      const sev = normalizeSeverity(m.severity);
      c[sev] += 1;
    }
    return c;
  }, [results]);

  const rows = useMemo(() => groupByCode(results), [results]);

  return (
    <div className="space-y-4">
      {/* Header */}
      <section className="glass-card p-4">
        <h3 className="text-lg font-semibold text-[var(--text-primary)]">Instance Sanity Check</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Runs a fresh DSS instance sanity check — connections, clusters, code envs, security, etc.
          Results are always live.
        </p>
        <div className="mt-3 flex items-center gap-3">
          <button
            onClick={() => rescan({ force: true })}
            disabled={scanning}
            className="px-4 py-1.5 rounded-md text-sm font-medium bg-[var(--accent)] text-white hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {scanning ? 'Running...' : 'Rerun'}
          </button>
        </div>
      </section>

      {/* Progress */}
      {scanning && (
        <section className="glass-card p-4">
          <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
            <Spinner />
            Running sanity check&hellip;
          </div>
        </section>
      )}

      {/* Error */}
      {error && (
        <section className="glass-card p-4">
          <div className="text-sm text-[var(--neon-red)]">
            <span className="font-medium">Sanity check error:</span> {error}
          </div>
        </section>
      )}

      {/* Stats */}
      {hasResults && (
        <section className="glass-card p-4">
          <div className="grid grid-cols-4 gap-4">
            <StatTile value={results.length} label="Total" />
            <StatTile value={counts.ERROR} label="Errors" valueClassName="text-[var(--neon-red)]" />
            <StatTile
              value={counts.WARNING}
              label="Warnings"
              valueClassName="text-[var(--neon-yellow)]"
            />
            <StatTile value={counts.INFO} label="Info" valueClassName="text-[var(--neon-cyan)]" />
          </div>
        </section>
      )}

      {/* Grouped-by-code table */}
      {hasResults && (
        <section className="glass-card p-4">
          <div className="overflow-auto max-h-[70vh]">
            <table className="table-dark w-full">
              <thead>
                <tr>
                  <th className="w-1/3">Title</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.code}
                    className="hover:bg-[var(--bg-glass)] align-top"
                    style={{ borderLeft: `3px solid ${SEVERITY_COLORS[row.severity]}` }}
                  >
                    <td className="text-[var(--text-primary)] text-sm align-top">{row.title}</td>
                    <td className="text-[var(--text-secondary)] text-sm leading-relaxed align-top">
                      {renderDetails(row, projectNameToKey, projectKeys, dssBaseUrl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Empty state (after a successful run with no messages) */}
      {!scanning && !error && !hasResults && (
        <section className="glass-card p-4">
          <div className="py-6 text-center text-sm text-[var(--text-muted)]">
            Click <span className="text-[var(--text-secondary)] font-medium">Rerun</span> to run a
            sanity check.
          </div>
        </section>
      )}
    </div>
  );
}
