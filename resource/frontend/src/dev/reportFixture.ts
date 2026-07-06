/* Dev-only fixture for report-preview.html — realistic tam-global-shaped data
   so the deck can be designed/verified without a live backend or LLM run.
   Not imported by the app bundle (vite builds index.html only). */
import type { ParsedData } from '../types';
import type { ReportData } from '../utils/prepareReportData';

const users = [
  ...Array.from({ length: 60 }, (_, i) => ({ login: `designer${i}`, displayName: `Designer ${i}`, userProfile: 'FULL_DESIGNER', enabled: i < 56 })),
  ...Array.from({ length: 9 }, (_, i) => ({ login: `analyst${i}`, displayName: `Analyst ${i}`, userProfile: 'DATA_ANALYST', enabled: true })),
  ...Array.from({ length: 3 }, (_, i) => ({ login: `ai${i}`, displayName: `AI Consumer ${i}`, userProfile: 'AI_CONSUMER', enabled: true })),
  ...Array.from({ length: 4 }, (_, i) => ({ login: `svc${i}`, displayName: `Service ${i}`, userProfile: 'DATA_SCIENTIST', enabled: i < 3 })),
  { login: 'reader1', displayName: 'Reader One', userProfile: 'READER', enabled: true },
];

const codeEnvs = Array.from({ length: 168 }, (_, i) => ({
  name: `env_${i}`, version: '1', language: i < 159 ? 'PYTHON' : 'R',
  owner: 'admin', sizeBytes: 1e9, usageCount: i < 45 ? 0 : (i % 30) + 1, projectCount: i % 12,
}));

const gb = (n: number) => n * 1024 ** 3;

export const FIXTURE_PARSED = {
  company: 'TAM Global',
  dssVersion: '14.7.0',
  osInfo: 'AlmaLinux 9.8 (Shamrock Pampas Cat)',
  cpuCores: '4',
  pythonVersion: '3.11.13',
  lastRestartTime: 'Jun 25, 2026',
  licenseInfo: { licenseType: 'ENTERPRISE', expiresOn: '2027-01-31', maxUsers: 100, hasExpired: false },
  users,
  codeEnvs,
  pythonVersionCounts: { '3.9': 82, '3.11': 23, '3.10': 16, '3.6': 16, '3.8': 10, '3.7': 4 },
  rVersionCounts: { '4.3': 9 },
  filesystemInfo: [
    { Filesystem: 'tmpfs', Size: '16G', Used: '0', Available: '16G', 'Use%': '0%', 'Mounted on': '/dev/shm' },
    { Filesystem: '/dev/nvme1n1', Size: '2.0T', Used: '1.5T', Available: '478G', 'Use%': '77%', 'Mounted on': '/data' },
    { Filesystem: '/dev/nvme0n1p2', Size: '149G', Used: '22G', Available: '128G', 'Use%': '15%', 'Mounted on': '/' },
    { Filesystem: '/dev/nvme0n1p1', Size: '994M', Used: '202M', Available: '793M', 'Use%': '21%', 'Mounted on': '/boot' },
    { Filesystem: '/dev/nvme0n1p15', Size: '99M', Used: '3.4M', Available: '96M', 'Use%': '4%', 'Mounted on': '/boot/efi' },
  ],
  memoryInfo: { total: '31G', used: '19G', available: '11G' },
  javaMemorySettings: { BACKEND: '8g', FEK: '2g', JEK: '2g' },
  connectionCounts: {
    S3: 23, Snowflake: 13, CustomLLM: 9, OpenAI: 7, AzureOpenAI: 6,
    PostgreSQL: 5, Bedrock: 4, VertexAI: 3, Anthropic: 3, Databricks: 3,
    BigQuery: 2, Redshift: 2, Kafka: 2, MongoDB: 2, ElasticSearch: 2,
    Azure: 2, GCS: 2, FTP: 1, SFTP: 1, HTTP: 1, MySQL: 1, Oracle: 1, SQLServer: 1, Filesystem: 12,
  },
  connectionDetails: Array.from({ length: 97 }, (_, i) => ({ name: `conn_${i}`, type: 'S3' })),
  disabledFeatures: {
    govern: { status: 'Disabled', description: 'Govern Integration' },
    sso: { status: 'Disabled', description: 'Enterprise SSO' },
    ldap: { status: 'Disabled', description: 'LDAP Authentication' },
  },
  pluginDetails: Array.from({ length: 137 }, (_, i) => ({ id: `plugin_${i}`, label: `Plugin ${i}`, installedVersion: '1.0.0', isDev: i < 60 })),
  clusters: [{ name: 'fe-sandbox-cluster', status: 'RUNNING', version: '1.29' }],
  logStats: { 'Unique Errors': 1, 'Total Lines': 1006, 'Displayed Errors': 1 },
  rawLogErrors: ['java.lang.IllegalArgumentException: Unknown runnable type'],
  projectFootprintSummary: { projectCount: 451, instanceAvgProjectGB: 0.12, instanceProjectRiskAvg: 0.139 },
  projectFootprint: [
    { projectKey: 'DIAG_PARSER_BRANCH1', name: 'Diag Parser Branch 1', totalBytes: gb(11.96), totalGB: 11.96, projectSizeHealth: 'red' },
    { projectKey: 'UKPROPERTYPRICES', name: 'UK Property Prices', totalBytes: gb(4.6), totalGB: 4.6, projectSizeHealth: 'red' },
    { projectKey: '2025_GENERAL_TEST_PROJECT', name: 'General Test', totalBytes: gb(4.35), totalGB: 4.35, projectSizeHealth: 'red' },
    { projectKey: 'SOL_DKU_MONITORING_AK', name: 'Monitoring Clone', totalBytes: gb(2.8), totalGB: 2.8, projectSizeHealth: 'orange' },
    { projectKey: 'NLP_LAB', name: 'NLP Lab', totalBytes: gb(2.1), totalGB: 2.1, projectSizeHealth: 'orange' },
    { projectKey: 'CHURN_PREDICTION', name: 'Churn Prediction', totalBytes: gb(1.7), totalGB: 1.7, projectSizeHealth: 'orange' },
  ],
  projectCostData: {
    span: { firstTs: '2026-06-12T00:00:00Z', lastTs: '2026-07-01T00:00:00Z' },
    totals: { memGBh: 4210, cpuH: 987, llmUSD: 342.18, projectCount: 58 },
    projects: [
      { projectKey: 'LLM_FACTORY', memGBh: 812, cpuH: 204, llmUSD: 188.4 },
      { projectKey: 'CHURN_PREDICTION', memGBh: 604, cpuH: 171, llmUSD: 0 },
      { projectKey: 'DIAG_PARSER_BRANCH1', memGBh: 415, cpuH: 98, llmUSD: 12.2 },
      { projectKey: 'SOL_DKU_MONITORING_AK', memGBh: 322, cpuH: 87, llmUSD: 0 },
      { projectKey: 'UKPROPERTYPRICES', memGBh: 218, cpuH: 54, llmUSD: 0 },
      { projectKey: 'NLP_LAB', memGBh: 174, cpuH: 41, llmUSD: 66.1 },
    ],
    users: [], contextTypes: [], idleResources: [],
  },
} as unknown as ParsedData;

export const FIXTURE_REPORT: ReportData = {
  slides: {
    executive_summary: {
      headline: 'Healthy and busy — with storage on the clock',
      overall_status: 'GOOD WITH CAVEATS — A healthy, actively used platform with excellent GenAI uptake, but storage growth and code-env/project sprawl need proactive governance this quarter.',
      findings: [
        'Instance runs the current DSS 14.7.0 on Python 3.11 with a rich LLM Mesh footprint (35+ AI connections) supporting strong agentic and GenAI workloads.',
        'The /data volume is at 77% (1.5TB of 2.0TB) and 451 projects are largely unmanaged sandboxes, creating storage and governance pressure.',
        '168 code environments exist with ~45 unused and many on end-of-life Python 3.6/3.7, warranting a consolidation and modernization effort.',
      ],
    },
    instance_overview: {
      headline: 'Current release, modest hardware, heavy usage',
      narrative: '• Running DSS 14.7.0, the current release, on AlmaLinux 9.8 — excellent version currency\n• Python 3.11.13 as the builtin interpreter aligns with modern supported runtimes\n• 4 cores / 8 threads is modest for 77 enabled users and heavy GenAI workloads\n• Last restart Jun 25, 2026 indicates good uptime stability',
    },
    projects: {
      headline: 'Growth is outpacing governance',
      narrative: '• 451 projects on the instance, a very large footprint indicating heavy platform use\n• Average project risk score of 0.139 is low, reflecting generally healthy individual projects\n• Many projects are per-user solution clones suggesting demo/sandbox sprawl\n• Average project size is only 0.12GB, so a small number of projects drive most storage',
      highlights: ['451 projects, low avg risk', 'Sandbox sprawl from solution clones', 'Storage concentrated in few projects'],
    },
    project_footprint: {
      headline: 'A handful of projects own the disk',
      narrative: '• Top project DIAG_PARSER_BRANCH1 consumes 11.96GB, flagged red for size\n• UKPROPERTYPRICES (4.6GB) and 2025_GENERAL_TEST_PROJECT (4.35GB) are next largest, both red\n• Numerous SOL_DKU_MONITORING clones each hold 0.5–2.8GB, multiplying storage unnecessarily\n• We recommend reviewing top-20 projects for cleanup of intermediate datasets',
      risks: ['4 projects flagged red for size', '15+ projects flagged orange'],
    },
    code_envs: {
      headline: '168 environments, one instance',
      narrative: '• 168 code environments across Python and R — a very high count for one instance\n• Python 3.9 dominates with 82 environments, followed by 3.11 (23) and 3.10 (16)\n• 16 environments still on Python 3.6 and 4 on 3.7 — both end-of-life\n• 9 R environments present, several unused',
    },
    code_env_health: {
      headline: 'Python 3.6 refuses to die',
      narrative: '• Approximately 45 environments show zero usage, prime cleanup candidates\n• py39-base is the workhorse with 392 usages across 365 projects — mission critical\n• py36 remains heavily used (116 usages, 43 projects) despite Python 3.6 being EOL\n• We recommend consolidating redundant per-user test environments',
      upgrade_paths: ['py36 → py311-base (43 projects)', 'py37 → py311-base (4 envs)', 'Remove 45 zero-usage environments'],
    },
    filesystem: {
      headline: '/data is 77% full and climbing',
      narrative: '• /data volume at 77% used — 1.5TB of 2.0TB consumed, 478GB free\n• Root filesystem healthy at 15% (22GB of 149GB)\n• At current growth /data is the primary capacity risk within 6–12 months\n• We recommend enabling storage cleanup macros and monitoring the /data trend closely',
      warnings: ['/data at 77% — plan expansion before 85%'],
    },
    memory: {
      headline: 'No swap, no safety net',
      narrative: '• System RAM is 31GB total with 19GB used and 11GB in buffer/cache\n• Backend JVM heap set to 8g, FEK and JEK each at 2g\n• Swap is not configured — risk of OOM kills under memory pressure\n• cgroups v2 enabled with a 38g memory limit exceeding physical RAM',
      tuning_recs: ['Configure swap on the DSS host', 'Review heap headroom for 77 users', 'Align cgroup limit with physical RAM'],
    },
    connections: {
      headline: 'A cloud-first, LLM-heavy landscape',
      narrative: '• 97 connections configured across 24 distinct types — a rich, diverse landscape\n• S3 leads with 23 connections, followed by Snowflake (13) reflecting a cloud-first data strategy\n• Extensive LLM Mesh: OpenAI (7), AzureOpenAI (6), CustomLLM (9), plus Bedrock, Anthropic, Vertex\n• Many single-user/test connections suggest a cleanup opportunity',
    },
    issues: {
      headline: 'Local accounts, disabled governance',
      narrative: '• Govern Integration is Disabled — no centralized governance, compliance, or model validation\n• Impersonation is enabled while LDAP/SSO/Azure AD auth are all disabled\n• 137 plugins installed, with ~60 in dev mode indicating heavy experimentation\n• No enterprise SSO exposes reliance on local accounts for 77 users',
      risk_level: 'medium',
    },
    users: {
      headline: 'A builder-heavy population',
      narrative: '• 77 total users with 72 enabled — healthy active population\n• 60 FULL_DESIGNER users indicates a heavily builder-oriented instance\n• Only 1 READER and 3 AI_CONSUMER — limited consumption-tier usage\n• 19 groups and 4 technical accounts support structured access',
    },
    compute_cost: {
      headline: 'Where the compute actually goes',
      narrative: '• 4,210 memory GB·h and 987 CPU hours consumed in the 19-day audit window\n• LLM_FACTORY dominates with 812 GB·h and $188 of the $342 LLM spend\n• 58 of 451 projects generated measurable compute — usage is highly concentrated\n• Idle notebooks and abandoned jobs are a small but recoverable cost slice',
      drivers: ['LLM_FACTORY: 55% of LLM spend', 'Top 6 projects: 60% of memory GB·h'],
    },
    logs: {
      headline: 'Remarkably clean logs',
      narrative: '• Only 1 unique error across 1,006 log lines analyzed — very clean log health\n• Error relates to an unknown runnable type from a plugin auto-documentation agent\n• No recurring OOM, crash, or connection failures observed\n• We recommend resolving the orphaned plugin runnable reference',
      patterns: ['IllegalArgumentException: Unknown runnable type pyrunnable_auto-doc', 'WARN Pluginifiable: missing plugin meta (dev plugin)'],
    },
    rec_critical: {
      items: [
        { title: 'Address /data Volume Capacity', description: 'The /data mount is at 77% (1.5TB of 2.0TB). Run disk-cleanup macros, purge intermediate datasets, and plan expansion before reaching 85%.', impact: 'Prevents pipeline failures from full disk' },
        { title: 'Configure Swap Space', description: 'Swap is not configured on a 31GB RAM host serving 77 users with an 8g backend heap. Add swap to prevent hard OOM kills of the DSS backend.', impact: 'Protects backend stability under load' },
      ],
    },
    rec_important: {
      items: [
        { title: 'Enable Enterprise SSO', description: 'LDAP, SSO, and Azure AD are all disabled while 77 users rely on local accounts. Configure SSO/LDAP to centralize authentication and enforce security policy.', impact: 'Stronger security and simpler user management' },
        { title: 'Migrate EOL Python Environments', description: '20+ environments run end-of-life Python 3.6/3.7, including the heavily-used py36 (116 usages). Migrate these to Python 3.11 to stay supported and secure.', impact: 'Maintains supportability and security patching' },
        { title: 'Consolidate Code Environments', description: '168 environments exist with ~45 unused. Remove zero-usage envs and consolidate per-user test envs onto standard shared environments like py39-base.', impact: 'Reduces maintenance and rebuild overhead' },
        { title: 'Establish Project Lifecycle Policy', description: '451 projects include many duplicate SOL_DKU_MONITORING clones and multi-GB test projects. Archive inactive sandboxes and enforce naming/retention standards.', impact: 'Controls storage growth and governance' },
        { title: 'Right-size Compute Capacity', description: '4 cores/8 threads is modest for the observed GenAI and Spark workloads across 72 active users. Review CPU allocation and offload heavy work to K8s.', impact: 'Improves interactive and job performance' },
      ],
    },
    rec_nice_to_have: {
      items: [
        { title: 'Evaluate Govern Integration', description: 'Govern Integration is disabled. Evaluate Dataiku Govern for centralized model validation and compliance as production AI workloads mature.', impact: 'Improves oversight of production AI assets' },
        { title: 'Audit Unused Connections', description: 'Among 97 connections many are single-user test buckets. Review and retire unused connections and standardize shared enterprise connections.', impact: 'Cleaner, more secure connection catalog' },
        { title: 'Review Dev-Mode Plugins', description: 'Roughly 60 of 137 plugins are in dev mode. Promote stable ones to installed versions and remove abandoned experiments to clean the plugin store.', impact: 'More stable, maintainable plugin landscape' },
      ],
    },
    action_plan: {
      headline: 'Seven moves for the next two quarters',
      priorities: [
        { action: 'Run disk-cleanup macros and purge intermediate datasets on top-20 projects; plan /data expansion', timeline: 'Within 30 days', effort: 'medium' },
        { action: 'Configure swap space and validate JVM heap headroom on the DSS host', timeline: 'Next maintenance window', effort: 'low' },
        { action: 'Remove ~45 zero-usage code environments after owner confirmation', timeline: 'Within 30 days', effort: 'low' },
        { action: 'Plan and execute migration of Python 3.6/3.7 environments to 3.11', timeline: 'Q3 2026', effort: 'high' },
        { action: 'Configure SSO or LDAP authentication and phase out local accounts', timeline: 'Q3 2026', effort: 'medium' },
        { action: 'Define and roll out project lifecycle/retention policy; archive duplicate sandbox clones', timeline: 'Q3 2026', effort: 'medium' },
        { action: 'Evaluate Dataiku Govern and audit unused connections and dev-mode plugins', timeline: 'Q4 2026', effort: 'medium' },
      ],
    },
  },
};
