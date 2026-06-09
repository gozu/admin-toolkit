import { useState, useMemo } from 'react';
import { useDiag } from '../context/DiagContext';
import type { CodeEnv } from '../types';
import { useTableFilter } from '../hooks/useTableFilter';
import { formatSizeGb, getRelativeSizeColor } from '../utils/formatters';
import { ProgressIndicator } from './common/ProgressIndicator';
import { dssUrls } from '../utils/codeEnvUsageLinks';

const EMPTY_ARR: never[] = [];

type ViewMode = 'summary' | 'details';

export function CodeEnvsTable() {
  const { state } = useDiag();
  const { isVisible } = useTableFilter();
  const { parsedData } = state;
  const rawCodeEnvs = parsedData.codeEnvs ?? EMPTY_ARR;
  const codeEnvSizes = parsedData.codeEnvSizes;
  const codeEnvs = useMemo(() => {
    if (!codeEnvSizes || !rawCodeEnvs.length) return rawCodeEnvs;
    return rawCodeEnvs.map((env) => {
      const sizeKey = `${(env.language || 'python').toLowerCase()}:${env.name}`;
      const size = codeEnvSizes[sizeKey];
      return size ? { ...env, sizeBytes: size } : env;
    });
  }, [rawCodeEnvs, codeEnvSizes]);
  const provisionalCodeEnvs = parsedData.provisionalCodeEnvs ?? EMPTY_ARR;
  const loading = parsedData.codeEnvsLoading;
  const isLoading = loading?.phase === 'running' || loading?.phase === 'queued';
  const pythonVersionCounts = parsedData.pythonVersionCounts || {};
  const rVersionCounts = parsedData.rVersionCounts || {};
  const totalEnvCount = parsedData.totalEnvCount;
  const skippedEnvCount = parsedData.skippedEnvCount;

  const [viewMode, setViewMode] = useState<ViewMode>('details');
  const [ownerFilter, setOwnerFilter] = useState<string | null>(null);

  const allEnvs = useMemo(() => {
    const realNames = new Set(codeEnvs.map((e) => e.name));
    const provisionalAsEnvs = provisionalCodeEnvs
      .filter((e) => !realNames.has(e.name))
      .map(
        (e) =>
          ({
            name: e.name,
            version: '',
            language: 'python' as CodeEnv['language'],
            usageCount: e.usageCount,
          }) as CodeEnv,
      );
    return [...codeEnvs, ...provisionalAsEnvs];
  }, [codeEnvs, provisionalCodeEnvs]);

  const { pythonEnvs, rEnvs } = useMemo(() => {
    const python = allEnvs.filter((env) => env.language === 'python');
    const r = allEnvs.filter((env) => env.language === 'r');
    return { pythonEnvs: python, rEnvs: r };
  }, [allEnvs]);

  const sortedCodeEnvs = useMemo(
    () =>
      [...allEnvs].sort((a, b) => {
        const sizeA = a.sizeBytes || 0;
        const sizeB = b.sizeBytes || 0;
        if (sizeB !== sizeA) return sizeB - sizeA;
        return a.name.localeCompare(b.name);
      }),
    [allEnvs],
  );
  const filteredCodeEnvs = useMemo(
    () =>
      ownerFilter
        ? sortedCodeEnvs.filter((env) => (env.owner || 'Unknown') === ownerFilter)
        : sortedCodeEnvs,
    [sortedCodeEnvs, ownerFilter],
  );

  const maxCodeEnvBytes = useMemo(
    () => sortedCodeEnvs.reduce((max, env) => Math.max(max, env.sizeBytes || 0), 0),
    [sortedCodeEnvs],
  );

  if (!isVisible('code-envs-table') || (codeEnvs.length === 0 && !isLoading)) {
    return null;
  }

  const sortedPythonVersions = Object.entries(pythonVersionCounts).sort((a, b) => b[1] - a[1]);
  const sortedRVersions = Object.entries(rVersionCounts).sort((a, b) => b[1] - a[1]);

  const pythonCount = pythonEnvs.length;
  const rCount = rEnvs.length;

  return (
    <div className="rounded-xl overflow-hidden md:col-span-2" id="code-envs-table">
      <div className="px-4 py-3 border-b border-[var(--border-glass)]">
        <div className="flex items-center justify-between">
          <h4 className="text-lg font-semibold text-[var(--text-primary)]">
            {codeEnvs.length > 0
              ? ownerFilter
                ? `${filteredCodeEnvs.length} of ${codeEnvs.length} Code Envs`
                : `${codeEnvs.length} Code Envs`
              : 'Code Envs'}
            {skippedEnvCount != null && skippedEnvCount > 0 && totalEnvCount != null && (
              <span className="ml-2 text-sm font-normal text-[var(--text-muted)]">
                ({codeEnvs.length} of {totalEnvCount} — {skippedEnvCount} plugin-managed excluded)
              </span>
            )}
          </h4>
          <div className="flex items-center gap-2">
            {pythonCount > 0 && (
              <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-[var(--neon-cyan)]/10 text-[var(--neon-cyan)] border border-[var(--neon-cyan)]/30">
                {pythonCount} Python
              </span>
            )}
            {rCount > 0 && (
              <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-[var(--neon-purple)]/10 text-[var(--neon-purple)] border border-[var(--neon-purple)]/30">
                {rCount} R
              </span>
            )}
          </div>
        </div>
      </div>

      {ownerFilter && (
        <div className="px-4 py-2 border-b border-[var(--border-glass)] bg-[var(--bg-elevated)] flex items-center gap-2 text-sm">
          <span className="text-[var(--text-secondary)]">Filtered by owner:</span>
          <span className="px-2 py-0.5 rounded-full bg-[var(--neon-cyan)]/10 text-[var(--neon-cyan)] border border-[var(--neon-cyan)]/30 text-xs font-medium">
            {ownerFilter}
          </span>
          <button
            onClick={() => setOwnerFilter(null)}
            className="ml-1 px-2 py-0.5 text-xs rounded text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-glass-hover)] transition-colors"
          >
            Clear
          </button>
        </div>
      )}

      {isLoading && codeEnvs.length === 0 && (
        <div className="px-4 py-3">
          <ProgressIndicator lifecycle={loading} />
        </div>
      )}

      {!(isLoading && codeEnvs.length === 0) && (
        <>
          <div>
            {codeEnvs.length === 0 ? (
              <div className="p-4 text-sm text-[var(--text-secondary)]">
                Waiting for code environment data...
              </div>
            ) : viewMode === 'summary' ? (
              <div className="divide-y divide-[var(--border-glass)]">
                {/* Python Versions Summary */}
                {sortedPythonVersions.length > 0 && (
                  <div className="p-4">
                    <div className="flex items-center gap-2 mb-3">
                      <LanguageBadge language="python" />
                      <span className="text-sm font-medium text-[var(--text-secondary)]">
                        Python Environments
                      </span>
                    </div>
                    <table className="w-full">
                      <thead className="bg-[var(--bg-app)]">
                        <tr>
                          <th className="px-4 py-2 text-left text-sm font-semibold text-[var(--text-secondary)]">
                            Version
                          </th>
                          <th className="px-4 py-2 text-left text-sm font-semibold text-[var(--text-secondary)]">
                            Count
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-[var(--border-glass)]">
                        {sortedPythonVersions.map(([version, count], idx) => (
                          <tr key={idx} className="hover:bg-[var(--bg-glass-hover)]">
                            <td className="px-4 py-2">
                              <PythonVersionBadge version={version} />
                            </td>
                            <td className="px-4 py-2 text-[var(--text-primary)]">{count}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {/* R Versions Summary */}
                {sortedRVersions.length > 0 && (
                  <div className="p-4">
                    <div className="flex items-center gap-2 mb-3">
                      <LanguageBadge language="r" />
                      <span className="text-sm font-medium text-[var(--text-secondary)]">
                        R Environments
                      </span>
                    </div>
                    <table className="w-full">
                      <thead className="bg-[var(--bg-app)]">
                        <tr>
                          <th className="px-4 py-2 text-left text-sm font-semibold text-[var(--text-secondary)]">
                            Type
                          </th>
                          <th className="px-4 py-2 text-left text-sm font-semibold text-[var(--text-secondary)]">
                            Count
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-[var(--border-glass)]">
                        {sortedRVersions.map(([version, count], idx) => (
                          <tr key={idx} className="hover:bg-[var(--bg-glass-hover)]">
                            <td className="px-4 py-2">
                              <span className="text-[var(--neon-purple)] font-medium">
                                {version}
                              </span>
                            </td>
                            <td className="px-4 py-2 text-[var(--text-primary)]">{count}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            ) : (
              <table className="w-full">
                <thead className="bg-[var(--bg-app)] sticky top-0">
                  <tr>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[var(--text-secondary)]">
                      Name
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[var(--text-secondary)]">
                      Owner
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[var(--text-secondary)]">
                      Version
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[var(--text-secondary)]">
                      Language
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[var(--text-secondary)]">
                      Size (GB)
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--border-glass)]">
                  {filteredCodeEnvs.map((env, idx) => (
                    <tr key={idx} className="hover:bg-[var(--bg-glass-hover)]">
                      <td className="px-4 py-3">
                        <a
                          href={dssUrls.codeEnv(env.language, env.name)}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-[var(--neon-cyan)] hover:underline"
                        >
                          {env.name}
                        </a>
                      </td>
                      <td className="px-4 py-3">
                        <button
                          type="button"
                          onClick={() => setOwnerFilter(env.owner || 'Unknown')}
                          className="text-[var(--neon-cyan)] hover:underline cursor-pointer bg-transparent border-none p-0 font-inherit text-inherit"
                        >
                          {env.owner || 'Unknown'}
                        </button>
                      </td>
                      <td className="px-4 py-3">
                        {env.language === 'python' ? (
                          <PythonVersionBadge version={env.version} />
                        ) : (
                          <span className="text-[var(--neon-purple)] font-medium">
                            {env.version}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <LanguageBadge language={env.language} />
                      </td>
                      <td
                        className={`px-4 py-3 font-mono ${getRelativeSizeColor(env.sizeBytes || 0, maxCodeEnvBytes)}`}
                      >
                        {formatSizeGb(env.sizeBytes)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {codeEnvs.length > 0 && (
            <div className="px-4 py-3 border-t border-[var(--border-glass)]">
              <button
                onClick={() => setViewMode(viewMode === 'summary' ? 'details' : 'summary')}
                className="px-4 py-2 text-sm font-medium text-[var(--neon-cyan)] hover:bg-[var(--neon-cyan)]/10 rounded-lg transition-colors"
              >
                {viewMode === 'details' ? 'Show Summary' : 'View All Environments'}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function LanguageBadge({ language }: { language: 'python' | 'r' }) {
  if (language === 'python') {
    return (
      <span className="px-2 py-0.5 text-xs font-semibold rounded bg-[var(--neon-cyan)]/20 text-[var(--neon-cyan)] border border-[var(--neon-cyan)]/30">
        Python
      </span>
    );
  }
  return (
    <span className="px-2 py-0.5 text-xs font-semibold rounded bg-[var(--neon-purple)]/20 text-[var(--neon-purple)] border border-[var(--neon-purple)]/30">
      R
    </span>
  );
}

function PythonVersionBadge({ version }: { version: string }) {
  const versionMatch = version.match(/(\d+)\.(\d+)/);
  let colorClass = 'text-[var(--text-secondary)]';

  if (versionMatch) {
    const major = parseInt(versionMatch[1], 10);
    const minor = parseInt(versionMatch[2], 10);

    if (major < 3) {
      // Python 2.x - red (EOL)
      colorClass = 'text-[var(--neon-red)] font-bold';
    } else if (major === 3 && minor >= 9) {
      // Python 3.9+ - green (current)
      colorClass = 'text-[var(--neon-green)]';
    } else {
      // Python 3.6-3.8 - amber (outdated but supported)
      colorClass = 'text-[var(--neon-amber)]';
    }
  }

  return <span className={colorClass}>{version}</span>;
}
