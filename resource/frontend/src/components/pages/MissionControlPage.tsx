import { useCallback, useMemo } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { useDiag } from '../../context/DiagContext';
import { useHealthScore, useSharedHealthFactors } from '../../hooks';
import type { Lifecycle, PageId } from '../../types';
import { buildConnectionInsightsRows } from '../../utils/connectionInsights';
import { resolveLifecycleById } from '../../utils/pageLifecycle';
import {
  selectCodeEnvs,
  selectConnHealth,
  selectConnTypes,
  selectConnUsage,
  selectMemory,
  selectMounts,
  selectProjects,
  selectSanity,
  selectTreemapItems,
  selectUsers,
} from './missionControl/selectors';
import {
  CodeEnvsTile,
  ConnHealthTile,
  ConnInsightsTile,
  ConnInventoryTile,
  ConnUsageTile,
  ContainerExecsTile,
  CpuTile,
  DbHealthTile,
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

// Mission Control — the entire Admin Toolkit on one zero-scroll wall.
// 12 cols × 6 rows of named areas; every tile reads data the startup loaders
// already fetched, so this page issues no requests of its own.
const GRID_AREAS = [
  '"health health health fs fs fs mem mem coninv coninv conhlt conhlt"',
  '"health health health fs fs fs cpu cpu coninv coninv conuse conuse"',
  '"proj proj proj proj users users users conins conins conins plug plug"',
  '"proj proj proj proj users users users conins conins conins cenv cenv"',
  '"k8s k8s k8s cex cex cex llm llm llm pcomp pcomp pcomp"',
  '"logs logs logs sanity sanity sanity dbh dbh dbh envcmp envcmp envcmp"',
].join(' ');

const EPOCH = '1970-01-01T00:00:00.000Z';

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
    connectionDetails,
    connectionDatasetUsages,
    connectionLlmUsages,
    connectionLocalFilesystemUsages,
    connectionAudit,
    connectionUsageScanned,
    connectionUsageTotal,
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

  // The health tile gates on the global analysis aggregate (like SummaryPage):
  // no real score is revealed while modules are still streaming in.
  const analysisLifecycle = useMemo<Lifecycle>(() => {
    const al = parsedData.analysisLoading;
    if (!al) return { phase: 'queued' };
    const startedAt = al.startedAt || EPOCH;
    if (al.phase === 'done' || al.phase === 'error') {
      return { phase: 'done', startedAt, finishedAt: al.updatedAt || startedAt, isEmpty: false };
    }
    if (al.active) {
      return {
        phase: 'running',
        startedAt,
        progressPct: al.progressPct ?? 0,
        message: al.message,
        updatedAt: al.updatedAt || startedAt,
      };
    }
    return { phase: 'queued' };
  }, [parsedData.analysisLoading]);

  const mounts = useMemo(() => selectMounts(filesystemInfo), [filesystemInfo]);
  const treemap = useMemo(() => selectTreemapItems(dirTree), [dirTree]);
  const mem = useMemo(() => selectMemory(memoryInfo), [memoryInfo]);
  const connTypesVm = useMemo(() => selectConnTypes(connections), [connections]);
  const connHealthVm = useMemo(() => selectConnHealth(connectionHealth), [connectionHealth]);
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
  const insightsRows = useMemo(
    () =>
      buildConnectionInsightsRows({
        connectionDetails,
        connectionDatasetUsages,
        connectionLlmUsages,
        connectionLocalFilesystemUsages,
        connectionAudit,
        connectionHealth,
      }),
    [
      connectionDetails,
      connectionDatasetUsages,
      connectionLlmUsages,
      connectionLocalFilesystemUsages,
      connectionAudit,
      connectionHealth,
    ],
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
          lifecycle={analysisLifecycle}
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
        <ConnUsageTile lifecycle={lc('connections-usage')} onNavigate={setActivePage} vm={connUsageVm} />
        <ProjectsTile lifecycle={lc('projects')} onNavigate={setActivePage} vm={projectsVm} />
        <UsersTile lifecycle={lc('users')} onNavigate={setActivePage} vm={usersVm} onOwnerClick={handleOwnerClick} />
        <ConnInsightsTile lifecycle={lc('connections-insights')} onNavigate={setActivePage} rows={insightsRows} />
        <PluginsTile
          lifecycle={lc('plugins-installed')}
          onNavigate={setActivePage}
          count={pluginDetails?.length ?? pluginsCount ?? 0}
          pending={Boolean(pluginUsagesPending)}
        />
        <CodeEnvsTile lifecycle={lc('code-envs-cleaner')} onNavigate={setActivePage} vm={codeEnvsVm} />
        <K8sTile lifecycle={lc('k8s-insights')} onNavigate={setActivePage} />
        <ContainerExecsTile lifecycle={lc('container-execs')} onNavigate={setActivePage} />
        <LlmAuditTile lifecycle={lc('llm-audit')} onNavigate={setActivePage} summary={llmAudit?.summary} />
        <ProjectComputeTile lifecycle={lc('project-compute')} onNavigate={setActivePage} />
        <LogsTile
          lifecycle={lc('logs')}
          onNavigate={setActivePage}
          unique={Number(logStats?.['Unique Errors'] ?? 0)}
          snippet={rawLogErrors?.[0]?.data?.[0]}
        />
        <SanityTile lifecycle={lc('sanity-check')} onNavigate={setActivePage} vm={sanityVm} />
        <DbHealthTile lifecycle={lc('db-health')} onNavigate={setActivePage} />
        <EnvCompareTile lifecycle={lc('code-envs-comparison')} onNavigate={setActivePage} skippedEnvCount={skippedEnvCount} />
      </motion.div>
    </div>
  );
}
