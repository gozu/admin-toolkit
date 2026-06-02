import { useMemo } from 'react';
import { useDiag } from '../context/DiagContext';
import { useTableFilter } from '../hooks/useTableFilter';
import type { Cluster, NodeGroup } from '../types';

const EMPTY_ARR: never[] = [];

function calculateClusterHeightScore(cluster: Cluster): number {
  let score = 10; // Base: header + fixed info grid

  // Optional info items
  if (cluster.status === 'ON' && cluster.uptime) score += 1;
  if (cluster.subnetIds?.length) score += 1;
  if (cluster.securityGroups?.length) score += 1;

  // Server section
  if (cluster.server) score += 3;

  // Node pools (biggest height contributor)
  const nodeGroups = cluster.nodeGroups || [];
  if (nodeGroups.length > 0) {
    score += 2; // Header
    for (const ng of nodeGroups) {
      score += 5; // Base pool height
      if (ng.labels && Object.keys(ng.labels).length > 0) score += 1;
      if (ng.taints?.length) score += 1;
    }
  }
  return score;
}

function arrangeClustersByHeightSimilarity(clusters: Cluster[]): Cluster[] {
  if (clusters.length <= 2) return clusters;

  // Score and sort by height descending
  const scored = clusters
    .map((cluster, i) => ({ cluster, score: calculateClusterHeightScore(cluster), i }))
    .sort((a, b) => b.score - a.score);

  // Greedy pairing: match each cluster with closest remaining height
  const result: Cluster[] = [];
  const used = new Set<number>();

  for (let i = 0; i < scored.length; i++) {
    if (used.has(i)) continue;
    result.push(scored[i].cluster);
    used.add(i);

    // Find best partner (closest score)
    let bestJ = -1, bestDiff = Infinity;
    for (let j = i + 1; j < scored.length; j++) {
      if (used.has(j)) continue;
      const diff = Math.abs(scored[i].score - scored[j].score);
      if (diff < bestDiff) { bestDiff = diff; bestJ = j; }
    }
    if (bestJ !== -1) { result.push(scored[bestJ].cluster); used.add(bestJ); }
  }
  return result;
}

export function ClustersTable() {
  const { state } = useDiag();
  const { isVisible } = useTableFilter();
  const { parsedData } = state;
  const clusters = parsedData.clusters ?? EMPTY_ARR;

  const arrangedClusters = useMemo(
    () => arrangeClustersByHeightSimilarity(clusters),
    [clusters]
  );

  if (!isVisible('clusters-table') || clusters.length === 0) {
    return null;
  }

  return (
    <div
      className="bg-[var(--bg-surface)] rounded-xl shadow-md overflow-hidden col-span-full border border-[var(--border-glass)]"
      id="clusters-table"
    >
      <div className="px-4 py-3 border-b border-[var(--border-glass)]">
        <h4 className="text-lg font-semibold text-[var(--text-primary)]">
          Kubernetes Clusters
        </h4>
      </div>

      <div className="p-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
        {arrangedClusters.map((cluster) => (
          <ClusterCard key={cluster.name} cluster={cluster} />
        ))}
      </div>
    </div>
  );
}

function ClusterCard({ cluster }: { cluster: Cluster }) {
  const statusColors: Record<string, string> = {
    ON: 'bg-green-500',
    OFF: 'bg-red-500',
    UNKNOWN: 'bg-gray-500',
  };

  const status = cluster.status || 'UNKNOWN';

  return (
    <div className="border border-[var(--border-glass)] rounded-lg p-4 bg-[var(--bg-elevated)] h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h4 className="text-lg font-semibold text-[var(--text-primary)]">{cluster.name}</h4>
        <span
          className={`${statusColors[status]} text-white px-3 py-1 rounded-full text-xs font-medium`}
        >
          {status}
        </span>
      </div>

      {/* Cluster Info Grid */}
      <div className="grid grid-cols-2 gap-3 mb-4">
        <InfoItem
          label="Kubernetes Version"
          value={cluster.version || 'Unknown'}
          highlight
        />
        <InfoItem label="Region" value={cluster.region || 'Unknown'} />
        <InfoItem label="Networking" value={cluster.networkType || 'Unknown'} />
        <InfoItem label="VPC CIDR" value={cluster.vpcCidr || 'Not specified'} />
        {cluster.status === 'ON' && cluster.uptime && (
          <InfoItem label="Uptime" value={cluster.uptime} highlight />
        )}
        {cluster.subnetIds && cluster.subnetIds.length > 0 && (
          <InfoItem
            label="Subnets"
            value={cluster.subnetIds.join(', ')}
          />
        )}
        {cluster.securityGroups && cluster.securityGroups.length > 0 && (
          <InfoItem
            label="Security Groups"
            value={cluster.securityGroups.join(', ')}
          />
        )}
      </div>

      {/* Server info */}
      {cluster.server && (
        <div className="bg-[var(--bg-surface)] rounded p-3 mb-4 text-sm border border-[var(--border-glass)]">
          <span className="font-bold text-[var(--text-secondary)]">Server:</span>{' '}
          <span className="text-[var(--text-muted)] break-all">{cluster.server}</span>
        </div>
      )}

      {/* Node Pools */}
      {cluster.nodeGroups && cluster.nodeGroups.length > 0 && (
        <div className="flex-1">
          <h5 className="font-semibold text-[var(--text-secondary)] mb-2">Node Pools</h5>
          <div className="space-y-3">
            {cluster.nodeGroups.map((ng, idx) => (
              <NodePoolCard key={idx} nodeGroup={ng} index={idx} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function InfoItem({
  label,
  value,
  highlight = false,
}: {
  label: string;
  value: string;
  highlight?: boolean;
}) {
  return (
    <div className="flex flex-col">
      <span className="text-xs text-[var(--text-muted)] uppercase font-semibold">
        {label}
      </span>
      <span
        className={`text-sm ${highlight ? 'text-[var(--neon-cyan)] font-medium' : 'text-[var(--text-primary)]'}`}
      >
        {value}
      </span>
    </div>
  );
}

function NodePoolCard({
  nodeGroup,
  index,
}: {
  nodeGroup: NodeGroup;
  index: number;
}) {
  return (
    <div className="bg-[var(--bg-surface)] rounded p-3 border border-[var(--border-glass)]">
      <div className="flex items-center justify-between mb-2">
        <h6 className="font-medium text-[var(--text-primary)]">
          {nodeGroup.name || `Pool ${index + 1}`}
        </h6>
        {nodeGroup.spot && (
          <span className="bg-[var(--neon-amber)] text-black px-2 py-0.5 rounded-full text-xs font-semibold">
            Spot
          </span>
        )}
      </div>

      <div className="grid grid-cols-3 sm:grid-cols-6 gap-2 text-xs">
        <div className="flex flex-col">
          <span className="text-[var(--text-muted)] uppercase">Machine Type</span>
          <span className="text-[var(--neon-cyan)] font-medium">
            {nodeGroup.instanceType || 'Unknown'}
          </span>
        </div>
        <div className="flex flex-col">
          <span className="text-[var(--text-muted)] uppercase">Min</span>
          <span className="text-[var(--text-primary)]">{nodeGroup.minSize || 0}</span>
        </div>
        <div className="flex flex-col">
          <span className="text-[var(--text-muted)] uppercase">Desired</span>
          <span className="text-[var(--text-primary)]">
            {nodeGroup.desiredCapacity || 0}
          </span>
        </div>
        <div className="flex flex-col">
          <span className="text-[var(--text-muted)] uppercase">Max</span>
          <span className="text-[var(--text-primary)]">
            {nodeGroup.maxSize || nodeGroup.desiredCapacity || 0}
          </span>
        </div>
        <div className="flex flex-col">
          <span className="text-[var(--text-muted)] uppercase">Disk Type</span>
          <span className="text-[var(--text-primary)]">{nodeGroup.volumeType || 'gp2'}</span>
        </div>
        <div className="flex flex-col">
          <span className="text-[var(--text-muted)] uppercase">Disk Size</span>
          <span className="text-[var(--text-primary)]">
            {nodeGroup.volumeSize ? `${nodeGroup.volumeSize}GB` : 'Default'}
          </span>
        </div>
      </div>

      {/* Labels and Taints */}
      {((nodeGroup.labels && Object.keys(nodeGroup.labels).length > 0) ||
        (nodeGroup.taints && nodeGroup.taints.length > 0)) && (
        <div className="mt-2 text-xs text-[var(--text-secondary)]">
          {nodeGroup.labels && Object.keys(nodeGroup.labels).length > 0 && (
            <div>
              <span className="font-semibold">Labels:</span>{' '}
              {Object.entries(nodeGroup.labels)
                .map(([k, v]) => {
                  const cleanKey = k
                    .replace(/^alpha\.eksctl\.io\//, '')
                    .replace(/^beta\.eksctl\.io\//, '');
                  return `${cleanKey}=${v}`;
                })
                .join(', ')}
            </div>
          )}
          {nodeGroup.taints && nodeGroup.taints.length > 0 && (
            <div>
              <span className="font-semibold">Taints:</span>{' '}
              {nodeGroup.taints
                .map((t) => `${t.key}=${t.value}:${t.effect}`)
                .join(', ')}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

