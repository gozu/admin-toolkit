import { motion } from 'framer-motion';
import { useDiag } from '../context/DiagContext';
import { useTableFilter } from '../hooks/useTableFilter';
import {
  fmBaseline,
  compareToFM,
  atOrAboveFMDefaults,
  type FMComparison,
} from '../utils/fmMemoryDefaults';
import { computeJekConcurrency } from '../utils/jekConcurrency';
import { ExternalLinkIcon } from './ExternalLinkIcon';

const CONTAINER_DOCS_URL =
  'https://doc.dataiku.com/dss/latest/containers/concepts.html#containerized-execution-configurations';

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
  const totalVm = parseInt(totalVmStr.replace(/[^0-9]/g, '')) || 0;
  const backendGB = parseInt(backendStr.replace(/[^0-9]/g, '')) || 0;
  const jekGB = parseInt(jekStr.replace(/[^0-9]/g, '')) || 0;
  const cgroupLimit = parseInt(cgroupLimitStr.replace(/[^0-9]/g, '')) || 0;

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

  // Memory model: Instance Total - Backend (outside cgroup) - Workloads CGroup = Available for JEK
  const jekTotal = jekGB * effectiveMaxJobs;
  const availableForJEK = totalVm - backendGB - cgroupLimit;
  const jekHeadroom = availableForJEK - jekTotal;

  // Need both instance total and cgroup limit to show the card
  if (totalVm === 0 || cgroupLimit === 0) return null;

  // FM baseline for this instance size.
  const fmBase = fmBaseline(totalVm);
  const fmCmp = compareToFM({ backendGB, cgroupGB: cgroupLimit }, fmBase);
  const isAtOrAboveFM = atOrAboveFMDefaults(fmCmp);

  // Hard invariant: cgroup larger than instance total RAM cannot be enforced.
  const cgroupExceedsInstance = cgroupLimit > totalVm;

  // Status: FM baseline is always "ok" unless a hard invariant breaks.
  // Below FM → at least warning. Above/match FM → ok regardless of JEK headroom.
  let status: 'ok' | 'info' | 'warning' | 'critical';
  if (cgroupExceedsInstance || availableForJEK < 0) {
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

  // FM-baseline scenario text.
  const fmScenarioText = (() => {
    if (cgroupExceedsInstance) {
      return `Cgroup (${cgroupLimitStr}) exceeds physical RAM (${totalVmStr}) — unenforced. Restore FM baseline (cgroup ${fmBase.cgroupGB}g) or grow the instance.`;
    }
    if (isAtOrAboveFM) {
      return 'Memory sizing matches or exceeds FM baseline — healthy.';
    }
    return 'Below FM baseline: backend/cgroup smaller than recommended; expect tight JEK headroom.';
  })();

  // Containerized-execution sub-paragraph (preserved from the earlier advisor).
  const containerText = (() => {
    if (bothContainerized) {
      return 'User code + visual recipes both containerized — local backend stays light.';
    }
    if (userCodeContainer || visualRecipesContainer) {
      return 'One workload type is local — set a default container exec config for the other to fully offload.';
    }
    return 'Protip: default a container exec config for user code + visual recipes to offload from the local JEK.';
  })();

  return (
    <motion.div
      className="chart-container"
      id="memory-analysis"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="chart-header">
        <h4>Memory Analysis</h4>
      </div>

      <div className="chart-summary" style={{ marginTop: '0.5rem' }}>
        <table>
          <tbody>
            <tr><td className="text-[var(--text-secondary)]">Instance total</td><td className="text-right font-mono">{totalVmStr}</td></tr>
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
                <span className="text-xs font-mono" style={{ color: fmAnnotationColor(fmCmp.cgroup) }}>
                  ({fmAnnotationText(fmCmp.cgroup, fmBase.cgroupGB)})
                </span>
              </td>
              <td className="text-right font-mono text-[var(--neon-cyan)]">- {cgroupLimitStr}</td>
            </tr>
            <tr><td colSpan={2} className="py-1"><div className="border-t border-[var(--border-color)] opacity-50" /></td></tr>
            <tr><td className="text-[var(--text-secondary)]">Available for JEK</td><td className="text-right font-mono" style={{ color: availableForJEK <= 0 ? 'var(--neon-red)' : 'var(--text-primary)' }}>{availableForJEK} GB</td></tr>
            {effectiveMaxJobs > 0 && jekGB > 0 && (
              <>
                <tr>
                  <td className="text-[var(--text-secondary)] pl-2">
                    JEK {jekGB}g × {effectiveMaxJobs} jobs
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
                <tr><td className="font-medium pt-1" style={{ color: resultColor }}>Headroom</td><td className="text-right font-mono font-bold pt-1" style={{ color: resultColor }}>{jekHeadroom} GB</td></tr>
              </>
            )}
          </tbody>
        </table>

        {/* Advisor block: always shown. Names the FM baseline, the scenario,
            and the containerized-execution option that offloads local memory. */}
        <div className="mt-3 p-2 rounded text-xs" style={{ border: '1px solid var(--border-color)' }}>
          <div className="font-medium text-[var(--text-primary)] mb-1">Memory sizing vs. FM baseline</div>
          <div className="space-y-0.5 font-mono">
            <div>
              FM baseline for {totalVm} GB instance: backend{' '}
              <span style={{ color: 'var(--text-primary)' }}>{fmBase.backendGB}g</span>, cgroup{' '}
              <span style={{ color: 'var(--text-primary)' }}>{fmBase.cgroupGB}g</span>, leaves{' '}
              <span style={{ color: 'var(--text-primary)' }}>{fmBase.availableForJEK}g</span> for JEK
            </div>
            <div>
              User code recipes:{' '}
              <span style={{ color: userCodeContainer ? 'var(--neon-green)' : 'var(--neon-yellow)' }}>
                {userCodeContainer ? 'containerized execution' : 'local backend for execution'}
              </span>
            </div>
            <div>
              Visual recipes (DSS engine):{' '}
              <span style={{ color: visualRecipesContainer ? 'var(--neon-green)' : 'var(--neon-yellow)' }}>
                {visualRecipesContainer ? 'containerized execution' : 'local backend for execution'}
              </span>
            </div>
          </div>
          <div className="mt-2">
            {fmScenarioText}
          </div>
          <div className="mt-1">
            {containerText}{' '}
            <a href={CONTAINER_DOCS_URL} target="_blank" rel="noreferrer" className="text-[var(--neon-cyan)] hover:underline whitespace-nowrap">
              docs<ExternalLinkIcon />
            </a>
          </div>
        </div>

        {status === 'critical' && cgroupExceedsInstance && (
          <div className="mt-2 p-2 rounded text-xs" style={{ backgroundColor: 'color-mix(in srgb, var(--neon-red) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--neon-red) 30%, transparent)', color: 'var(--neon-red)' }}>
            Workloads cgroup ({cgroupLimitStr}) exceeds DSS instance total ({totalVmStr}) — the cap cannot be enforced by the kernel.
          </div>
        )}

        {status === 'critical' && !cgroupExceedsInstance && availableForJEK < 0 && (
          <div className="mt-2 p-2 rounded text-xs" style={{ backgroundColor: 'color-mix(in srgb, var(--neon-red) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--neon-red) 30%, transparent)', color: 'var(--neon-red)' }}>
            Backend + workloads cgroup exceed the instance total by {Math.abs(availableForJEK)}GB.
          </div>
        )}

        {status === 'critical' && !cgroupExceedsInstance && availableForJEK >= 0 && jekHeadroom < 0 && (
          <div className="mt-2 p-2 rounded text-xs" style={{ backgroundColor: 'color-mix(in srgb, var(--neon-red) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--neon-red) 30%, transparent)', color: 'var(--neon-red)' }}>
            JEK allocation exceeds available memory by {jekOver}GB. OOM kills likely if all {effectiveMaxJobs} jobs run concurrently on this DSS instance.
          </div>
        )}

        {status === 'warning' && !isAtOrAboveFM && jekHeadroom < 0 && someContainerized && !bothContainerized && (
          <div className="mt-2 p-2 rounded text-xs" style={{ backgroundColor: 'color-mix(in srgb, var(--neon-yellow) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--neon-yellow) 30%, transparent)', color: 'var(--neon-yellow)' }}>
            {userCodeContainer
              ? `Visual recipes using the DSS engine run in the local backend by default — JEK allocation is ${jekOver}GB over the instance budget, so OOM is possible when visual-recipe load is high.`
              : `User code recipes run in the local backend by default — JEK allocation is ${jekOver}GB over the instance budget, so OOM is possible when user-code load is high.`}
          </div>
        )}

        {status === 'info' && jekHeadroom < 0 && bothContainerized && !isAtOrAboveFM && (
          <div className="mt-2 p-2 rounded text-xs" style={{ backgroundColor: 'color-mix(in srgb, var(--neon-yellow) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--neon-yellow) 30%, transparent)', color: 'var(--neon-yellow)' }}>
            Worst-case local JEK allocation is {jekOver}GB over the instance budget. Because both workload types default to containerized execution, this only materialises if projects override the default to run locally.
          </div>
        )}

        {status === 'warning' && !isAtOrAboveFM && jekHeadroom >= 0 && (
          <div className="mt-2 p-2 rounded text-xs" style={{ backgroundColor: 'color-mix(in srgb, var(--neon-yellow) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--neon-yellow) 30%, transparent)', color: 'var(--neon-yellow)' }}>
            {fmCmp.backend === 'below' && fmCmp.cgroup === 'below'
              ? `Backend and cgroup are both below FM's baseline for this instance size (FM: ${fmBase.backendGB}g backend, ${fmBase.cgroupGB}g cgroup).`
              : fmCmp.backend === 'below'
                ? `Backend ${backendGB}g is below FM's baseline for this instance size (FM: ${fmBase.backendGB}g).`
                : `Cgroup ${cgroupLimit}g is below FM's baseline for this instance size (FM: ${fmBase.cgroupGB}g).`}
          </div>
        )}
      </div>
    </motion.div>
  );
}
