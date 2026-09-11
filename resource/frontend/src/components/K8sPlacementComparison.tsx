import { useMemo, useState } from 'react';
import type {
  K8sNodeBreakdown,
  K8sPlacementNode,
  K8sPlacementPod,
  K8sPlacementProjection,
} from '../types';
import './K8sPlacementComparison.css';

type Metric = 'memory' | 'cpu';
type SizingMode = 'rightsized' | 'requests';
type ChartNode = K8sPlacementNode & {
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
const reservation = (p: K8sPlacementPod, metric: Metric) => {
  const value = metric === 'cpu' ? p.reservedCpuMilli : p.reservedMemMib;
  return value <= 0 && usage(p, metric) == null ? null : value;
};
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

export function currentPlacementNodes(
  nodes: K8sNodeBreakdown[],
  mode: SizingMode,
  placements: K8sPlacementNode[] = [],
): ChartNode[] {
  const planned = new Map(placements.flatMap((n) => n.pods).map((p) => [p.key, p]));
  const size = (requested: number, measured: number | null, system: boolean) =>
    mode === 'rightsized' && !system && measured != null
      ? Math.ceil(measured / 0.75)
      : Math.max(requested, measured ?? 0);
  return nodes.map((n) => ({
    id: n.name,
    instanceType: n.instanceType,
    hourly: n.hourly,
    cpuCapacityMilli: parseCapacity(n.allocatableCpu, 'cpu'),
    memoryCapacityMib: parseCapacity(n.allocatableMemory, 'memory'),
    podCount: n.podCount,
    pods: (n.pods ?? []).map((p) => ({
      key: `${p.ns}/${p.name}`,
      name: p.name,
      ns: p.ns,
      sourceNode: n.name,
      statusReason:
        planned.get(`${p.ns}/${p.name}`)?.statusReason ??
        (p.crashLoopBackOff ? 'CrashLoopBackOff' : p.phase),
      isSystem: p.isSystem || p.isDaemonSet === true,
      perNodeService: p.isDaemonSet === true,
      realCpuMilli: p.realCpuMilli,
      realMemMib: p.realMemMib,
      reservedCpuMilli:
        planned.get(`${p.ns}/${p.name}`)?.reservedCpuMilli ??
        size(p.requestedCpuMilli, p.realCpuMilli, p.isSystem || p.isDaemonSet === true),
      reservedMemMib:
        planned.get(`${p.ns}/${p.name}`)?.reservedMemMib ??
        size(p.requestedMemMib, p.realMemMib, p.isSystem || p.isDaemonSet === true),
    })),
  }));
}

export function K8sPlacementComparison({
  nodes,
  projection,
  pricingOk,
  sizingLabel,
  sizingMode,
}: {
  nodes: K8sNodeBreakdown[];
  sizingLabel: string;
  sizingMode: SizingMode;
  projection?: K8sPlacementProjection;
  pricingOk: boolean;
}) {
  const basis = sizingMode === 'rightsized' ? 'used' : 'reserved';
  const [metric, setMetric] = useState<Metric>('memory');
  const [selected, setSelected] = useState<string | null>(null);
  const after = projection?.placementNodes;
  const before = useMemo(
    () => currentPlacementNodes(nodes, sizingMode, after),
    [nodes, sizingMode, after],
  );
  const model = useMemo(() => {
    const all = [...before, ...(after ?? [])];
    const max = Math.max(
      1,
      ...all.map((n) =>
        Math.max(
          capacity(n, metric),
          n.pods.reduce((sum, p) => sum + (reservation(p, metric) ?? 0), 0),
        ),
      ),
    );
    const pod = all.flatMap((n) => n.pods).find((p) => p.key === selected);
    const destinations =
      after?.filter((n) => n.pods.some((p) => p.key === selected)).map((n) => n.id) ?? [];
    const plannedPod = after?.flatMap((n) => n.pods).find((p) => p.key === selected);
    return { max, pod, plannedPod, destinations };
  }, [before, after, metric, selected]);
  const unplaced = projection?.unplaceablePods ?? [];
  const unsized = useMemo(() => {
    const retained = new Set(after?.flatMap((n) => n.retainedForPods ?? []));
    return (projection?.unknownSizingPods ?? []).filter((key) => !retained.has(key));
  }, [after, projection?.unknownSizingPods]);
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
              <div>
                {label}
                <small>{sizingLabel}</small>
              </div>
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
                  basis={basis}
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
            `${unsized.length} ${unsized.length === 1 ? 'pod has' : 'pods have'} neither measured usage nor resource requests. Savings are unavailable for this projection.`}
        </div>
      )}
      <div className="kp-selection" aria-live="polite">
        {model.pod ? (
          <>
            <strong>{model.pod.key}</strong>
            <span>
              {resource(
                model.plannedPod
                  ? reservation(model.plannedPod, metric)
                  : reservation(model.pod, metric),
                metric,
              )}{' '}
              {basis}
              {sizingMode === 'rightsized' ? ' (+33% sizing)' : ''} ·{' '}
              {resource(usage(model.pod, metric), metric)} measured
            </span>
            <span>
              {model.pod.sourceNode} → {model.destinations.join(', ') || 'not assigned'}
              {model.pod.perNodeService ? ' · per-node service' : ''}
            </span>
          </>
        ) : (
          <span>
            Both sides use the selected sizing on one capacity scale · monthly node rental at 730h
          </span>
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
  basis,
  selected,
  onSelect,
}: {
  node: ChartNode;
  metric: Metric;
  max: number;
  pricingOk: boolean;
  basis: 'used' | 'reserved';
  selected: string | null;
  onSelect: (key: string) => void;
}) {
  const cap = capacity(node, metric);
  let offset = 0;
  const segments = node.pods.map((p) => {
    const value = reservation(p, metric);
    const start = offset;
    offset += Math.max(0, value ?? 0);
    return { p, value, start };
  });
  const missingPods = Math.max(0, (node.podCount ?? node.pods.length) - node.pods.length);
  const unknown = segments.filter((s) => s.value == null).length + missingPods;
  const total = offset;
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
            ? `${unknown ? '≥ ' : ''}${resource(total, metric)} ${basis} / ${resource(cap, metric)}`
            : 'Capacity unavailable'}
        </span>
      </div>
      <div className="kp-track-space">
        {cap > 0 ? (
          <div
            className={`kp-track${unknown > 0 ? ' kp-track-unknown' : ''}`}
            style={{ width: `${(cap / max) * 100}%` }}
            aria-label={`${node.id}: ${resource(total, metric)} ${basis}, ${resource(cap, metric)} capacity${unknown ? `; ${unknown} pods unsized` : ''}`}
          >
            {segments
              .filter((s) => s.value != null && s.value > 0)
              .map(({ p, value, start }) => (
                <button
                  type="button"
                  key={p.key}
                  data-pod-key={p.key}
                  className={`kp-pod${selected === p.key ? ' kp-selected' : ''}`}
                  style={{
                    left: `${(start / cap) * 100}%`,
                    width: `${(value! / cap) * 100}%`,
                    background: podColor(p.key, p.isSystem),
                    opacity: selected && selected !== p.key ? 0.4 : 1,
                  }}
                  title={`${p.key}\n${resource(value, metric)} ${basis}\n${resource(usage(p, metric), metric)} measured${p.perNodeService ? '\nPer-node system service' : ''}`}
                  aria-label={`${p.key}: ${resource(value, metric)} ${basis}`}
                  aria-pressed={selected === p.key}
                  onClick={() => onSelect(p.key)}
                />
              ))}
          </div>
        ) : (
          <span className="kp-unavailable">Usage cannot be scaled.</span>
        )}
      </div>
      <div className="kp-row-footer">
        <details className="kp-pod-list">
          <summary>
            {node.podCount ?? node.pods.length} pods{unknown > 0 ? ` · ${unknown} unsized` : ''}
          </summary>
          <div>
            {missingPods > 0 && <p>{missingPods} pod records unavailable.</p>}
            {node.pods.map((p) => (
              <button key={p.key} type="button" onClick={() => onSelect(p.key)}>
                <i style={{ background: podColor(p.key, p.isSystem) }} />
                <span>{p.key}</span>
                <span>
                  {resource(reservation(p, metric), metric)} {basis}
                </span>
              </button>
            ))}
          </div>
        </details>
        {over && (
          <span className="kp-warning">
            {basis === 'used' ? 'Usage sizing' : 'Reservations'} exceed capacity
          </span>
        )}
      </div>
      {!!node.retainedForPods?.length && (
        <details className="kp-retained">
          <summary>Kept at current size · missing pod sizing</summary>
          <div>
            <p>
              All pods stay on this server. Its full rent is included; savings come from other
              servers.
            </p>
            {node.retainedForPods.map((key) => {
              const pod = node.pods.find((p) => p.key === key);
              return (
                <p key={key}>
                  <strong>{key}</strong>
                  {pod?.statusReason ? ` · ${pod.statusReason}` : ''} · no CPU or memory requests
                  and no usage sample.
                </p>
              );
            })}
            <p>
              Restore usage metrics or configure resource requests, then scan again to reassess this
              server.
            </p>
          </div>
        </details>
      )}
    </article>
  );
}
