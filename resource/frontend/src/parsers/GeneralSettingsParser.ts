import { BaseJSONParser } from './BaseParser';
import type {
  EnabledSettings,
  SparkSettings,
  MaxRunningActivities,
  AuthSettings,
  ContainerSettings,
  IntegrationSettings,
  ResourceLimits,
  CgroupSettings,
  ProxySettings,
  DisabledFeature,
  MemoryInfo,
  JavaMemorySettings,
  SecurityDefaultsSettings,
} from '../types';

interface GeneralSettingsData {
  [key: string]: unknown;
  ldapSettings?: { enabled?: boolean; authorizedGroups?: string[] };
  security?: { secureCookies?: boolean; [key: string]: unknown };
  metastoreCatalogsSettings?: {
    synchronizeTo?: { flavor?: string; [key: string]: unknown };
    [key: string]: unknown;
  };
  graphicsExportsEnabled?: boolean;
  defaultDatasetCreationSettings?: {
    forcedPreferedConnection?: string;
    preferedStorageFormats?: string;
    preferedConnection?: string;
    preferedUploadConnection?: string;
    [key: string]: unknown;
  };
  recipeEnginesPreferences?: {
    enginesPreferenceOrder?: unknown[];
    preferenceByRecipeType?: Record<string, unknown>;
    forbiddenEngines?: unknown[];
    [key: string]: unknown;
  };
  ssoSettings?: {
    enabled?: boolean;
    protocol?: string;
    samlSPParams?: {
      entityId?: string;
      displayNameAttribute?: string;
      emailAttribute?: string;
      signRequests?: boolean;
      enableGroups?: boolean;
      groupsAttribute?: string;
    };
    oidcSPParams?: {
      clientId?: string;
      displayNameAttribute?: string;
      emailAttribute?: string;
    };
  };
  azureADSettings?: { enabled?: boolean };
  customAuthSettings?: { enabled?: boolean };
  impersonation?: { enabled?: boolean };
  containerSettings?: {
    defaultExecutionConfig?: string;
    executionConfigs?: unknown[];
    cdeEnabled?: boolean;
    k8sEnabled?: boolean;
  };
  governIntegrationSettings?: {
    enabled?: boolean;
    nodeUrl?: string;
  };
  deployerClientSettings?: { mode?: string };
  limits?: {
    memSampleBytes?: { soft?: number; hard?: number };
  };
  maxRunningActivities?: number;
  maxRunningActivitiesPerJob?: number;
  jekSettings?: { maxRunningJobs?: number; numberOfReadyJEKs?: number };
  proxySettings?: {
    host?: string;
    port?: number;
    protocol?: string;
    useAuthentication?: boolean;
    username?: string;
    password?: string;
    noProxy?: string[];
  };
  cgroupSettings?: {
    enabled?: boolean;
    cgroupsVersion?: string;
    cgroupsV2Controllers?: string;
    hierarchiesMountPoint?: string;
    cgroups?: Array<{
      limits?: Array<{ key: string; value: string }>;
      cgroupPathTemplate?: string;
    }>;
    [key: string]: unknown;
  };
  sparkSettings?: {
    sparkEnabled?: boolean;
    additionalSparkSubmitJars?: string[];
    executionConfigs?: Array<{ name?: string }>;
    [key: string]: unknown;
  };
  aiDrivenAnalyticsSettings?: {
    enabled?: boolean;
    prepareAICompletionEnabled?: boolean;
    aiGenerateSQLEnabled?: boolean;
    aiExplanationsEnabled?: boolean;
    storiesAIEnabled?: boolean;
  };
  codeAssistantSettings?: { codeAssistantEnabled?: boolean };
  askDataikuSettings?: { enabled?: boolean };
  popularDatasetsSettings?: { enablePopularDatasets?: boolean };
}

interface GeneralSettingsResult {
  generalSettings: GeneralSettingsData;
  enabledSettings: EnabledSettings;
  sparkSettings: SparkSettings;
  maxRunningActivities: MaxRunningActivities;
  jekSettings: { maxRunningJobs?: number; numberOfReadyJEKs?: number };
  authSettings: AuthSettings;
  containerSettings: ContainerSettings;
  integrationSettings: IntegrationSettings;
  resourceLimits: ResourceLimits;
  cgroupSettings: CgroupSettings;
  proxySettings: ProxySettings;
  disabledFeatures: Record<string, DisabledFeature>;
  securityDefaults: SecurityDefaultsSettings;
  ldapAuthorizedGroups: string[];
}

interface ExternalData {
  sparkSettings?: SparkSettings;
  memoryInfo?: MemoryInfo;
  javaMemorySettings?: JavaMemorySettings;
  resourceLimits?: ResourceLimits;
}

export class GeneralSettingsParser extends BaseJSONParser<GeneralSettingsResult> {
  private externalData: ExternalData = {};

  setExternalData(data: ExternalData): void {
    this.externalData = data;
  }

  processData(data: GeneralSettingsData): GeneralSettingsResult {
    const result: GeneralSettingsResult = {
      generalSettings: data,
      enabledSettings: {},
      sparkSettings: {},
      maxRunningActivities: {},
      jekSettings: {},
      authSettings: {},
      containerSettings: {},
      integrationSettings: {},
      resourceLimits: {},
      cgroupSettings: {},
      proxySettings: {},
      disabledFeatures: {},
      securityDefaults: {},
      ldapAuthorizedGroups: [],
    };

    // Extract enabled settings
    for (const [key, value] of Object.entries(data)) {
      if (typeof value !== 'object' && (value === true || value === false)) {
        result.enabledSettings[key] = value;
      }
    }

    // Extract max running activities - always include these values
    result.maxRunningActivities['Max Running Activities'] =
      data.maxRunningActivities ?? 'Not set';
    result.maxRunningActivities['Max Running Activities Per Job'] =
      data.maxRunningActivitiesPerJob ?? 'Not set';

    result.jekSettings = data.jekSettings ?? {};

    // Process spark settings
    this.processSparkSettings(data, result);

    // Process all sub-settings
    result.authSettings = this.parseAuthSettings(data);
    result.containerSettings = this.parseContainerSettings(data);
    result.integrationSettings = this.parseIntegrationSettings(data);
    result.resourceLimits = this.parseResourceLimits(data);
    result.proxySettings = this.parseProxySettings(data);
    result.cgroupSettings = this.parseCgroupsSettings(data);
    result.securityDefaults = this.parseSecurityDefaults(data);
    result.ldapAuthorizedGroups = Array.isArray(data.ldapSettings?.authorizedGroups)
      ? (data.ldapSettings!.authorizedGroups as string[])
      : [];

    // Check for disabled features
    result.disabledFeatures = this.checkDisabledFeatures(data);

    return result;
  }

  private parseSecurityDefaults(
    data: GeneralSettingsData
  ): SecurityDefaultsSettings {
    const settings: SecurityDefaultsSettings = {};

    settings['Secure Cookies'] =
      data.security?.secureCookies === true ? 'Enabled' : 'Disabled';

    const flavor = data.metastoreCatalogsSettings?.synchronizeTo?.flavor;
    settings['Metastore Flavor'] = flavor && String(flavor).length > 0 ? String(flavor) : 'Not set';

    settings['Graphics Export'] =
      data.graphicsExportsEnabled === true ? 'Enabled' : 'Disabled';

    const dds = data.defaultDatasetCreationSettings || {};
    settings['Forced Preferred Connection'] =
      dds.forcedPreferedConnection && dds.forcedPreferedConnection.length > 0
        ? dds.forcedPreferedConnection
        : 'Not set';
    settings['Preferred Storage Formats'] =
      dds.preferedStorageFormats && dds.preferedStorageFormats.length > 0
        ? dds.preferedStorageFormats
        : 'Not set';

    const engines = data.recipeEnginesPreferences || {};
    const hasOrder = Array.isArray(engines.enginesPreferenceOrder) && engines.enginesPreferenceOrder.length > 0;
    const hasByType = engines.preferenceByRecipeType && Object.keys(engines.preferenceByRecipeType).length > 0;
    settings['Engine Preferences'] = hasOrder || hasByType ? 'Configured' : 'Default';

    return settings;
  }

  private checkDisabledFeatures(
    data: GeneralSettingsData
  ): Record<string, DisabledFeature> {
    const disabled: Record<string, DisabledFeature> = {};
    const add = (name: string, desc: string, url: string) =>
      (disabled[name] = { status: 'Disabled', description: desc, url });

    // Check AI-driven analytics settings
    if (data.aiDrivenAnalyticsSettings) {
      const ai = data.aiDrivenAnalyticsSettings;
      if ('enabled' in ai) {
        // New format (DSS 14+): single toggle
        if (ai.enabled === false)
          add(
            'AI Assistants',
            'AI-driven analytics features including prepare completion, SQL generation, explanations, and stories assistance',
            'https://doc.dataiku.com/dss/latest/ai-assistants/index.html'
          );
      } else {
        // Old format (DSS 12.x): 4 granular toggles
        if (!ai.prepareAICompletionEnabled)
          add(
            'AI: Prepare Completion',
            'AI-powered suggestions and completions in visual recipes (Prepare, Group, Window, etc.) to help build transformation steps faster',
            'https://knowledge.dataiku.com/latest/data-preparation/prepare-recipe/how-to-generate-steps.html'
          );
        if (!ai.aiGenerateSQLEnabled)
          add(
            'AI: Generate SQL',
            'Generate SQL queries from natural language descriptions in SQL notebooks and recipes, powered by LLM integration',
            'https://knowledge.dataiku.com/latest/code/sql/concept-ai-sql-assistant.html'
          );
        if (!ai.aiExplanationsEnabled)
          add(
            'AI: Explanations',
            'Get AI-generated explanations for code blocks, formulas, recipe logic, and analysis results to improve understanding',
            'https://knowledge.dataiku.com/latest/collaboration/wikis-documentation/concept-explain-flow.html'
          );
        if (!ai.storiesAIEnabled)
          add(
            'AI: Stories',
            'AI assistance for generating narrative content and insights when creating Dataiku Stories and dashboards',
            'https://knowledge.dataiku.com/latest/collaboration/stories/tutorial-stories-with-genai.html'
          );
      }
    }

    if (data.codeAssistantSettings?.codeAssistantEnabled === false)
      add(
        'Code Assistant',
        'Inline AI code completion and suggestions in Python/R notebooks and code recipes, powered by LLM services',
        'https://doc.dataiku.com/dss/latest/ai-assistants/code-assistant.html'
      );
    if (data.askDataikuSettings?.enabled === false)
      add(
        'Ask Dataiku',
        'Interactive AI chatbot that answers questions about DSS features, APIs, and best practices within the interface',
        'https://doc.dataiku.com/dss/latest/generative-ai/chat-ui/answers.html'
      );
    if (data.sparkSettings?.sparkEnabled === false)
      add(
        'Spark',
        'Enable Apache Spark for distributed processing of large datasets across visual recipes, code recipes, and SQL',
        'https://doc.dataiku.com/dss/latest/spark/index.html'
      );
    if (data.containerSettings?.cdeEnabled === false)
      add(
        'Container Execution (CDE)',
        'Run code recipes and notebooks in isolated Docker containers with custom dependencies, separate from base DSS environment',
        'https://doc.dataiku.com/dss/latest/containers/concepts.html#containerized-execution-configurations'
      );
    if (data.containerSettings?.k8sEnabled === false)
      add(
        'Kubernetes',
        'Deploy and execute containerized workloads on Kubernetes clusters for scalable, distributed processing',
        'https://doc.dataiku.com/dss/latest/containers/kubernetes.html'
      );
    if (data.cgroupSettings?.enabled === false)
      add(
        'CGroups',
        'Linux control groups to enforce CPU and memory limits per process, preventing resource exhaustion from runaway jobs',
        'https://doc.dataiku.com/dss/latest/operations/cgroups.html'
      );
    if (data.governIntegrationSettings?.enabled === false)
      add(
        'Govern Integration',
        'Connect to Dataiku Govern for centralized governance, compliance tracking, model validation, and artifact management',
        'https://doc.dataiku.com/dss/latest/governance/index.html'
      );
    if (data.popularDatasetsSettings?.enablePopularDatasets === false)
      add(
        'Popular Datasets',
        'Track and surface frequently accessed datasets across projects to help users discover commonly used data sources',
        'https://knowledge.dataiku.com/latest/getting-started/dataiku-ui/concept-popular-datasets.html'
      );
    if (data.impersonation?.enabled === false)
      add(
        'Impersonation',
        'Run jobs as the Unix user who triggered them (instead of the dataiku user) for proper file permissions and security auditing',
        'https://doc.dataiku.com/dss/latest/user-isolation/index.html'
      );

    // Deployer mode check (special case - not binary enabled/disabled)
    if (
      data.deployerClientSettings?.mode &&
      data.deployerClientSettings.mode !== 'LOCAL'
    ) {
      disabled['Deployer Client'] = {
        status: `Mode: ${data.deployerClientSettings.mode}`,
        description:
          'Deployer mode should be LOCAL for standard deployments. This setting controls how project bundles are managed and deployed to other DSS instances',
        url: 'https://knowledge.dataiku.com/latest/mlops-o16n/project-deployment/concept-project-deployment.html',
      };
    }

    return disabled;
  }

  private processSparkSettings(
    data: GeneralSettingsData,
    result: GeneralSettingsResult
  ): void {
    // Preserve existing Spark version from diag.txt
    const existingSpark = this.externalData.sparkSettings || {};
    const sparkVersionFromDiag = existingSpark['Spark Version'];

    result.sparkSettings = {};
    if (sparkVersionFromDiag) {
      result.sparkSettings['Spark Version'] = sparkVersionFromDiag;
    }

    if (data.sparkSettings && typeof data.sparkSettings === 'object') {
      // Extract simple key-value pairs
      for (const [key, value] of Object.entries(data.sparkSettings)) {
        if (typeof value !== 'object' && !Array.isArray(value) && value !== undefined && value !== null) {
          result.sparkSettings[key] = value as string | number | boolean;
        }
      }

      // Handle arrays with simple formatting
      if (
        data.sparkSettings.additionalSparkSubmitJars &&
        Array.isArray(data.sparkSettings.additionalSparkSubmitJars)
      ) {
        result.sparkSettings['additionalSparkSubmitJars'] =
          `${data.sparkSettings.additionalSparkSubmitJars.length} jar(s)`;
      }

      // Count execution configs
      if (
        data.sparkSettings.executionConfigs &&
        Array.isArray(data.sparkSettings.executionConfigs)
      ) {
        result.sparkSettings['executionConfigs'] =
          `${data.sparkSettings.executionConfigs.length} configuration(s)`;

        // Extract execution config names
        const configNames = data.sparkSettings.executionConfigs
          .map((config) => config.name)
          .filter(Boolean)
          .join(', ');

        if (configNames) {
          result.sparkSettings['executionConfigNames'] = configNames;
        }
      }
    }
  }

  private parseAuthSettings(data: GeneralSettingsData): AuthSettings {
    const authSettings: AuthSettings = {};

    // Always include LDAP status
    authSettings['LDAP Authentication'] = data.ldapSettings?.enabled === true ? 'Enabled' : 'Disabled';

    if (data.ldapSettings?.enabled === true) {
      const groups = Array.isArray(data.ldapSettings.authorizedGroups)
        ? data.ldapSettings.authorizedGroups
        : [];
      authSettings['LDAP Authorized Groups'] =
        groups.length > 0 ? `${groups.length} configured` : 'None (any LDAP user can sign in)';
    }

    // Always include SSO status
    if (data.ssoSettings) {
      const isSSOConfigured = data.ssoSettings.enabled;
      authSettings['SSO Authentication'] = isSSOConfigured
        ? `Enabled (${data.ssoSettings.protocol})`
        : 'Disabled';

      if (isSSOConfigured) {
        if (
          data.ssoSettings.protocol === 'SAML' &&
          data.ssoSettings.samlSPParams
        ) {
          this.processSamlSettings(data.ssoSettings.samlSPParams, authSettings);
        }

        if (
          data.ssoSettings.protocol === 'OIDC' &&
          data.ssoSettings.oidcSPParams
        ) {
          this.processOidcSettings(data.ssoSettings.oidcSPParams, authSettings);
        }
      }
    }

    // Always include Azure AD status
    authSettings['Azure AD Authentication'] = data.azureADSettings?.enabled === true ? 'Enabled' : 'Disabled';

    // Always include Custom Auth status
    authSettings['Custom Authentication'] = data.customAuthSettings?.enabled === true ? 'Enabled' : 'Disabled';

    // Always include Impersonation status
    authSettings['Impersonation'] = data.impersonation?.enabled === true ? 'Enabled' : 'Disabled';

    return authSettings;
  }

  private processSamlSettings(
    samlParams: NonNullable<GeneralSettingsData['ssoSettings']>['samlSPParams'],
    authSettings: AuthSettings
  ): void {
    if (!samlParams) return;

    if (samlParams.entityId) {
      authSettings['SSO Entity ID'] = {
        value: samlParams.entityId,
        truncate: true,
        maxLength: 20,
      };
    }

    if (samlParams.displayNameAttribute) {
      authSettings['SSO Display Name Attribute'] =
        samlParams.displayNameAttribute;
    }

    if (samlParams.emailAttribute) {
      authSettings['SSO Email Attribute'] = samlParams.emailAttribute;
    }

    authSettings['SSO Sign Requests'] =
      samlParams.signRequests === true ? 'Yes' : 'No';
    authSettings['SSO Groups Enabled'] =
      samlParams.enableGroups === true ? 'Yes' : 'No';

    if (samlParams.groupsAttribute) {
      authSettings['SSO Groups Attribute'] = samlParams.groupsAttribute;
    }
  }

  private processOidcSettings(
    oidcParams: NonNullable<GeneralSettingsData['ssoSettings']>['oidcSPParams'],
    authSettings: AuthSettings
  ): void {
    if (!oidcParams) return;

    if (oidcParams.clientId) {
      authSettings['SSO Client ID'] = oidcParams.clientId;
    }

    if (oidcParams.displayNameAttribute) {
      authSettings['SSO Display Name Attribute'] =
        oidcParams.displayNameAttribute;
    }

    if (oidcParams.emailAttribute) {
      authSettings['SSO Email Attribute'] = oidcParams.emailAttribute;
    }
  }

  private parseContainerSettings(
    data: GeneralSettingsData
  ): ContainerSettings {
    const containerSettings: ContainerSettings = {};
    if (data.containerSettings) {
      containerSettings['Default Execution Config'] =
        data.containerSettings.defaultExecutionConfig || 'Not set';
      containerSettings['Container Execution Configs'] = data.containerSettings
        .executionConfigs
        ? data.containerSettings.executionConfigs.length
        : 0;
    }
    return containerSettings;
  }

  private parseIntegrationSettings(
    data: GeneralSettingsData
  ): IntegrationSettings {
    const integrationSettings: IntegrationSettings = {};
    if (data.governIntegrationSettings) {
      integrationSettings['Govern Integration'] =
        data.governIntegrationSettings.enabled === true
          ? `Enabled (${data.governIntegrationSettings.nodeUrl || ''})`
          : 'Disabled';
    }

    if (data.deployerClientSettings) {
      integrationSettings['Deployer Mode'] =
        data.deployerClientSettings.mode || 'Unknown';
    }

    return integrationSettings;
  }

  private parseResourceLimits(data: GeneralSettingsData): ResourceLimits {
    const resourceLimits: ResourceLimits = {};
    if (data.limits && data.limits.memSampleBytes) {
      const softLimit = data.limits.memSampleBytes.soft;
      resourceLimits['Memory Sample Soft Limit'] =
        softLimit && softLimit > 0
          ? Math.round(softLimit / (1024 * 1024)) + ' MB'
          : 'None';

      const hardLimit = data.limits.memSampleBytes.hard;
      resourceLimits['Memory Sample Hard Limit'] =
        hardLimit && hardLimit > 0
          ? Math.round(hardLimit / (1024 * 1024)) + ' MB'
          : 'None';
    }

    // Note: maxRunningActivities is now in its own section (maxRunningActivities)
    // so we don't duplicate it here

    return resourceLimits;
  }

  private parseProxySettings(data: GeneralSettingsData): ProxySettings {
    const proxySettings: ProxySettings = {};

    if (!data.proxySettings) {
      // Always return some baseline data even if not configured
      proxySettings['Proxy Configured'] = 'No';
      return proxySettings;
    }

    const proxy = data.proxySettings;
    const isProxyConfigured = (proxy.port ?? 0) > 0 && proxy.host;
    proxySettings['Proxy Configured'] = isProxyConfigured ? 'Yes' : 'No';

    if (isProxyConfigured) {
      proxySettings['Host'] = proxy.host || 'Not set';
      proxySettings['Port'] = proxy.port || 'Not set';
      proxySettings['Protocol'] = proxy.protocol || 'http';

      if (proxy.useAuthentication) {
        proxySettings['Authentication'] = 'Yes';
        proxySettings['Username'] = proxy.username || 'Not set';
        proxySettings['Password'] = proxy.password ? '[Set]' : '[Not set]';
      } else {
        proxySettings['Authentication'] = 'No';
      }

      if (proxy.noProxy && Array.isArray(proxy.noProxy)) {
        proxySettings['No Proxy Hosts'] = proxy.noProxy.join(', ') || 'None';
      }
    }

    return proxySettings;
  }

  private parseCgroupsSettings(data: GeneralSettingsData): CgroupSettings {
    if (!data.cgroupSettings) {
      return {};
    }

    const cgroupSettings: CgroupSettings = {};
    const cgroups = data.cgroupSettings;

    // Basic settings
    cgroupSettings['Enabled'] = cgroups.enabled === true ? 'Yes' : 'No';
    cgroupSettings['CGroups Version'] =
      cgroups.cgroupsVersion || 'Not specified';
    cgroupSettings['V2 Controllers'] =
      cgroups.cgroupsV2Controllers || 'Not specified';
    cgroupSettings['Mount Point'] =
      cgroups.hierarchiesMountPoint || 'Not specified';

    // Get the main cgroup limit if available
    if (cgroups.cgroups && cgroups.cgroups.length > 0) {
      const mainCgroup = cgroups.cgroups[0];
      if (mainCgroup.limits && mainCgroup.limits.length > 0) {
        const limit = mainCgroup.limits[0];
        if (limit.value) {
          cgroupSettings['Memory Limit'] = limit.value;
        }
      }
      if (mainCgroup.cgroupPathTemplate) {
        cgroupSettings['Main Path Template'] = mainCgroup.cgroupPathTemplate;
      }
    }

    // Count configured targets
    this.processTargetTypes(cgroups, cgroupSettings);

    return cgroupSettings;
  }

  private processTargetTypes(
    cgroups: GeneralSettingsData['cgroupSettings'],
    cgroupSettings: CgroupSettings
  ): void {
    if (!cgroups) return;

    // Known non-target-type keys to skip
    // These are either config keys or legacy keys not part of cgroup placement settings
    const skipKeys = new Set([
      'enabled',
      'cgroupsVersion',
      'cgroupsV2Controllers',
      'hierarchiesMountPoint',
      'cgroups',
      'jobExecutionKernels',
      'edaRecipes',
      'metricsChecks',
      'deploymentHooks',
      'devLambdaServer',
      'customPythonDataAccessComponents',
    ]);

    let configuredTargets = 0;
    let emptyTargets = 0;
    const emptyTargetNames: string[] = [];

    // Dynamically detect target types by checking for objects with a 'targets' array
    for (const [key, value] of Object.entries(cgroups)) {
      if (skipKeys.has(key)) continue;

      // Check if this looks like a target type config (has targets array)
      if (value && typeof value === 'object' && 'targets' in value) {
        const typeData = value as { targets?: unknown[] };
        if (Array.isArray(typeData.targets)) {
          if (typeData.targets.length > 0) {
            configuredTargets++;
          } else {
            emptyTargets++;
            emptyTargetNames.push(key);
          }
        }
      }
    }

    cgroupSettings['Configured Target Types'] = configuredTargets;
    if (emptyTargets > 0) {
      cgroupSettings['Empty Target Types'] = emptyTargetNames.join(', ');
    }
  }

}
