import { useMemo, useState } from 'react';
import type {
  K8sNodeBreakdown,
  K8sPlacementNode,
  K8sPlacementPod,
  K8sPlacementProjection,
} from '../types';
import './K8sPlacementComparison.css';

type Metric = 'memory' | 'cpu';
type ChartNode = K8sPlacementNode & {
  observedCpu?: number | null;
  observedMemory?: number | null;
  podCount?: number;
};
const COLORS = ['#62c7bf', '#83aff0', '#b59ae1', '#eab576', '#dfcf7d', '#8ecb98', '#db96ba'];
export function podColor(key: string, system: boolean): string {
  if (system) return '#8a83ae';
  let hash = 0;
  for (const char of key) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return COLORS[hash % COLORS.length];
}
export function parseCapacity(value: string | null | undefined, metric: Metric): number {
  if (!value) return 0;
  const match = value.match(/^([\d.]+)([a-zA-Z]*)$/);
  if (!match) return 0;
  const n = Number(match[1]);
  const units: Record<string, number> =
    metric === 'cpu'
      ? { '': 1000, m: 1, u: 0.001, n: 0.000001 }
      : {
          '': 1 / 1048576,
          Ki: 1 / 1024,
          Mi: 1,
          Gi: 1024,
          Ti: 1048576,
          K: 1000 / 1048576,
          M: 1000000 / 1048576,
          G: 1e9 / 1048576,
          T: 1e12 / 1048576,
        };
  return Number.isFinite(n) ? n * (units[match[2]] ?? 0) : 0;
}
const usage = (p: K8sPlacementPod, metric: Metric) =>
  metric === 'cpu' ? p.realCpuMilli : p.realMemMib;
const reservation = (p: K8sPlacementPod, metric: Metric) =>
  metric === 'cpu' ? p.reservedCpuMilli : p.reservedMemMib;
const capacity = (n: ChartNode, metric: Metric) =>
  metric === 'cpu' ? n.cpuCapacityMilli : n.memoryCapacityMib;
const resource = (value: number | null | undefined, metric: Metric) =>
  value == null
    ? 'unknown'
    : metric === 'cpu'
      ? `${Number(value.toFixed(3))}m`
      : `${Number(value.toFixed(3))} MiB`;
export const monthlyRent = (hourly: number | null | undefined) =>
  hourly == null || !Number.isFinite(hourly)
    ? 'Price unavailable'
    : `$${(hourly * 730).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}/mo`;

export function currentPlacementNodes(nodes: K8sNodeBreakdown[]): ChartNode[] {
  return nodes.map((n) => ({
    id: n.name,
    instanceType: n.instanceType,
    hourly: n.hourly,
    cpuCapacityMilli: parseCapacity(n.allocatableCpu, 'cpu'),
    memoryCapacityMib: parseCapacity(n.allocatableMemory, 'memory'),
    observedCpu: n.cpuUsageMilli,
    observedMemory: n.memUsageMib,
    podCount: n.podCount,
    pods: (n.pods ?? []).map((p) => ({
      key: `${p.ns}/${p.name}`,
      name: p.name,
      ns: p.ns,
      sourceNode: n.name,
      isSystem: p.isSystem || p.isDaemonSet === true,
      perNodeService: p.isDaemonSet === true,
      realCpuMilli: p.realCpuMilli,
      realMemMib: p.realMemMib,
      reservedCpuMilli: p.requestedCpuMilli,
      reservedMemMib: p.requestedMemMib,
    })),
  }));
}

export function K8sPlacementComparison({
  nodes,
  projection,
  pricingOk,
}: {
  nodes: K8sNodeBreakdown[];
  projection?: K8sPlacementProjection;
  pricingOk: boolean;
}) {
  const [metric, setMetric] = useState<Metric>('memory');
  const [selected, setSelected] = useState<string | null>(null);
  const before = useMemo(() => currentPlacementNodes(nodes), [nodes]);
  const after = projection?.placementNodes;
  const model = useMemo(() => {
    const all = [...before, ...(after ?? [])];
    const max = Math.max(
      1,
      ...all.map((n) =>
        Math.max(
          capacity(n, metric),
          n.pods.reduce((v, p) => v + (usage(p, metric) ?? 0), 0),
          n.pods.reduce((v, p) => v + reservation(p, metric), 0),
          (metric === 'cpu' ? (n as ChartNode).observedCpu : (n as ChartNode).observedMemory) ?? 0,
        ),
      ),
    );
    const pod = all.flatMap((n) => n.pods).find((p) => p.key === selected);
    const destinations =
      after?.filter((n) => n.pods.some((p) => p.key === selected)).map((n) => n.id) ?? [];
    return { max, pod, destinations };
  }, [before, after, metric, selected]);
  const unplaced = projection?.unplaceablePods ?? [];
  const unsized = projection?.unknownSizingPods ?? [];
  return (
    <div className="k8s-placement" aria-label="Pod placement comparison">
      <div className="kp-toolbar">
        <div className="kp-legend">
          <span>
            <i style={{ background: COLORS[0] }} />
            User pods
          </span>
          <span>
            <i style={{ background: '#8a83ae' }} />
            System pods
          </span>
          <span>
            <i className="kp-unattributed" />
            Unattributed usage
          </span>
          <span>
            <i className="kp-reserved-key" />
            Reserved
          </span>
          <span>
            <i className="kp-free" />
            Available capacity
          </span>
        </div>
        <div className="kp-toggle" role="group" aria-label="Capacity metric">
          {(['memory', 'cpu'] as const).map((m) => (
            <button type="button" key={m} aria-pressed={metric === m} onClick={() => setMetric(m)}>
              {m === 'cpu' ? 'CPU' : 'Memory'}
            </button>
          ))}
        </div>
      </div>
      <div className="kp-columns">
        {(
          [
            { label: 'Current', rows: before },
            { label: 'Proposed', rows: after },
          ] as const
        ).map(({ label, rows }) => (
          <section key={label} aria-label={`${label} servers`}>
            <h4 className="kp-heading">
              {label}
              <span>{rows == null ? '' : `${rows.length} nodes`}</span>
            </h4>
            {rows == null ? (
              <div className="kp-unavailable">Run a new audit to calculate pod destinations.</div>
            ) : rows.length === 0 ? (
              <div className="kp-unavailable">
                {label === 'Current' ? 'No node data.' : 'No servers in this projection.'}
              </div>
            ) : (
              rows.map((n) => (
                <ServerRow
                  key={n.id}
                  node={n}
                  metric={metric}
                  max={model.max}
                  pricingOk={pricingOk}
                  proposed={label === 'Proposed'}
                  selected={selected}
                  onSelect={(key) => setSelected((old) => (old === key ? null : key))}
                />
              ))
            )}
          </section>
        ))}
      </div>
      {(unplaced.length > 0 || unsized.length > 0) && (
        <div className="kp-warning">
          Incomplete projection:{' '}
          {unplaced.length > 0 && `${unplaced.length} pods could not be placed. `}
          {unsized.length > 0 &&
            `${unsized.length} pods have neither measured usage nor resource requests.`}
        </div>
      )}
      <div className="kp-selection" aria-live="polite">
        {model.pod ? (
          <>
            <strong>{model.pod.key}</strong>
            <span>
              {resource(model.pod.realCpuMilli, 'cpu')} CPU ·{' '}
              {resource(model.pod.realMemMib, 'memory')} memory
            </span>
            <span>
              {model.pod.sourceNode} → {model.destinations.join(', ') || 'not assigned'}
              {model.pod.perNodeService ? ' · per-node service' : ''}
            </span>
          </>
        ) : (
          <span>Filled blocks: measured usage · outline: reservations · common capacity scale · monthly rental at 730h</span>
        )}
      </div>
    </div>
  );
}

function ServerRow({
  node,
  metric,
  max,
  pricingOk,
  proposed,
  selected,
  onSelect,
}: {
  node: ChartNode;
  metric: Metric;
  max: number;
  pricingOk: boolean;
  proposed: boolean;
  selected: string | null;
  onSelect: (key: string) => void;
}) {
  const cap = capacity(node, metric);
  let offset = 0;
  const segments = node.pods.map((p) => {
    const value = usage(p, metric);
    const start = offset;
    offset += Math.max(0, value ?? 0);
    return { p, value, start };
  });
  const missingPods = Math.max(0, (node.podCount ?? node.pods.length) - node.pods.length);
  const unknown = segments.filter((s) => s.value == null).length + missingPods;
  const observed = metric === 'cpu' ? node.observedCpu : node.observedMemory;
  const unattributed = observed == null ? 0 : Math.max(0, observed - offset);
  const total = offset + unattributed;
  const reserved = node.pods.reduce((sum, p) => sum + reservation(p, metric), 0);
  // Under-requested pods still consume space. Avoid calling their measured use
  // available, and don't claim availability when inventory/measurements are missing.
  const occupied = node.pods.reduce((sum, p) => sum + Math.max(reservation(p, metric), usage(p, metric) ?? 0), 0) + unattributed;
  const available = unknown > 0 || cap <= 0 ? null : Math.max(0, cap - occupied);
  const over = cap > 0 && total > cap;
  return (
    <article className="kp-server" data-node={node.id}>
      <div className="kp-server-title">
        <strong title={node.id}>{node.id}</strong>
        <span className="kp-price">{monthlyRent(pricingOk ? node.hourly : null)}</span>
      </div>
      <div className="kp-server-meta">
        <span>{node.instanceType}</span>
        <span>
          {cap > 0
            ? `${resource(cap, metric)} capacity`
            : 'Capacity unavailable'}
        </span>
      </div>
      <div className="kp-resource-totals" aria-label={`${metric === 'cpu' ? 'CPU' : 'Memory'} allocation`}>
        <span>Used <b>{unknown > 0 ? '≥ ' : ''}{resource(total, metric)}</b></span>
        <span>Reserved <b>{missingPods > 0 ? '≥ ' : ''}{resource(reserved, metric)}</b></span>
        <span title="Capacity remaining after each pod's reservation or measured use, whichever is higher, plus unattributed node usage. Placement constraints may further restrict it.">Available <b>{resource(available, metric)}</b></span>
      </div>
      <div className="kp-track-space">
        {cap > 0 ? (
          <div
            className={`kp-track${unknown > 0 ? ' kp-track-unknown' : ''}`}
            style={{ width: `${(cap / max) * 100}%` }}
            aria-label={`${node.id}: ${resource(total, metric)} measured, ${resource(cap, metric)} capacity${unknown ? `; ${unknown} pods unmeasured` : ''}`}
          >
            {segments
              .filter((s) => s.value != null && s.value > 0)
              .map(({ p, value, start }) => (
                <button
                  type="button"
                  key={p.key}
                  className={`kp-pod${selected === p.key ? ' kp-selected' : ''}`}
                  style={{
                    left: `${(start / cap) * 100}%`,
                    width: `${(value! / cap) * 100}%`,
                    background: podColor(p.key, p.isSystem),
                    opacity: selected && selected !== p.key ? 0.4 : 1,
                  }}
                  title={`${p.key}\n${resource(p.realCpuMilli, 'cpu')} CPU · ${resource(p.realMemMib, 'memory')} memory${p.perNodeService ? '\nPer-node service; proposed copies use this observed footprint' : ''}`}
                  aria-label={`${p.key}: ${resource(value, metric)}`}
                  aria-pressed={selected === p.key}
                  onClick={() => onSelect(p.key)}
                />
              ))}
            {unattributed > 0 && (
              <span
                className="kp-node-usage kp-unattributed"
                title="Node usage not attributed to measured pods"
                style={{
                  left: `${(offset / cap) * 100}%`,
                  width: `${(unattributed / cap) * 100}%`,
                }}
              />
            )}
            {reserved > 0 && (
              <span
                className="kp-reservation-outline"
                style={{ width: `${(reserved / cap) * 100}%` }}
                role="img"
                aria-label={`${node.id}: ${resource(reserved, metric)} reserved`}
              />
            )}
          </div>
        ) : (
          <span className="kp-unavailable">Usage cannot be scaled.</span>
        )}
      </div>
      {proposed && <SizeReason node={node} />}
      <div className="kp-row-footer">
        <details className="kp-pod-list">
          <summary>
            {node.podCount ?? node.pods.length} pods{unknown > 0 ? ` · ${unknown} unmeasured` : ''}
          </summary>
          <div>
            {missingPods > 0 && <p>{missingPods} pod records unavailable.</p>}
            {node.pods.map((p) => (
              <button key={p.key} type="button" onClick={() => onSelect(p.key)}>
                <i style={{ background: podColor(p.key, p.isSystem) }} />
                <span>{p.key}</span>
                <span>{resource(usage(p, metric), metric)} used · {resource(reservation(p, metric), metric)} reserved</span>
              </button>
            ))}
          </div>
        </details>
        {over && <span className="kp-warning">Usage exceeds capacity</span>}
        {reserved > cap && cap > 0 && <span className="kp-warning">Reservations exceed capacity</span>}
      </div>
    </article>
  );
}

function SizeReason({ node }: { node: ChartNode }) {
  const checks = node.sizeChecks;
  if (checks == null) return <div className="kp-size-note">Refresh audit for server sizing details.</div>;
  if (checks.length === 0) return <div className="kp-size-note">No smaller size evaluated in this pool.</div>;
  const smallerFit = checks.find((check) => check.blockers.length === 0);
  const closest = checks[0];
  const resourceNames = closest.blockers.filter((b) => 'required' in b).map((b) =>
    b.kind === 'memory' ? 'Memory' : b.kind.toUpperCase(),
  );
  const summary = smallerFit
    ? `These pods also fit ${smallerFit.instanceType}; a smaller server may suffice.`
    : resourceNames.length > 0
      ? `${resourceNames.join(' + ')} reservations exceed ${closest.instanceType} capacity.`
      : `Pod placement restrictions block ${closest.instanceType}.`;
  return (
    <details className="kp-size-reason">
      <summary>{summary}</summary>
      <div>
        <p>Same pods and per-node services, on smaller sizes in this pool:</p>
        {checks.map((check) => (
          <div className="kp-size-check" key={check.instanceType}>
            <strong>{check.instanceType}</strong>
            {check.blockers.length === 0 ? <span>Fits the checked resources and placement rules.</span> : check.blockers.map((blocker) => (
              <span key={blocker.kind}>
                {'required' in blocker
                  ? `${blocker.kind === 'memory' ? 'Memory' : blocker.kind.toUpperCase()}: ${blocker.kind === 'gpu' ? blocker.required : resource(blocker.required, blocker.kind)} reserved; ${blocker.kind === 'gpu' ? blocker.capacity : resource(blocker.capacity, blocker.kind)} capacity.`
                  : `${blocker.kind === 'selector' ? 'Required node labels do not match' : 'Node scheduling restrictions are not permitted by'}: ${blocker.pods.join(', ')}.`}
              </span>
            ))}
          </div>
        ))}
        <p>Reservations follow the selected sizing mode. This checks replacing this server; rearranging pods across the fleet may give a cheaper result.</p>
      </div>
    </details>
  );
}
