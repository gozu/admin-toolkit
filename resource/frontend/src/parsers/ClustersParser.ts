import type { Cluster, NodeGroup, ExtractedFiles } from '../types';

interface ClustersResult {
  clusters: Cluster[];
}

interface NodePoolInlinedConfig {
  machineType?: string;
  numNodes?: number;
  minNumNodes?: number;
  maxNumNodes?: number;
  diskSizeGb?: number;
  enableGPU?: boolean;
  useSpotInstances?: boolean;
  labels?: Record<string, string>;
  taints?: Array<{ key: string; value: string; effect: string }>;
  nodeGroupId?: string;
}

interface ActualClusterJSON {
  name?: string;
  architecture?: string;
  params?: {
    config?: {
      k8sVersion?: string;
      privateCluster?: boolean;
      connectionInfo?: {
        inlinedConfig?: {
          region?: string;
        };
      };
      networkingSettings?: {
        inlinedConfig?: {
          subnets?: string[];
          securityGroups?: string[];
          privateNetworking?: boolean;
        };
      };
      nodePools?: Array<{
        inlinedConfig?: NodePoolInlinedConfig;
      }>;
      nodePool?: {
        inlinedConfig?: NodePoolInlinedConfig;
      };
    };
  };
  data?: {
    cluster?: {
      Arn?: string;
      Version?: string;
      Status?: string;
      ResourcesVpcConfig?: {
        SubnetIds?: string[];
        SecurityGroupIds?: string[];
        VpcId?: string;
        EndpointPrivateAccess?: boolean;
        EndpointPublicAccess?: boolean;
      };
      KubernetesNetworkConfig?: {
        ServiceIpv4Cidr?: string;
      };
    };
  };
  containerSettings?: {
    executionConfigsGenericOverrides?: {
      properties?: Array<{ key: string; value: string }>;
    };
  };
}

export class ClustersParser {
  parse(extractedFiles: ExtractedFiles, dsshome: string): ClustersResult {
    const clusterConfigs: Record<string, Cluster> = {};

    const escapedDsshome = dsshome.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

    // New pattern: config/clusters/<name>.json
    const clusterJsonPattern = new RegExp(
      `^${escapedDsshome}config/clusters/([^/]+)\\.json$`
    );

    // Keep kube config pattern for additional info
    const kubeConfigPattern = new RegExp(
      `^${escapedDsshome}clusters/([^/]+)/exec/kube_config$`
    );

    // Keep log patterns for status/uptime
    const startLogPattern = new RegExp(
      `^${escapedDsshome}clusters/([^/]+)/log/start\\.log$`
    );
    const stopLogPattern = new RegExp(
      `^${escapedDsshome}clusters/([^/]+)/log/stop\\.log$`
    );

    // Parse cluster config files and logs
    for (const filename in extractedFiles) {
      // Try new JSON pattern first
      const jsonMatch = filename.match(clusterJsonPattern);
      if (jsonMatch) {
        const clusterName = jsonMatch[1];
        const clusterInfo = this.parseClusterJSON(
          extractedFiles[filename],
          clusterName
        );
        if (clusterInfo) clusterConfigs[clusterName] = clusterInfo;
      }

      const kubeMatch = filename.match(kubeConfigPattern);
      if (kubeMatch) {
        const clusterName = kubeMatch[1];
        const kubeInfo = this.parseKubeConfig(extractedFiles[filename]);
        if (kubeInfo) {
          clusterConfigs[clusterName] = clusterConfigs[clusterName]
            ? { ...clusterConfigs[clusterName], ...kubeInfo }
            : { name: clusterName, nodeGroups: [], ...kubeInfo };
        }
      }

      const startLogMatch = filename.match(startLogPattern);
      if (startLogMatch) {
        const clusterName = startLogMatch[1];
        const startInfo = this.parseStartLog(extractedFiles[filename]);
        if (startInfo) {
          clusterConfigs[clusterName] = clusterConfigs[clusterName]
            ? { ...clusterConfigs[clusterName], ...startInfo }
            : { name: clusterName, nodeGroups: [], ...startInfo };
        }
      }

      const stopLogMatch = filename.match(stopLogPattern);
      if (stopLogMatch) {
        const clusterName = stopLogMatch[1];
        const stopInfo = this.parseStopLog(extractedFiles[filename]);
        if (stopInfo) {
          clusterConfigs[clusterName] = clusterConfigs[clusterName]
            ? { ...clusterConfigs[clusterName], ...stopInfo }
            : { name: clusterName, nodeGroups: [], ...stopInfo };
        }
      }
    }

    // Calculate cluster status after all logs are parsed
    Object.values(clusterConfigs).forEach((cluster) => {
      if (cluster.lastStartTime && cluster.lastStopTime) {
        cluster.status =
          cluster.lastStartTime > cluster.lastStopTime ? 'ON' : 'OFF';
      } else if (cluster.lastStartTime && !cluster.lastStopTime) {
        cluster.status = 'ON';
      } else if (!cluster.lastStartTime && cluster.lastStopTime) {
        cluster.status = 'OFF';
      } else {
        cluster.status = 'UNKNOWN';
      }
    });

    return { clusters: Object.values(clusterConfigs) };
  }

  private parseClusterJSON(
    jsonContent: string,
    clusterName: string
  ): Cluster | null {
    try {
      const data = JSON.parse(jsonContent) as ActualClusterJSON;
      const cluster: Cluster = { name: data.name || clusterName, nodeGroups: [] };

      // Extract region from connectionInfo, or from ARN as fallback
      cluster.region = data.params?.config?.connectionInfo?.inlinedConfig?.region;
      if (!cluster.region && data.data?.cluster?.Arn) {
        // ARN format: arn:aws:eks:REGION:ACCOUNT:cluster/NAME
        const arnParts = data.data.cluster.Arn.split(':');
        if (arnParts.length >= 4) {
          cluster.region = arnParts[3];
        }
      }

      // Extract version - prefer actual running version from data.cluster
      cluster.version = data.data?.cluster?.Version || data.params?.config?.k8sVersion;

      // Extract VPC info from ResourcesVpcConfig
      if (data.data?.cluster?.ResourcesVpcConfig) {
        const vpc = data.data.cluster.ResourcesVpcConfig;
        cluster.securityGroups = vpc.SecurityGroupIds;
        cluster.subnetIds = vpc.SubnetIds;
        cluster.vpcId = vpc.VpcId;
      }

      // Extract service CIDR as vpcCidr
      cluster.vpcCidr = data.data?.cluster?.KubernetesNetworkConfig?.ServiceIpv4Cidr;

      // Determine network type
      const privateCluster = data.params?.config?.privateCluster;
      const privateAccess = data.data?.cluster?.ResourcesVpcConfig?.EndpointPrivateAccess;
      cluster.networkType = privateCluster
        ? 'Fully Private'
        : privateAccess
          ? 'Private'
          : 'Public';

      // Extract node groups from nodePools array + default nodePool
      const nodeGroups: NodeGroup[] = [];

      // Default node pool
      if (data.params?.config?.nodePool?.inlinedConfig) {
        const np = data.params.config.nodePool.inlinedConfig;
        nodeGroups.push({
          name: np.nodeGroupId || 'default',
          instanceType: np.machineType || '',
          desiredCapacity: np.numNodes || 0,
          minSize: np.minNumNodes || 0,
          maxSize: np.maxNumNodes || 0,
          volumeSize: np.diskSizeGb,
          spot: np.useSpotInstances,
          labels: np.labels || {},
          taints: np.taints || [],
        });
      }

      // Additional node pools
      if (data.params?.config?.nodePools) {
        for (const pool of data.params.config.nodePools) {
          if (pool.inlinedConfig) {
            const np = pool.inlinedConfig;
            nodeGroups.push({
              name: np.nodeGroupId || `pool-${nodeGroups.length}`,
              instanceType: np.machineType || '',
              desiredCapacity: np.numNodes || 0,
              minSize: np.minNumNodes || 0,
              maxSize: np.maxNumNodes || 0,
              volumeSize: np.diskSizeGb,
              spot: np.useSpotInstances,
              labels: np.labels || {},
              taints: np.taints || [],
            });
          }
        }
      }

      cluster.nodeGroups = nodeGroups;
      return cluster;
    } catch (error) {
      console.error('Error parsing cluster JSON:', error);
      return null;
    }
  }

  private parseKubeConfig(
    kubeConfigContent: string
  ): Partial<Cluster> | null {
    try {
      const lines = kubeConfigContent.split('\n');
      const kubeInfo: Partial<Cluster> = {};

      let inClusterSection = false;
      let inUserSection = false;

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();

        if (line.startsWith('current-context:')) {
          kubeInfo.currentContext = line.split(':')[1].trim();
        }

        if (line === 'clusters:') {
          inClusterSection = true;
          continue;
        }

        if (line === 'users:') {
          inUserSection = true;
          inClusterSection = false;
          continue;
        }

        if (
          line === 'contexts:' ||
          line === 'kind:' ||
          line === 'preferences:'
        ) {
          inClusterSection = false;
          inUserSection = false;
          continue;
        }

        if (inClusterSection && line.startsWith('server:')) {
          kubeInfo.server = line.split('server:')[1].trim();
        }

        if (inClusterSection && line.startsWith('name:')) {
          const fullName = line.split('name:')[1].trim();
          // Extract just the cluster name before any domain suffix
          kubeInfo.clusterName = fullName.split('.')[0];
        }

        if (inUserSection && line.startsWith('command:')) {
          kubeInfo.authCommand = line.split('command:')[1].trim();
        }

        if (inUserSection && line.startsWith('apiVersion:')) {
          kubeInfo.authApiVersion = line.split('apiVersion:')[1].trim();
        }
      }

      return kubeInfo;
    } catch (error) {
      console.error('Error parsing kube config:', error);
      return null;
    }
  }

  private parseStartLog(logContent: string): Partial<Cluster> | null {
    return this.parseClusterLog(logContent, 'start');
  }

  private parseStopLog(logContent: string): Partial<Cluster> | null {
    return this.parseClusterLog(logContent, 'stop');
  }

  private parseClusterLog(
    content: string,
    type: 'start' | 'stop'
  ): Partial<Cluster> | null {
    const matches = [
      ...content.matchAll(
        new RegExp(
          `Run ${type} on cluster.*?at (\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}\\.\\d{3}\\+\\d{4})`,
          'g'
        )
      ),
    ];
    if (!matches.length) return null;

    const time = new Date(matches[matches.length - 1][1]);
    const result: Partial<Cluster> = {
      [`last${type.charAt(0).toUpperCase() + type.slice(1)}Time`]: time,
    };

    if (type === 'start') {
      const uptimeMs = Date.now() - time.getTime();
      const days = Math.floor(uptimeMs / 86400000);
      const hours = Math.floor((uptimeMs % 86400000) / 3600000);
      result.uptime =
        days > 0
          ? `${days}d ${hours}h`
          : `${hours}h ${Math.floor((uptimeMs % 3600000) / 60000)}m`;
    }

    return result;
  }
}
