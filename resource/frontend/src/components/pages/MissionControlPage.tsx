import { useCallback, useMemo } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { useDiag } from '../../context/DiagContext';
import { useHealthScore, useSharedHealthFactors, SCORE_LIFECYCLE_FIELDS } from '../../hooks';
import type { Lifecycle, PageId } from '../../types';
import { resolveLifecycleById, resolveLifecycleFromFields } from '../../utils/pageLifecycle';
import {
  selectCodeEnvs,
  selectConnHealth,
  selectConnTypes,
  selectConnUsage,
  selectMemory,
  selectMounts,
  selectPlugins,
  selectProjects,
  selectSanity,
  selectTreemapItems,
  selectUsers,
} from './missionControl/selectors';
import {
  AdoptionTile,
  CodeEnvsTile,
  ConnHealthTile,
  ConnInventoryTile,
  ConnUsageTile,
  ContainerExecsTile,
  CostTile,
  CpuTile,
  EnvCompareTile,
  FilesystemTile,
  HealthTile,
  K8sTile,
  LlmAuditTile,
  LogsTile,
  MemoryTile,
  PluginsTile,
  ProjectComputeTile,
  ProjectsTile,
  SanityTile,
  UsersTile,
} from './missionControl/tiles';
import './missionControl/viz.css';

// Mission Control — the entire Admin Toolkit on one zero-scroll wall.
// 12 cols × 6 rows of named areas; every tile reads data the startup loaders
// already fetched (adoption's single cached GET is the one wall-initiated
// kick). Rows 3-4 give the wall its only time axis: the adoption spine.
// Two panes were demoted to chips rather than tiles: worst connection-audit
// severity rides on Conn Health, and runtime-DB health rides on Sanity —
// both deep-link to their pages.
const GRID_AREAS = [
  '"health health health fs fs fs mem mem coninv coninv conhlt conhlt"',
  '"health health health fs fs fs cpu cpu coninv coninv conuse conuse"',
  '"proj proj proj users users users adopt adopt adopt adopt plug plug"',
  '"proj proj proj users users users adopt adopt adopt adopt cenv cenv"',
  '"k8s k8s k8s cex cex cex llm llm llm pcomp pcomp pcomp"',
  '"logs logs logs sanity sanity sanity cost cost cost envcmp envcmp envcmp"',
].join(' ');

// Stable fallback so memoized tiles don't see a fresh lifecycle object each render.
const QUEUED: Lifecycle = { phase: 'queued' };

export function MissionControlPage() {
  const { state, setActivePage, setFocusedConnectionFilter, setFocusedUserFilter } = useDiag();
  const { parsedData } = state;
  const reduced = useReducedMotion();

  const {
    filesystemInfo,
    dirTree,
    memoryInfo,
    cpuCores,
    connections,
    connectionHealth,
    connectionDatasetUsages,
    connectionLlmUsages,
    connectionAudit,
    connectionUsageScanned,
    connectionUsageTotal,
    clusters,
    projects,
    projectFootprint,
    projectFootprintSummary,
    users,
    pluginDetails,
    pluginsCount,
    pluginUsagesPending,
    codeEnvs,
    codeEnvSizes,
    pythonVersionCounts,
    skippedEnvCount,
    logStats,
    rawLogErrors,
    sanityCheck,
    sanityCheckMaxSeverity,
    llmAudit,
    dssVersion,
    lastRestartTime,
  } = parsedData;

  const { healthFactorToggles } = useSharedHealthFactors();
  const healthScore = useHealthScore(parsedData, healthFactorToggles);

  // Reveal the score as soon as ITS OWN inputs settle — gate on the score's
  // lifecycle fields (like SummaryPage), NOT the global `analysisLoading`
  // aggregate. That aggregate waits on all ~26 modules incl. Cost/CRU, which the
  // score never reads and which auto-starts last, so gating on it would skeleton
  // the ring until an unrelated scan finished. Memoized so the tile sees a stable
  // lifecycle object once parsedData settles.
  const scoreLc = useMemo(
    () => resolveLifecycleFromFields(SCORE_LIFECYCLE_FIELDS, parsedData),
    [parsedData],
  );
  // TileShell renders a red error state (not children) on phase 'error', so a
  // single failed score input would replace the ring with an error message.
  // Normalize a failed input to a terminal `done` so the score still shows.
  const tileLc = useMemo<Lifecycle>(
    () =>
      scoreLc.phase === 'error'
        ? { phase: 'done', startedAt: scoreLc.startedAt, finishedAt: scoreLc.finishedAt, isEmpty: false }
        : scoreLc,
    [scoreLc],
  );

  // Live mode loads the directory tree into apiDirTree; dirTree is zip-mode.
  const liveDirTree = state.apiDirTree?.tree;
  const mounts = useMemo(() => selectMounts(filesystemInfo), [filesystemInfo]);
  const treemap = useMemo(
    () => selectTreemapItems(liveDirTree ?? dirTree),
    [liveDirTree, dirTree],
  );
  const mem = useMemo(() => selectMemory(memoryInfo), [memoryInfo]);
  const connTypesVm = useMemo(() => selectConnTypes(connections), [connections]);
  const connHealthVm = useMemo(
    () => selectConnHealth(connectionHealth, connectionAudit),
    [connectionHealth, connectionAudit],
  );
  const connUsageVm = useMemo(
    () =>
      selectConnUsage({
        connectionDatasetUsages,
        connectionLlmUsages,
        connectionUsageScanned,
        connectionUsageTotal,
      }),
    [connectionDatasetUsages, connectionLlmUsages, connectionUsageScanned, connectionUsageTotal],
  );
  const projectsVm = useMemo(
    () => selectProjects({ projects, projectFootprint, projectFootprintSummary }),
    [projects, projectFootprint, projectFootprintSummary],
  );
  const usersVm = useMemo(
    () => selectUsers({ users, projects, projectFootprint }),
    [users, projects, projectFootprint],
  );
  const pluginsVm = useMemo(
    () => selectPlugins({ pluginDetails, pluginsCount }),
    [pluginDetails, pluginsCount],
  );
  const codeEnvsVm = useMemo(
    () => selectCodeEnvs({ codeEnvs, codeEnvSizes, pythonVersionCounts }),
    [codeEnvs, codeEnvSizes, pythonVersionCounts],
  );
  const sanityVm = useMemo(
    () => selectSanity(sanityCheck, sanityCheckMaxSeverity),
    [sanityCheck, sanityCheckMaxSeverity],
  );

  const handleTypeClick = useCallback(
    (type: string) => {
      setFocusedConnectionFilter({ type });
      setActivePage('connections-insights');
    },
    [setFocusedConnectionFilter, setActivePage],
  );
  const handleOwnerClick = useCallback(
    (login: string) => {
      setFocusedUserFilter({ login });
      setActivePage('users');
    },
    [setFocusedUserFilter, setActivePage],
  );

  const lc = (id: PageId): Lifecycle => resolveLifecycleById(id, parsedData);

  return (
    <div className="relative flex min-h-0 flex-1 flex-col p-3">
      {/* NOC backdrop: faint scanlines + vignette, pure CSS, hit-transparent. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            'repeating-linear-gradient(0deg, rgba(128,128,128,0.03) 0px, rgba(128,128,128,0.03) 1px, transparent 1px, transparent 3px)',
        }}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            'radial-gradient(ellipse 90% 80% at 50% 40%, transparent 60%, rgba(0,0,0,0.14) 100%)',
        }}
      />
      <motion.div
        className="grid min-h-[840px] flex-1 gap-2"
        style={{
          gridTemplateColumns: 'repeat(12, minmax(0, 1fr))',
          gridTemplateRows: 'repeat(6, minmax(0, 1fr))',
          gridTemplateAreas: GRID_AREAS,
        }}
        variants={{ hidden: {}, show: { transition: { staggerChildren: 0.035 } } }}
        initial={reduced ? false : 'hidden'}
        animate="show"
      >
        <HealthTile
          lifecycle={tileLc}
          onNavigate={setActivePage}
          health={healthScore}
          dssVersion={dssVersion}
          lastRestartTime={lastRestartTime}
        />
        <FilesystemTile lifecycle={lc('filesystem')} onNavigate={setActivePage} mounts={mounts} treemap={treemap} />
        <MemoryTile lifecycle={lc('memory')} onNavigate={setActivePage} mem={mem} />
        <CpuTile lifecycle={lc('cpu')} onNavigate={setActivePage} cores={cpuCores} />
        <ConnInventoryTile lifecycle={lc('connections-inventory')} onNavigate={setActivePage} vm={connTypesVm} onTypeClick={handleTypeClick} />
        <ConnHealthTile lifecycle={lc('connections-health')} onNavigate={setActivePage} vm={connHealthVm} />
        {/* The standalone Usage page merged into Insights; the tile keeps its
            dedicated scan lifecycle rather than Insights' composite one. */}
        <ConnUsageTile lifecycle={parsedData.connectionUsageLoading ?? QUEUED} onNavigate={setActivePage} vm={connUsageVm} />
        <ProjectsTile lifecycle={lc('projects')} onNavigate={setActivePage} vm={projectsVm} />
        <UsersTile lifecycle={lc('users')} onNavigate={setActivePage} vm={usersVm} onOwnerClick={handleOwnerClick} />
        <AdoptionTile lifecycle={lc('adoption')} onNavigate={setActivePage} />
        <PluginsTile
          lifecycle={lc('plugins-installed')}
          onNavigate={setActivePage}
          vm={pluginsVm}
          pending={Boolean(pluginUsagesPending)}
        />
        <CodeEnvsTile lifecycle={lc('code-envs-cleaner')} onNavigate={setActivePage} vm={codeEnvsVm} />
        <K8sTile lifecycle={lc('k8s-insights')} onNavigate={setActivePage} clusterCount={clusters?.length ?? 0} />
        <ContainerExecsTile lifecycle={lc('container-execs')} onNavigate={setActivePage} />
        <LlmAuditTile lifecycle={lc('llm-audit')} onNavigate={setActivePage} summary={llmAudit?.summary} />
        <ProjectComputeTile lifecycle={lc('project-compute')} onNavigate={setActivePage} />
        <LogsTile
          lifecycle={lc('logs')}
          onNavigate={setActivePage}
          unique={Number(logStats?.['Unique Errors'] ?? 0)}
          totalLines={Number(logStats?.['Total Lines'] ?? 0)}
          snippet={rawLogErrors?.[0]?.data?.[0]}
        />
        <SanityTile lifecycle={lc('sanity-check')} onNavigate={setActivePage} vm={sanityVm} />
        <CostTile lifecycle={lc('project-cost')} onNavigate={setActivePage} />
        <EnvCompareTile
          lifecycle={lc('code-envs-comparison')}
          onNavigate={setActivePage}
          skippedEnvCount={skippedEnvCount}
          codeEnvSizes={codeEnvSizes}
        />
      </motion.div>
    </div>
  );
}
