import { useDiag } from '../context/DiagContext';
import { useTableFilter } from '../hooks/useTableFilter';
import { ChartContainer } from './ChartContainer';
import {
  fmBaseline,
  compareToFM,
  atOrAboveFMDefaults,
  type FMComparison,
} from '../utils/fmMemoryDefaults';
import { computeJekConcurrency } from '../utils/jekConcurrency';
import { parseSizeToGB } from '../utils/formatters';

function fmAnnotationColor(cmp: FMComparison): string {
  if (cmp === 'match') return 'var(--neon-green)';
  if (cmp === 'above') return 'var(--neon-yellow)';
  return 'var(--neon-red)';
}

function fmAnnotationText(cmp: FMComparison, expectedGB: number): string {
  if (cmp === 'match') return `FM default: ${expectedGB}g ✓`;
  if (cmp === 'above') return `FM default: ${expectedGB}g, manually raised`;
  return `FM default: ${expectedGB}g, below baseline`;
}

export function MemoryAnalysisCard() {
  const { state } = useDiag();
  const { isVisible } = useTableFilter();
  const { parsedData } = state;

  if (!isVisible('memory-analysis')) return null;

  // Read raw values from parsedData
  const totalVmStr = parsedData.memoryInfo?.total || '';
  const backendStr = parsedData.javaMemorySettings?.BACKEND || '0g';
  const jekStr = parsedData.javaMemorySettings?.JEK || '0g';
  const cgroupLimitStr = String(parsedData.cgroupSettings?.['Memory Limit'] || '0');

  // Parse numeric values (all to GB)
  const totalVm = Math.round(parseSizeToGB(totalVmStr)) || 0;
  const backendGB = parseInt(backendStr.replace(/[^0-9]/g, '')) || 0;
  const jekGB = parseInt(jekStr.replace(/[^0-9]/g, '')) || 0;
  const cgroupLimit = parseInt(cgroupLimitStr.replace(/[^0-9]/g, '')) || 0;
  const hasCgroupLimit = cgroupLimit > 0;

  // Instance-default containerization signals (project-standards.json +
  // general-settings.json). If no execution configs exist, these collapse to NONE.
  const execDefaults = parsedData.containerExecDefaults;
  const execConfigsCount = execDefaults?.executionConfigsCount ?? 0;
  const execConfigsPresent = execConfigsCount > 0;
  const userCodeContainer = execConfigsPresent && execDefaults!.userCodeMode === 'CONTAINER';
  const visualRecipesContainer = execConfigsPresent && execDefaults!.visualRecipesMode === 'CONTAINER';
  const bothContainerized = userCodeContainer && visualRecipesContainer;
  const someContainerized = userCodeContainer || visualRecipesContainer;

  // JEK concurrency: one process per *job* (shared across activities in that
  // job), discounted by the instance-default containerization fraction.
  const jekConcurrency = computeJekConcurrency({
    maxRunningActivities: parsedData.maxRunningActivities,
    containerExecDefaults: parsedData.containerExecDefaults,
  });
  const { baseMaxJobs, effectiveMaxJobs, derivedFrom } = jekConcurrency;

  // Concurrency-config warnings surfaced in this card (also flagged in the
  // Issues panel via platformReviewChecks). Max Running Jobs is the memory-
  // critical cap on concurrent JEK processes; Max Running Activities is a
  // higher global backstop that should stay bounded (recommended 30–50).
  const maxRunningJobsVal = parsedData.maxRunningActivities?.['Max Running Jobs'];
  const maxRunningActivitiesVal = parsedData.maxRunningActivities?.['Max Running Activities'];
  const maxRunningJobsUncapped = maxRunningJobsVal === 0;
  const maxRunningActivitiesHigh =
    typeof maxRunningActivitiesVal === 'number' && maxRunningActivitiesVal > 50;

  // Memory model: Instance Total - Backend (outside cgroup) - Workloads CGroup = Available for JEK
  const jekTotal = jekGB * effectiveMaxJobs;
  const availableForJEK = totalVm - backendGB - (hasCgroupLimit ? cgroupLimit : 0);
  const jekHeadroom = availableForJEK - jekTotal;

  // Need instance total to anchor the analysis. Cgroup memory limit may be
  // absent from older/incomplete configurations; render a partial analysis then.
  if (totalVm === 0) return null;

  // FM baseline for this instance size.
  const fmBase = fmBaseline(totalVm);
  const fmCmp = compareToFM({ backendGB, cgroupGB: hasCgroupLimit ? cgroupLimit : fmBase.cgroupGB }, fmBase);
  const isAtOrAboveFM = hasCgroupLimit && atOrAboveFMDefaults(fmCmp);

  // Hard invariant: cgroup larger than instance total RAM cannot be enforced.
  const cgroupExceedsInstance = hasCgroupLimit && cgroupLimit > totalVm;

  // Status blends hard configuration invariants and then the FM-baseline
  // comparison as the configuration-only fallback.
  let status: 'ok' | 'info' | 'warning' | 'critical';
  if (!hasCgroupLimit) {
    status = 'warning';
  } else if (cgroupExceedsInstance || availableForJEK < 0) {
    status = 'critical';
  } else if (isAtOrAboveFM) {
    status = 'ok';
  } else if (jekHeadroom < 0) {
    // Below FM and JEK over budget — soften only when both workload types run off-box.
    status = bothContainerized ? 'info' : someContainerized ? 'warning' : 'critical';
  } else {
    // Below FM but headroom OK: flag the under-baseline config.
    status = 'warning';
  }

  const statusColors = { ok: 'var(--neon-green)', warning: 'var(--neon-yellow)', info: 'var(--neon-yellow)', critical: 'var(--neon-red)' };
  const resultColor = statusColors[status];

  const jekOver = Math.abs(jekHeadroom);

  // Config-derived verdict for the result row.
  const analysisResult = (() => {
    if (!hasCgroupLimit) return 'Memory analysis is incomplete';
    if (cgroupExceedsInstance || availableForJEK < 0) return 'Memory configuration is invalid';
    return 'Based on configuration';
  })();

  const containerRecommendation = (() => {
    if (bothContainerized) return null;
    if (someContainerized) {
      const localSide = userCodeContainer ? 'DSS-engine visual recipes' : 'user code recipes';
      return `${localSide} run locally by default. Set a container default for them to reduce DSS host memory pressure.`;
    }
    const configsText = execConfigsPresent
      ? `${execConfigsCount} container execution config${execConfigsCount === 1 ? '' : 's'} exist, but`
      : 'No default container execution is configured, so';
    return `${configsText} user code and DSS-engine visual recipes run locally by default. Set container defaults to reduce DSS host memory pressure.`;
  })();

  return (
    <ChartContainer id="memory-analysis" title="Memory Analysis">
      <div className="chart-summary" style={{ marginTop: '0.5rem' }}>
        <table>
          <tbody>
            <tr><td className="text-[var(--text-secondary)]">Instance total</td><td className="text-right font-mono">{totalVm} GB</td></tr>
            <tr><td colSpan={2} className="py-1"><div className="border-t border-[var(--border-color)] opacity-50" /></td></tr>
            <tr>
              <td className="text-[var(--text-secondary)] pl-2">
                Backend (Xmx){' '}
                <span className="text-xs font-mono" style={{ color: fmAnnotationColor(fmCmp.backend) }}>
                  ({fmAnnotationText(fmCmp.backend, fmBase.backendGB)})
                </span>
              </td>
              <td className="text-right font-mono text-[var(--neon-cyan)]">- {backendGB} GB</td>
            </tr>
            <tr>
              <td className="text-[var(--text-secondary)] pl-2">
                Workloads CGroup{' '}
                {hasCgroupLimit ? (
                  <span className="text-xs font-mono" style={{ color: fmAnnotationColor(fmCmp.cgroup) }}>
                    ({fmAnnotationText(fmCmp.cgroup, fmBase.cgroupGB)})
                  </span>
                ) : (
                  <span className="text-xs font-mono text-[var(--neon-yellow)]">
                    (memory limit not found)
                  </span>
                )}
              </td>
              <td className="text-right font-mono text-[var(--neon-cyan)]">
                {hasCgroupLimit ? `- ${cgroupLimitStr}` : 'Not found'}
              </td>
            </tr>
            <tr><td colSpan={2} className="py-1"><div className="border-t border-[var(--border-color)] opacity-50" /></td></tr>
            <tr>
              <td className="text-[var(--text-secondary)]">
                {hasCgroupLimit ? 'Available for local jobs (JEK)' : 'After backend (upper bound)'}
              </td>
              <td className="text-right font-mono" style={{ color: availableForJEK <= 0 ? 'var(--neon-red)' : 'var(--text-primary)' }}>{availableForJEK} GB</td>
            </tr>
            {!hasCgroupLimit && effectiveMaxJobs > 0 && jekGB > 0 && (
              <tr>
                <td className="text-[var(--text-secondary)] pl-2">
                  Configured local job estimate
                  <span className="text-xs text-[var(--text-muted)] ml-1">
                    ({jekGB}g × {effectiveMaxJobs} jobs)
                  </span>
                </td>
                <td className="text-right font-mono text-[var(--text-muted)]">{jekTotal} GB; headroom unavailable</td>
              </tr>
            )}
            {hasCgroupLimit && effectiveMaxJobs > 0 && jekGB > 0 && (
              <>
                <tr>
                  <td className="text-[var(--text-secondary)] pl-2">
                    Configured local job budget (JEK) {jekGB}g × {effectiveMaxJobs} jobs
                    {effectiveMaxJobs < baseMaxJobs && (
                      <span className="text-xs text-[var(--text-muted)] ml-1">
                        (of {baseMaxJobs} max; ~95% offloaded)
                      </span>
                    )}
                    {derivedFrom === 'activities' && (
                      <span className="text-xs text-[var(--text-muted)] ml-1">
                        (worst case: 1 activity/job)
                      </span>
                    )}
                  </td>
                  <td className="text-right font-mono text-[var(--neon-cyan)]">- {jekTotal} GB</td>
                </tr>
                <tr><td colSpan={2} className="py-1"><div className="border-t border-[var(--border-color)] opacity-50" /></td></tr>
                <tr><td className="font-medium pt-1" style={{ color: resultColor }}>Configured headroom</td><td className="text-right font-mono font-bold pt-1" style={{ color: resultColor }}>{jekHeadroom} GB</td></tr>
              </>
            )}
            <tr>
              <td colSpan={2} className="py-1"><div className="border-t border-[var(--border-color)] opacity-50" /></td>
            </tr>
            <tr>
              <td className="font-medium pt-1" style={{ color: resultColor }}>Analysis result</td>
              <td className="text-right font-mono font-bold pt-1" style={{ color: resultColor }}>{analysisResult}</td>
            </tr>
            {containerRecommendation && (
              <>
                <tr>
                  <td colSpan={2} className="py-1"><div className="border-t border-[var(--border-color)] opacity-50" /></td>
                </tr>
                <tr>
                  <td className="text-[var(--text-secondary)] pl-2">Execution defaults</td>
                  <td className="text-right text-[var(--neon-yellow)]">{containerRecommendation}</td>
                </tr>
              </>
            )}
          </tbody>
        </table>

        {!hasCgroupLimit && (
          <div className="mt-2 p-2 rounded text-xs" style={{ backgroundColor: 'color-mix(in srgb, var(--neon-yellow) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--neon-yellow) 30%, transparent)', color: 'var(--neon-yellow)' }}>
            Memory analysis is partial because the workloads cgroup memory limit was not found in this configuration.
          </div>
        )}

        {hasCgroupLimit && status === 'critical' && cgroupExceedsInstance && (
          <div className="mt-2 p-2 rounded text-xs" style={{ backgroundColor: 'color-mix(in srgb, var(--neon-red) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--neon-red) 30%, transparent)', color: 'var(--neon-red)' }}>
            Workloads cgroup ({cgroupLimitStr}) exceeds DSS instance total ({totalVm} GB) — the cap cannot be enforced by the kernel.
          </div>
        )}

        {hasCgroupLimit && status === 'critical' && !cgroupExceedsInstance && availableForJEK < 0 && (
          <div className="mt-2 p-2 rounded text-xs" style={{ backgroundColor: 'color-mix(in srgb, var(--neon-red) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--neon-red) 30%, transparent)', color: 'var(--neon-red)' }}>
            Backend + workloads cgroup exceed the instance total by {Math.abs(availableForJEK)}GB.
          </div>
        )}

        {hasCgroupLimit && status === 'critical' && !cgroupExceedsInstance && availableForJEK >= 0 && jekHeadroom < 0 && (
          <div className="mt-2 p-2 rounded text-xs" style={{ backgroundColor: 'color-mix(in srgb, var(--neon-red) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--neon-red) 30%, transparent)', color: 'var(--neon-red)' }}>
            JEK allocation exceeds available memory by {jekOver}GB. OOM kills likely if all {effectiveMaxJobs} jobs run concurrently on this DSS instance.
          </div>
        )}

        {hasCgroupLimit && status === 'warning' && !isAtOrAboveFM && jekHeadroom < 0 && someContainerized && !bothContainerized && (
          <div className="mt-2 p-2 rounded text-xs" style={{ backgroundColor: 'color-mix(in srgb, var(--neon-yellow) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--neon-yellow) 30%, transparent)', color: 'var(--neon-yellow)' }}>
            {userCodeContainer
              ? `Visual recipes using the DSS engine run in the local backend by default — JEK allocation is ${jekOver}GB over the instance budget, so OOM is possible when visual-recipe load is high.`
              : `User code recipes run in the local backend by default — JEK allocation is ${jekOver}GB over the instance budget, so OOM is possible when user-code load is high.`}
          </div>
        )}

        {hasCgroupLimit && status === 'info' && jekHeadroom < 0 && bothContainerized && !isAtOrAboveFM && (
          <div className="mt-2 p-2 rounded text-xs" style={{ backgroundColor: 'color-mix(in srgb, var(--neon-yellow) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--neon-yellow) 30%, transparent)', color: 'var(--neon-yellow)' }}>
            Worst-case local JEK allocation is {jekOver}GB over the instance budget. Because both workload types default to containerized execution, this only materializes if projects override the default to run locally.
          </div>
        )}

        {hasCgroupLimit && status === 'warning' && !isAtOrAboveFM && jekHeadroom >= 0 && (
          <div className="mt-2 p-2 rounded text-xs" style={{ backgroundColor: 'color-mix(in srgb, var(--neon-yellow) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--neon-yellow) 30%, transparent)', color: 'var(--neon-yellow)' }}>
            {fmCmp.backend === 'below' && fmCmp.cgroup === 'below'
              ? `Backend and cgroup are both below FM's baseline for this instance size (FM: ${fmBase.backendGB}g backend, ${fmBase.cgroupGB}g cgroup).`
              : fmCmp.backend === 'below'
                ? `Backend ${backendGB}g is below FM's baseline for this instance size (FM: ${fmBase.backendGB}g).`
                : `Cgroup ${cgroupLimit}g is below FM's baseline for this instance size (FM: ${fmBase.cgroupGB}g).`}
          </div>
        )}

        {maxRunningJobsUncapped && (
          <div className="mt-2 p-2 rounded text-xs" style={{ backgroundColor: 'color-mix(in srgb, var(--neon-yellow) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--neon-yellow) 30%, transparent)', color: 'var(--neon-yellow)' }}>
            No cap on concurrent jobs (Max Running Jobs = 0): nothing directly bounds the number of local JEK processes. Set Max Running Jobs — derived from the host memory left for JEK execution — to bound local memory use.
          </div>
        )}

        {maxRunningActivitiesHigh && (
          <div className="mt-2 p-2 rounded text-xs" style={{ backgroundColor: 'color-mix(in srgb, var(--neon-yellow) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--neon-yellow) 30%, transparent)', color: 'var(--neon-yellow)' }}>
            Max Running Activities is {maxRunningActivitiesVal}, well above the recommended 30–50 range.
          </div>
        )}
      </div>
    </ChartContainer>
  );
}
