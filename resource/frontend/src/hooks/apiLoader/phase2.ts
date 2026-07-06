/**
 * Phase 2 of the live-mode load: the six parallel secondary fetches
 * (connections / users / connections-audit / plugins / java-memory /
 * mail-channels) plus the GeneralSettings + ProjectStandards parser
 * application. Bodies moved verbatim from the old monolithic
 * useApiDataLoader.ts.
 */
import { GeneralSettingsParser } from '../../parsers/GeneralSettingsParser';
import { JavaMemoryParser } from '../../parsers/JavaMemoryParser';
import { ProjectStandardsParser } from '../../parsers/ProjectStandardsParser';
import type { ConnectionAuditResult } from '../../types';
import { fetchJson, fetchText } from '../../utils/api';
import { extractExecResourceConfigs } from '../../utils/execResources';
import type { LoaderCtx } from './context';
import type { LifecycleTracker } from './lifecycle';
import type {
  ConnectionsResponse,
  MailChannelsResponse,
  OverviewResponse,
  PluginsResponse,
  UsersResponse,
} from './types';

export async function loadPhase2(
  ctx: LoaderCtx,
  tracker: LifecycleTracker,
  overview: OverviewResponse,
  rawSettings: Record<string, unknown>,
  rawProjectStandards: Record<string, unknown>,
): Promise<void> {
  const { dispatch, log, getErrorMessage, timedFetch } = ctx;
  const { track } = tracker;
  // Glyph-bearing Phase-2 members are tracked individually (markRunning at
  // call, done/error on settle); the bare members (plugins/java-memory/
  // mail-channels) drive no sidebar glyph. All six fetches fire eagerly
  // here, so they still run in parallel.
  const connectionsTracked = track(
    'connectionsInventoryLoading',
    timedFetch('/api/connections', fetchJson<ConnectionsResponse>('/api/connections')),
    {
      startMessage: 'Loading connections',
      doneMessage: (v) =>
        `Loaded ${Object.keys((v as ConnectionsResponse).connections || {}).length} connection types`,
      isEmpty: (v) => Object.keys((v as ConnectionsResponse).connections || {}).length === 0,
    },
  );
  const usersTracked = track(
    'usersLoading',
    timedFetch('/api/users', fetchJson<UsersResponse>('/api/users')),
    {
      startMessage: 'Loading users',
      doneMessage: (v) => `${(v as UsersResponse).users?.length || 0} users`,
      isEmpty: (v) => ((v as UsersResponse).users?.length || 0) === 0,
    },
  );
  const connectionAuditTracked = track(
    'connectionsAuditLoading',
    timedFetch(
      '/api/connections/audit',
      fetchJson<{ connections: ConnectionAuditResult[]; summary: Record<string, number> }>(
        '/api/connections/audit',
      ),
    ),
    {
      startMessage: 'Auditing connections',
      doneMessage: (v) =>
        `${((v as { connections?: ConnectionAuditResult[] }).connections || []).length} findings`,
      isEmpty: (v) =>
        ((v as { connections?: ConnectionAuditResult[] }).connections || []).length === 0,
    },
  );
  const pluginsTracked = track(
    'pluginsLoading',
    timedFetch('/api/plugins', fetchJson<PluginsResponse>('/api/plugins')),
    {
      startMessage: 'Loading installed plugins',
      doneMessage: (v) => `${(v as PluginsResponse).pluginsCount || 0} plugins`,
      isEmpty: (v) => ((v as PluginsResponse).pluginsCount || 0) === 0,
    },
  );
  const javaMemoryBare = timedFetch('/api/java-memory', fetchText('/api/java-memory'));
  const mailChannelsBare = timedFetch(
    '/api/mail-channels',
    fetchJson<MailChannelsResponse>('/api/mail-channels'),
  );

  // track() never rejects → Promise.all is safe and unwraps to the inner
  // settled result, so `if (res.status === 'fulfilled')` branches stay.
  const [connectionsRes, usersRes, connectionAuditRes, pluginsRes] = await Promise.all([
    connectionsTracked,
    usersTracked,
    connectionAuditTracked,
    pluginsTracked,
  ]);
  const [javaMemoryRes, mailChannelsRes] = await Promise.allSettled([
    javaMemoryBare,
    mailChannelsBare,
  ]);

  if (ctx.cancelled()) return;

  if (connectionsRes.status === 'fulfilled') {
    tracker.data = {
      ...tracker.data,
      connections: connectionsRes.value.connections || {},
      connectionCounts: connectionsRes.value.connections || {},
      connectionDetails: connectionsRes.value.connectionDetails || [],
    };
    dispatch({ type: 'SET_PARSED_DATA', payload: tracker.data });
    log(`Loaded connections (${Object.keys(tracker.data.connections || {}).length} types)`);
  } else {
    log(`Failed /api/connections: ${getErrorMessage(connectionsRes.reason)}`, 'warn');
  }

  if (usersRes.status === 'fulfilled') {
    tracker.data = {
      ...tracker.data,
      userStats: usersRes.value.userStats || {},
      users: usersRes.value.users || [],
    };
    dispatch({ type: 'SET_PARSED_DATA', payload: tracker.data });
    log(`Loaded users (${tracker.data.users?.length || 0})`);
  } else {
    log(`Failed /api/users: ${getErrorMessage(usersRes.reason)}`, 'warn');
  }

  if (pluginsRes.status === 'fulfilled') {
    tracker.data = {
      ...tracker.data,
      plugins: pluginsRes.value.plugins || [],
      pluginDetails: pluginsRes.value.pluginDetails || [],
      pluginsCount: pluginsRes.value.pluginsCount || 0,
      // Usage counts arrive later via the deferred /api/plugins/usages scan.
      pluginUsagesPending: (pluginsRes.value.pluginDetails?.length || 0) > 0,
    };
    dispatch({ type: 'SET_PARSED_DATA', payload: tracker.data });
    log(`Loaded plugins (${tracker.data.pluginsCount || 0})`);
  } else {
    log(`Failed /api/plugins: ${getErrorMessage(pluginsRes.reason)}`, 'warn');
  }

  if (javaMemoryRes.status === 'fulfilled') {
    const parser = new JavaMemoryParser();
    const result = parser.parse(javaMemoryRes.value, 'env-default.sh');
    tracker.data = {
      ...tracker.data,
      javaMemorySettings: result.javaMemorySettings || {},
      javaMemoryLimits: result.javaMemorySettings || {},
      dssVersion: result.dssVersion || overview.dssVersion,
    };
    dispatch({ type: 'SET_PARSED_DATA', payload: tracker.data });
    log('Loaded Java memory settings');
  } else {
    log(`Failed /api/java-memory: ${getErrorMessage(javaMemoryRes.reason)}`, 'warn');
  }

  if (mailChannelsRes.status === 'fulfilled') {
    tracker.data = {
      ...tracker.data,
      mailChannels: mailChannelsRes.value.channels || [],
      configuredMailChannel: mailChannelsRes.value.configuredMailChannel,
    };
    dispatch({ type: 'SET_PARSED_DATA', payload: tracker.data });
    log(`Loaded mail channels (${tracker.data.mailChannels?.length || 0})`);
  } else {
    log(`Failed /api/mail-channels: ${getErrorMessage(mailChannelsRes.reason)}`, 'warn');
  }

  if (connectionAuditRes.status === 'fulfilled') {
    const auditFindings = connectionAuditRes.value.connections || [];
    tracker.data = {
      ...tracker.data,
      connectionAudit: auditFindings,
    };
    dispatch({ type: 'SET_PARSED_DATA', payload: tracker.data });
    log(`Loaded connection audit (${auditFindings.length} findings)`);
  } else {
    log(`Failed /api/connections/audit: ${getErrorMessage(connectionAuditRes.reason)}`, 'warn');
  }

  // Apply general settings parser after we have memory and java data
  const settingsParser = new GeneralSettingsParser();
  settingsParser.setExternalData({
    sparkSettings: tracker.data.sparkSettings,
    memoryInfo: tracker.data.memoryInfo,
    javaMemorySettings: tracker.data.javaMemorySettings,
    resourceLimits: tracker.data.resourceLimits,
  });
  const settingsResult = settingsParser.parse(JSON.stringify(rawSettings), 'general-settings.json');

  tracker.data = {
    ...tracker.data,
    generalSettings: settingsResult.generalSettings || {},
    enabledSettings: settingsResult.enabledSettings || {},
    sparkSettings: {
      ...(tracker.data.sparkSettings || {}),
      ...(settingsResult.sparkSettings || {}),
    },
    maxRunningActivities: settingsResult.maxRunningActivities || {},
    jekSettings: settingsResult.jekSettings || {},
    authSettings: settingsResult.authSettings || {},
    containerSettings: settingsResult.containerSettings || {},
    integrationSettings: settingsResult.integrationSettings || {},
    resourceLimits: settingsResult.resourceLimits || {},
    cgroupSettings: settingsResult.cgroupSettings || {},
    proxySettings: settingsResult.proxySettings || {},
    disabledFeatures: settingsResult.disabledFeatures || {},
    securityDefaults: settingsResult.securityDefaults || {},
    ldapAuthorizedGroups: settingsResult.ldapAuthorizedGroups || [],
  };
  dispatch({ type: 'SET_PARSED_DATA', payload: tracker.data });
  log('Applied GeneralSettings parser');

  // Derive containerExecDefaults from project-standards (modes) +
  // GeneralSettings (executionConfigsCount). On failure the parser
  // yields userCodeMode='NONE', visualRecipesMode='NONE' so the card
  // cleanly falls back to "local backend for execution".
  const containerSettingsRaw = (
    rawSettings as { containerSettings?: { executionConfigs?: unknown[] } }
  ).containerSettings;
  const executionConfigsCount = Array.isArray(containerSettingsRaw?.executionConfigs)
    ? containerSettingsRaw!.executionConfigs!.length
    : 0;
  // Keep the structured per-config resource fields too (the health score's
  // exec-config-resources component reads them); the count-only derivation
  // above stays for the exec-defaults card.
  const execResourceConfigs = extractExecResourceConfigs(rawSettings);
  const projectStandardsResult = new ProjectStandardsParser().parse(
    JSON.stringify(rawProjectStandards),
    'project-standards.json',
  );
  tracker.data = {
    ...tracker.data,
    execResourceConfigs,
    containerExecDefaults: {
      executionConfigsCount,
      userCodeMode: projectStandardsResult.userCodeMode,
      visualRecipesMode: projectStandardsResult.visualRecipesMode,
    },
  };
  dispatch({ type: 'SET_PARSED_DATA', payload: tracker.data });
  log(
    `Applied ProjectStandards parser (configs=${executionConfigsCount}, userCode=${projectStandardsResult.userCodeMode}, visualRecipes=${projectStandardsResult.visualRecipesMode})`,
  );
}
