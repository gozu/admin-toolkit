import { useEffect, useMemo, useState } from 'react';
import { codeEnvComparisonScan } from '../state/codeEnvComparisonStore';
import { useTableSort } from '../hooks/useTableSort';
import { Spinner } from './common/Spinner';
import { dssUrls } from '../utils/codeEnvUsageLinks';
import type { CodeEnvCompareBlue, CodeEnvCompareYellow } from '../types';

type SectionKey = 'green' | 'purple' | 'blue' | 'yellow';

const SECTION_META: Record<
  SectionKey,
  { label: string; hint: string; color: string; badgeBg: string }
> = {
  green: {
    label: 'Exact Duplicates',
    hint: 'Identical packages, versions, and Python — safe to consolidate',
    color: 'var(--neon-green)',
    badgeBg: 'bg-[var(--neon-green)]',
  },
  purple: {
    label: 'Different Python Only',
    hint: 'Identical packages and versions — only the Python interpreter differs',
    color: 'var(--neon-purple)',
    badgeBg: 'bg-[var(--neon-purple)]',
  },
  blue: {
    label: 'Version Mismatches',
    hint: 'Same package set — one or more version differences',
    color: 'var(--neon-cyan)',
    badgeBg: 'bg-[var(--neon-cyan)]',
  },
  yellow: {
    label: 'Near Duplicates',
    hint: 'Almost identical — differ by exactly 1 package',
    color: 'var(--neon-amber)',
    badgeBg: 'bg-[var(--neon-amber)]',
  },
};

export function CodeEnvCompareTable() {
  const { data, loading, error, scanStarted } = codeEnvComparisonScan.use();
  const [expanded, setExpanded] = useState<Record<SectionKey, boolean>>({
    green: true,
    purple: true,
    blue: true,
    yellow: true,
  });

  useEffect(() => {
    if (!scanStarted) void codeEnvComparisonScan.load();
  }, [scanStarted]);

  const toggle = (key: SectionKey) => setExpanded((prev) => ({ ...prev, [key]: !prev[key] }));

  // Compute env counts per category
  const stats = useMemo(() => {
    if (!data) return null;
    const greenEnvs = new Set(data.green.flatMap((g) => g.envNames));
    const purpleEnvs = new Set(data.purple.flatMap((g) => g.envNames));
    const blueEnvs = new Set(data.blue.flatMap((g) => g.envNames));
    const yellowEnvs = new Set(data.yellow.flatMap((p) => [p.envA, p.envB]));
    const allAffected = new Set([...greenEnvs, ...purpleEnvs, ...blueEnvs, ...yellowEnvs]);
    return {
      green: { groups: data.green.length, envs: greenEnvs.size },
      purple: { groups: data.purple.length, envs: purpleEnvs.size },
      blue: { groups: data.blue.length, envs: blueEnvs.size },
      yellow: { groups: data.yellow.length, envs: yellowEnvs.size },
      totalAffected: allAffected.size,
    };
  }, [data]);

  const totalGroups =
    (data?.green.length || 0) +
    (data?.purple.length || 0) +
    (data?.blue.length || 0) +
    (data?.yellow.length || 0);

  // ── Shell states ──

  if (error) {
    return (
      <Shell>
        <div className="p-4 text-sm text-[var(--neon-red)]">{error}</div>
      </Shell>
    );
  }

  if (loading && !data) {
    return (
      <Shell>
        <div className="p-6 flex items-center gap-3 text-sm text-[var(--text-secondary)]">
          <Spinner size="h-4 w-4" color="border-[var(--neon-cyan)]" />
          Comparing package lists across code environments...
        </div>
      </Shell>
    );
  }

  if (!data || totalGroups === 0) {
    return (
      <Shell>
        <div className="p-6 text-center">
          <div className="text-[var(--neon-green)] text-2xl mb-1">&#10003;</div>
          <div className="text-sm text-[var(--text-secondary)]">
            {data
              ? `${data.analyzedCount} Python environments analyzed — no duplicates or near-duplicates found.`
              : 'No data available.'}
          </div>
        </div>
      </Shell>
    );
  }

  return (
    <div className="rounded-xl overflow-hidden">
      {/* Header */}
      <div className="px-5 py-4">
        <h4 className="text-lg font-semibold text-[var(--text-primary)]">Code Env Comparison</h4>
        <p className="text-xs text-[var(--text-muted)] mt-0.5">
          {data.analyzedCount} Python environments analyzed &middot; {stats!.totalAffected} involved
          in duplicates or near-duplicates
        </p>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 px-5 py-4">
        {(['green', 'purple', 'blue', 'yellow'] as const).map((key) => {
          const meta = SECTION_META[key];
          const s = stats![key];
          if (s.groups === 0) return null;
          return (
            <button
              key={key}
              type="button"
              onClick={() => toggle(key)}
              className="text-left rounded-lg p-3 hover:bg-[var(--bg-glass-hover)] transition-colors cursor-pointer"
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="w-2 h-2 rounded-full" style={{ backgroundColor: meta.color }} />
                <span className="text-xs font-medium text-[var(--text-secondary)]">
                  {meta.label}
                </span>
              </div>
              <div className="flex items-baseline gap-1.5">
                <span className="text-2xl font-bold tabular-nums" style={{ color: meta.color }}>
                  {s.groups}
                </span>
                <span className="text-xs text-[var(--text-muted)]">
                  {s.groups === 1 ? 'group' : 'groups'}
                </span>
              </div>
              <div className="text-[0.7rem] text-[var(--text-muted)] mt-0.5">
                {s.envs} env{s.envs !== 1 ? 's' : ''} affected
              </div>
            </button>
          );
        })}
      </div>

      {/* Sections */}
      <div className="divide-y divide-[var(--border-glass)]">
        {/* GREEN — Exact Duplicates */}
        {data.green.length > 0 && (
          <CollapsibleSection
            sectionKey="green"
            count={data.green.length}
            envCount={stats!.green.envs}
            expanded={expanded.green}
            onToggle={() => toggle('green')}
          >
            <div className="px-5 py-3 space-y-2">
              {data.green.map((group, idx) => (
                <GroupCard key={idx} color="var(--neon-green)">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-medium text-[var(--text-secondary)]">
                      {group.packageCount} packages &middot; Python {group.pythonVersion}
                    </span>
                    <span className="text-[0.65rem] uppercase tracking-wider font-semibold text-[var(--neon-green)]/70">
                      {group.envNames.length} identical envs
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {group.envNames.map((name) => (
                      <EnvBadge key={name} name={name} />
                    ))}
                  </div>
                </GroupCard>
              ))}
            </div>
          </CollapsibleSection>
        )}

        {/* PURPLE — Different Python */}
        {data.purple.length > 0 && (
          <CollapsibleSection
            sectionKey="purple"
            count={data.purple.length}
            envCount={stats!.purple.envs}
            expanded={expanded.purple}
            onToggle={() => toggle('purple')}
          >
            <div className="px-5 py-3 space-y-2">
              {data.purple.map((group, idx) => {
                // Group envs by their python version for display
                const byVersion: Record<string, string[]> = {};
                for (const name of group.envNames) {
                  const v = group.pythonVersions[name] || 'unknown';
                  (byVersion[v] ??= []).push(name);
                }
                return (
                  <GroupCard key={idx} color="var(--neon-purple)">
                    <div className="text-xs font-medium text-[var(--text-secondary)] mb-2">
                      {group.packageCount} packages &middot; {group.envNames.length} envs
                    </div>
                    <div className="space-y-2">
                      {Object.entries(byVersion)
                        .sort(([a], [b]) => a.localeCompare(b))
                        .map(([ver, names]) => (
                          <div key={ver}>
                            <div className="flex items-center gap-1.5 mb-1">
                              <span className="px-1.5 py-0.5 rounded text-[0.65rem] font-bold bg-[var(--neon-purple)]/15 text-[var(--neon-purple)] border border-[var(--neon-purple)]/25">
                                Python {ver}
                              </span>
                            </div>
                            <div className="flex flex-wrap gap-1.5 ml-0.5">
                              {names.map((n) => (
                                <EnvBadge key={n} name={n} />
                              ))}
                            </div>
                          </div>
                        ))}
                    </div>
                  </GroupCard>
                );
              })}
            </div>
          </CollapsibleSection>
        )}

        {/* BLUE — Version Mismatches */}
        {data.blue.length > 0 && (
          <CollapsibleSection
            sectionKey="blue"
            count={data.blue.length}
            envCount={stats!.blue.envs}
            expanded={expanded.blue}
            onToggle={() => toggle('blue')}
          >
            <div className="px-5 py-3 space-y-2">
              {data.blue.map((group, idx) => (
                <GroupCard key={idx} color="var(--neon-cyan)">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-medium text-[var(--text-secondary)]">
                      {group.packageCount} packages &middot; {group.envNames.length} envs
                    </span>
                    <span className="text-[0.65rem] text-[var(--neon-amber)]">
                      {group.diffCount} version{group.diffCount !== 1 ? 's' : ''} differ
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-1.5 mb-3">
                    {group.envNames.map((name) => (
                      <EnvBadge key={name} name={name} />
                    ))}
                  </div>
                  <VersionMatrixTable group={group} />
                </GroupCard>
              ))}
            </div>
          </CollapsibleSection>
        )}

        {/* YELLOW — Near Duplicates */}
        {data.yellow.length > 0 && (
          <CollapsibleSection
            sectionKey="yellow"
            count={data.yellow.length}
            envCount={stats!.yellow.envs}
            expanded={expanded.yellow}
            onToggle={() => toggle('yellow')}
          >
            <div className="px-5 py-3 space-y-2">
              {data.yellow.map((pair, idx) => (
                <GroupCard key={idx} color="var(--neon-amber)">
                  <div className="flex items-center gap-2 mb-2">
                    <EnvBadge name={pair.envA} />
                    <span className="text-[var(--text-muted)] text-sm">&harr;</span>
                    <EnvBadge name={pair.envB} />
                  </div>
                  <div className="space-y-1.5">
                    {pair.onlyInA.length > 0 && (
                      <div className="flex items-start gap-2 text-xs">
                        <span className="shrink-0 mt-0.5 px-1.5 py-0.5 rounded bg-[var(--neon-red)]/10 text-[var(--neon-red)] border border-[var(--neon-red)]/20 text-[0.65rem] font-medium">
                          only {pair.envA}
                        </span>
                        <span className="font-mono text-[var(--text-secondary)] leading-relaxed">
                          {pair.onlyInA.join(', ')}
                        </span>
                      </div>
                    )}
                    {pair.onlyInB.length > 0 && (
                      <div className="flex items-start gap-2 text-xs">
                        <span className="shrink-0 mt-0.5 px-1.5 py-0.5 rounded bg-[var(--neon-cyan)]/10 text-[var(--neon-cyan)] border border-[var(--neon-cyan)]/20 text-[0.65rem] font-medium">
                          only {pair.envB}
                        </span>
                        <span className="font-mono text-[var(--text-secondary)] leading-relaxed">
                          {pair.onlyInB.join(', ')}
                        </span>
                      </div>
                    )}
                    {pair.versionDiffs.length > 0 && (
                      <div className="mt-1">
                        <div className="text-[0.65rem] uppercase tracking-wider text-[var(--text-muted)] mb-1 font-medium">
                          Version differences
                        </div>
                        <PairVersionDiffTable pair={pair} />
                      </div>
                    )}
                  </div>
                </GroupCard>
              ))}
            </div>
          </CollapsibleSection>
        )}
      </div>
    </div>
  );
}

// ── Sub-components ──

function VersionMatrixTable({ group }: { group: CodeEnvCompareBlue }) {
  const { sortKey, sortDir, handleSort, sortIndicator } = useTableSort<string>({
    defaultKey: 'package',
    defaultDir: 'asc',
    ascDefaultKeys: ['package', ...group.envNames],
  });
  const rows = useMemo(() => {
    return Object.entries(group.diffs).sort(([pkgA, versionsA], [pkgB, versionsB]) => {
      const cmp =
        sortKey === 'package'
          ? pkgA.localeCompare(pkgB)
          : (versionsA[sortKey] || '').localeCompare(versionsB[sortKey] || '', undefined, {
              numeric: true,
            });
      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [group.diffs, sortKey, sortDir]);
  return (
    <div className="overflow-x-auto rounded-md border border-[var(--border-glass)]">
      <table className="w-full text-xs">
        <thead>
          <tr className="bg-[var(--bg-app)]">
            <th
              className="px-3 py-2 text-left font-semibold text-[var(--text-secondary)] whitespace-nowrap cursor-pointer select-none"
              onClick={() => handleSort('package')}
            >
              Package{sortIndicator('package')}
            </th>
            {group.envNames.map((name) => (
              <th
                key={name}
                className="px-3 py-2 text-left font-semibold text-[var(--text-secondary)] font-mono whitespace-nowrap cursor-pointer select-none"
                onClick={() => handleSort(name)}
              >
                {name}
                {sortIndicator(name)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(([pkg, versions], rowIdx) => {
            const vals = group.envNames.map((n) => versions[n] || '');
            // Find the most common value to dim it
            const freq: Record<string, number> = {};
            vals.forEach((v) => (freq[v] = (freq[v] || 0) + 1));
            const mostCommon = Object.entries(freq).sort(
              (a, b) => b[1] - a[1],
            )[0]?.[0];
            return (
              <tr
                key={pkg}
                className={
                  rowIdx % 2 === 0 ? 'bg-transparent' : 'bg-[var(--bg-elevated)]/30'
                }
              >
                <td className="px-3 py-1.5 font-mono text-[var(--text-primary)] whitespace-nowrap">
                  {pkg}
                </td>
                {group.envNames.map((name) => {
                  const v = versions[name] || '';
                  const isDiff = v !== mostCommon;
                  return (
                    <td
                      key={name}
                      className={`px-3 py-1.5 font-mono whitespace-nowrap ${
                        isDiff
                          ? 'text-[var(--neon-amber)] font-semibold'
                          : 'text-[var(--text-muted)]'
                      }`}
                    >
                      {v || (
                        <span className="text-[var(--text-muted)]/50 italic">
                          none
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PairVersionDiffTable({ pair }: { pair: CodeEnvCompareYellow }) {
  const { sortKey, sortDir, handleSort, sortIndicator } = useTableSort<'package' | 'a' | 'b'>({
    defaultKey: 'package',
    defaultDir: 'asc',
    ascDefaultKeys: ['package', 'a', 'b'],
  });
  const rows = useMemo(() => {
    return [...pair.versionDiffs].sort((x, y) => {
      const vx = sortKey === 'package' ? x.package : sortKey === 'a' ? x.versionA : x.versionB;
      const vy = sortKey === 'package' ? y.package : sortKey === 'a' ? y.versionA : y.versionB;
      const cmp = (vx || '').localeCompare(vy || '', undefined, { numeric: true });
      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [pair.versionDiffs, sortKey, sortDir]);
  return (
    <div className="overflow-x-auto rounded border border-[var(--border-glass)]">
      <table className="w-full text-xs">
        <thead>
          <tr className="bg-[var(--bg-app)]">
            <th
              className="px-3 py-1.5 text-left font-semibold text-[var(--text-secondary)] cursor-pointer select-none"
              onClick={() => handleSort('package')}
            >
              Package{sortIndicator('package')}
            </th>
            <th
              className="px-3 py-1.5 text-left font-semibold text-[var(--text-secondary)] font-mono cursor-pointer select-none"
              onClick={() => handleSort('a')}
            >
              {pair.envA}
              {sortIndicator('a')}
            </th>
            <th
              className="px-3 py-1.5 text-left font-semibold text-[var(--text-secondary)] font-mono cursor-pointer select-none"
              onClick={() => handleSort('b')}
            >
              {pair.envB}
              {sortIndicator('b')}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((d, di) => (
            <tr
              key={d.package}
              className={
                di % 2 === 0 ? 'bg-transparent' : 'bg-[var(--bg-elevated)]/30'
              }
            >
              <td className="px-3 py-1 font-mono text-[var(--text-primary)]">
                {d.package}
              </td>
              <td className="px-3 py-1 font-mono text-[var(--neon-amber)]">
                {d.versionA || (
                  <span className="text-[var(--text-muted)]/50 italic">
                    none
                  </span>
                )}
              </td>
              <td className="px-3 py-1 font-mono text-[var(--neon-amber)]">
                {d.versionB || (
                  <span className="text-[var(--text-muted)]/50 italic">
                    none
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl overflow-hidden">
      <div className="px-5 py-4">
        <h4 className="text-lg font-semibold text-[var(--text-primary)]">Code Env Comparison</h4>
      </div>
      {children}
    </div>
  );
}

function CollapsibleSection({
  sectionKey,
  count,
  envCount,
  expanded,
  onToggle,
  children,
}: {
  sectionKey: SectionKey;
  count: number;
  envCount: number;
  expanded: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  const meta = SECTION_META[sectionKey];
  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center gap-2.5 px-5 py-3 hover:bg-[var(--bg-glass-hover)] transition-colors text-left cursor-pointer"
      >
        <span
          className="text-[0.6rem] transition-transform duration-200"
          style={{
            transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
            color: meta.color,
          }}
        >
          &#9654;
        </span>
        <span
          className="w-2.5 h-2.5 rounded-full shrink-0"
          style={{ backgroundColor: meta.color }}
        />
        <span className="text-sm font-semibold" style={{ color: meta.color }}>
          {meta.label}
        </span>
        <span className="text-xs text-[var(--text-muted)] hidden sm:inline">
          &mdash; {meta.hint}
        </span>
        <span className="ml-auto flex items-center gap-2">
          <span className="text-xs text-[var(--text-muted)]">
            {envCount} env{envCount !== 1 ? 's' : ''}
          </span>
          <span
            className="px-2 py-0.5 text-xs font-bold rounded-full text-black/80"
            style={{ backgroundColor: meta.color }}
          >
            {count}
          </span>
        </span>
      </button>
      {expanded && children}
    </div>
  );
}

function GroupCard({ color, children }: { color: string; children: React.ReactNode }) {
  return (
    <div
      className="rounded-lg border border-[var(--border-glass)] bg-[var(--bg-elevated)]/30 p-3 relative"
      style={{ borderLeftWidth: 3, borderLeftColor: color }}
    >
      {children}
    </div>
  );
}

function EnvBadge({ name }: { name: string }) {
  // 'python' is safe: the comparison backend only compares Python envs
  // (python-lib/adk_backend/routes/code_envs.py filters to envLang == 'PYTHON').
  return (
    <a
      href={dssUrls.codeEnv('python', name)}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 bg-[var(--bg-surface)] px-2.5 py-1 rounded-md font-mono text-xs text-[var(--text-primary)] border border-[var(--border-glass)] shadow-sm hover:text-[var(--neon-cyan)] hover:border-[var(--neon-cyan)]/50 hover:underline"
    >
      {name}
    </a>
  );
}
