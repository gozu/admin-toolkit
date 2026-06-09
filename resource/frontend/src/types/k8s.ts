// Cluster types
export interface NodeGroup {
  name: string;
  instanceType: string;
  desiredCapacity: number;
  minSize: number;
  maxSize: number;
  volumeSize?: number;
  volumeType?: string;
  spot?: boolean;
  labels?: Record<string, string>;
  taints?: Array<{ key: string; value: string; effect: string }>;
}

export interface Cluster {
  name: string;
  region?: string;
  version?: string;
  networkType?: string;
  vpcCidr?: string;
  subnets?: Record<string, Record<string, { id: string }>>;
  subnetIds?: string[];
  securityGroups?: string[];
  vpcId?: string;
  status?: 'ON' | 'OFF' | 'UNKNOWN';
  uptime?: string;
  server?: string;
  nodeGroups: NodeGroup[];
  lastStartTime?: Date;
  lastStopTime?: Date;
  currentContext?: string;
  clusterName?: string;
  authCommand?: string;
  authApiVersion?: string;
}

export type K8sSeverity = 'critical' | 'high' | 'medium' | 'low' | 'info';
export type K8sRemediationKind = 'kubectl' | 'file-edit' | 'gui-step' | 'doc-link';

export interface K8sRemediation {
  kind: K8sRemediationKind;
  title: string;
  body?: string;
  target?: string | null;
}

export interface K8sFinding {
  id: string;
  rule: string;
  severity: K8sSeverity;
  category: string;
  title: string;
  summary: string;
  evidence: Record<string, unknown>;
  remediation: K8sRemediation[];
  costImpactPerMonth: number | null;
  confidence: 'high' | 'medium' | 'low';
}

export interface K8sNodeTaint {
  key: string;
  value?: string | null;
  effect: string;
}

export interface K8sNodeCondition {
  type: string;
  status: string;
  reason?: string | null;
  message?: string | null;
  lastTransitionTime?: string | null;
}

export interface K8sNodeAddress {
  type: string;
  address: string;
}

export interface K8sNodeInfo {
  kubeletVersion?: string | null;
  kubeProxyVersion?: string | null;
  containerRuntimeVersion?: string | null;
  kernelVersion?: string | null;
  osImage?: string | null;
  operatingSystem?: string | null;
  architecture?: string | null;
}

export interface K8sPodOnNode {
  name: string;
  ns: string;
  phase: string;
  restartCount: number;
  ready: boolean;
  requestedCpuMilli: number;
  requestedMemMib: number;
  realCpuMilli: number | null;
  realMemMib: number | null;
  isSystem: boolean;
  oomKilled?: boolean;
  crashLoopBackOff?: boolean;
  // GPU + DSS identity (forwarded by the backend; identity present only for
  // GPU-requesting DSS execution pods).
  requestedGpu?: number;
  dssProjectKey?: string | null;
  dssObjectType?: string | null;
  dssObjectId?: string | null;
  dssSubmitter?: string | null;
  gpuKeywordsFound?: boolean | null;
}

export interface K8sNodeBreakdown {
  name: string;
  instanceType: string;
  isGpu: boolean;
  ready: boolean;
  podCount: number;
  userPodCount: number;
  allocatableCpu: string | null;
  allocatableMemory: string | null;
  allocatableGpu: string | null;
  cpuUsageMilli: number | null;
  cpuPct: number | null;
  memUsageMib: number | null;
  memPct: number | null;
  hourly: number | null;
  labels: Record<string, string>;
  taints?: K8sNodeTaint[];
  unschedulable?: boolean;
  conditions?: K8sNodeCondition[];
  addresses?: K8sNodeAddress[];
  nodeInfo?: K8sNodeInfo;
  capacity?: {
    cpu?: string | null;
    memory?: string | null;
    ephemeralStorage?: string | null;
    gpu?: string | null;
  };
  createdAt?: number | null;
  selectedLabels?: Record<string, string>;
  pods?: K8sPodOnNode[];
}

export interface K8sPricingStatus {
  ok: boolean;
  source: string;
  region: string;
  error: string | null;
  fetchedAt: number | null;
}

export type K8sClusterHealthErrorClass = 'dns' | 'network' | 'auth' | 'tls' | 'unknown';

export interface K8sClusterHealth {
  id: string;
  ok: boolean;
  errorClass: K8sClusterHealthErrorClass | null;
  errorSummary: string | null;
  errorFull: string | null;
  latencyMs: number;
  kubectlServerVersion: string | null;
}

export interface K8sClusterHealthResult {
  ok: boolean;
  error?: string | null;
  clusters: K8sClusterHealth[];
  durationMs: number;
}

export interface K8sProbeStatus {
  ok: boolean;
  error: string | null;
  rc: number | null;
  stdoutHead: string;
  stderrFull: string;
  durationMs: number;
  itemCount: number | null;
}

export interface K8sCostSnapshot {
  currentHourly: number | null;
  currentMonthly: number | null;
  nodes: Array<{ name: string; instanceType: string; hourly: number | null; isGpu: boolean }>;
}

export interface K8sCluster {
  id: string;
  baseDir?: string | null;
  hasKubeconfig?: boolean;
  kubeconfig?: string | null;
  nodeCount?: number | null;
  podCount?: number | null;
  kubectlVersion?: string | null;
}

export interface K8sInsightsScanResult {
  ok: boolean;
  error?: string | null;
  cluster: K8sCluster;
  probes: Record<string, K8sProbeStatus>;
  findings: K8sFinding[];
  findingsCount: number;
  costSnapshot: K8sCostSnapshot;
  nodeBreakdown: K8sNodeBreakdown[];
  podSummary: {
    total: number;
    byPhase: Record<string, number>;
    byNamespace: Record<string, number>;
    failed: number;
  };
  pricingStatus?: K8sPricingStatus;
  metadata: {
    durationMs: number;
    dipHome: string;
    rulesEvaluated: number;
    rulesAvailable: number;
    kubectlVersion?: unknown;
  };
}

export interface K8sInsightsClustersResult {
  ok: boolean;
  error?: string | null;
  clusters: Array<{
    id: string;
    baseDir?: string;
    hasKubeconfig: boolean;
    kubeconfig: string | null;
    name?: string;
    state?: string | null;
    type?: string | null;
    architecture?: string | null;
    dirFiles?: string[];
  }>;
  unavailable?: Array<{
    id: string;
    state?: string | null;
    type?: string | null;
    hasKubeconfig: boolean;
    baseDir?: string;
    dirFiles?: string[];
  }>;
  durationMs: number;
  totalDiscovered?: number;
  dssRegistryError?: string | null;
}
