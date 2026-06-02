import { useMemo } from 'react';
import { pctTone, ratioTone, TONE_TEXT } from '../utils/k8sTone';

interface Props {
  evidence: Record<string, unknown>;
}

type Tone = 'red' | 'yellow' | 'green' | null;

interface FieldSpec {
  label: string;
  format: (v: unknown) => string;
  tone?: (v: unknown, ev: Record<string, unknown>) => Tone;
  group: 'identifier' | 'scalar';
}

// Identifier fields render first (always shown at the top of the panel).
// Scalar fields render in their declaration order below identifiers.
// Anything not in this map falls through to the generic "other" renderer.
const FIELDS: Record<string, FieldSpec> = {
  // ---- identifiers ----
  node:           { label: 'Node',          format: asString, group: 'identifier' },
  pod:            { label: 'Pod',           format: asString, group: 'identifier' },
  container:      { label: 'Container',     format: asString, group: 'identifier' },
  controller:     { label: 'Controller',    format: asString, group: 'identifier' },
  daemonset:      { label: 'DaemonSet',     format: asString, group: 'identifier' },
  deployment:     { label: 'Deployment',    format: asString, group: 'identifier' },
  pdb:            { label: 'PDB',           format: asString, group: 'identifier' },
  configName:     { label: 'Config',        format: asString, group: 'identifier' },
  orphanConfigName: { label: 'Orphan config', format: asString, group: 'identifier' },
  instanceType:   { label: 'Instance type', format: asString, group: 'identifier' },
  image:          { label: 'Image',         format: asString, group: 'identifier' },
  path:           { label: 'Path',          format: asString, group: 'identifier' },
  kubernetesNamespace: { label: 'Namespace', format: asString, group: 'identifier' },
  reason:         { label: 'Reason',        format: asString, group: 'identifier' },
  status:         { label: 'Status',        format: asString, group: 'identifier' },
  errorClass:     { label: 'Error class',   format: asString, group: 'identifier' },

  // ---- usage / utilization ----
  cpuPct:         { label: 'CPU usage',     format: (v) => `${asNum(v)}%`, tone: (v) => pctTone(v), group: 'scalar' },
  memPct:         { label: 'Memory usage',  format: (v) => `${asNum(v)}%`, tone: (v) => pctTone(v), group: 'scalar' },
  cpuPctOfRequest: { label: 'CPU % of request', format: (v) => `${(asNum(v) * 100).toFixed(1)}%`, group: 'scalar' },
  memPctOfRequest: { label: 'Memory % of request', format: (v) => `${(asNum(v) * 100).toFixed(1)}%`, group: 'scalar' },

  // ---- requests / limits / actual ----
  requestedCpuMilli: { label: 'CPU requested', format: (v) => `${asNum(v)}m`, group: 'scalar' },
  cpuRequestMilli:   { label: 'CPU request',   format: (v) => `${asNum(v)}m`, group: 'scalar' },
  cpuRequestedMilli: { label: 'CPU requested', format: (v) => `${asNum(v)}m`, group: 'scalar' },
  realCpuMilli:      { label: 'CPU actual',    format: (v) => `${asNum(v)}m`,
                       tone: (v, ev) => ratioTone(v, ev.requestedCpuMilli ?? ev.cpuRequestedMilli ?? ev.cpuRequestMilli), group: 'scalar' },
  cpuUsageMilli:     { label: 'CPU actual',    format: (v) => `${asNum(v)}m`,
                       tone: (v, ev) => ratioTone(v, ev.requestedCpuMilli ?? ev.cpuRequestedMilli ?? ev.cpuRequestMilli), group: 'scalar' },
  recommendedCpuMilli: { label: 'CPU recommended', format: (v) => `${asNum(v)}m`, group: 'scalar' },

  requestedMemMib:     { label: 'Memory requested', format: (v) => `${asNum(v)}MiB`, group: 'scalar' },
  memRequestMib:       { label: 'Memory request',   format: (v) => `${asNum(v)}MiB`, group: 'scalar' },
  memRequestedMib:     { label: 'Memory requested', format: (v) => `${asNum(v)}MiB`, group: 'scalar' },
  realMemMib:          { label: 'Memory actual',    format: (v) => `${asNum(v)}MiB`,
                         tone: (v, ev) => ratioTone(v, ev.requestedMemMib ?? ev.memRequestedMib ?? ev.memRequestMib), group: 'scalar' },
  memUsageMib:         { label: 'Memory actual',    format: (v) => `${asNum(v)}MiB`,
                         tone: (v, ev) => ratioTone(v, ev.requestedMemMib ?? ev.memRequestedMib ?? ev.memRequestMib), group: 'scalar' },
  p95UsageMib:         { label: 'Memory p95',       format: (v) => `${asNum(v)}MiB`, group: 'scalar' },
  recommendedMemMib:   { label: 'Memory recommended', format: (v) => `${asNum(v)}MiB`, group: 'scalar' },
  recommendedMemRequestMB: { label: 'Memory recommended', format: (v) => `${asNum(v)}MB`, group: 'scalar' },
  templateMemMib:      { label: 'Template memory',  format: (v) => `${asNum(v)}MiB`, group: 'scalar' },
  configMemMib:        { label: 'Config memory',    format: (v) => `${asNum(v)}MiB`, group: 'scalar' },
  memRequestMB:        { label: 'Config memory req', format: (v) => `${asNum(v)}MB`, group: 'scalar' },
  memLimitMB:          { label: 'Config memory lim', format: (v) => `${asNum(v)}MB`, group: 'scalar' },

  allocatableCpuMilli: { label: 'CPU allocatable',    format: (v) => `${asNum(v)}m`,   group: 'scalar' },
  cpuAllocatableMilli: { label: 'CPU allocatable',    format: (v) => `${asNum(v)}m`,   group: 'scalar' },
  allocatableMemMib:   { label: 'Memory allocatable', format: (v) => `${asNum(v)}MiB`, group: 'scalar' },
  memAllocatableMib:   { label: 'Memory allocatable', format: (v) => `${asNum(v)}MiB`, group: 'scalar' },

  gpuUsage:       { label: 'GPUs in use',    format: (v) => String(asNum(v)),   group: 'scalar' },
  gpuRequest:     { label: 'GPUs requested', format: (v) => String(asNum(v)),   group: 'scalar' },
  requestedGpu:   { label: 'GPUs requested', format: (v) => String(asNum(v)),   group: 'scalar' },
  nodeAllocatableGpu: { label: 'GPUs allocatable', format: (v) => String(asNum(v)), group: 'scalar' },
  cpuPodsOnGpu:   { label: 'CPU-only pods on GPU node', format: (v) => String(asNum(v)), tone: (v) => (asNum(v) > 0 ? 'yellow' : null), group: 'scalar' },

  // ---- counts / pods ----
  podCount:           { label: 'Pods',                format: (v) => String(asNum(v)), group: 'scalar' },
  totalPods:          { label: 'Total pods',          format: (v) => String(asNum(v)), group: 'scalar' },
  thisDssPodCount:    { label: 'This-DSS pods',       format: (v) => String(asNum(v)), group: 'scalar' },
  foreignPodCount:    { label: 'Foreign pods',        format: (v) => String(asNum(v)), group: 'scalar' },
  hostedPods:         { label: 'Hosted pods',         format: (v) => String(asNum(v)), group: 'scalar' },
  blockingPods:       { label: 'Blocking pods',       format: (v) => String(asNum(v)), group: 'scalar' },
  crashingPods:       { label: 'Crashing pods',       format: (v) => String(asNum(v)), tone: (v) => (asNum(v) > 0 ? 'red' : null), group: 'scalar' },
  daemonSetPodCount:  { label: 'DaemonSet pods',      format: (v) => String(asNum(v)), group: 'scalar' },
  controllerCount:    { label: 'Controllers',         format: (v) => String(asNum(v)), group: 'scalar' },
  replicaCount:       { label: 'Replicas',            format: (v) => String(asNum(v)), group: 'scalar' },
  currentNumberScheduled: { label: 'Scheduled', format: (v) => String(asNum(v)), group: 'scalar' },
  desiredNumberScheduled: { label: 'Desired',   format: (v) => String(asNum(v)), group: 'scalar' },
  restartCount:       { label: 'Restarts',            format: (v) => String(asNum(v)), tone: (v) => (asNum(v) > 0 ? 'red' : null), group: 'scalar' },
  nodeCount:          { label: 'Nodes',               format: (v) => String(asNum(v)), group: 'scalar' },
  cpuNodeCount:       { label: 'CPU nodes',           format: (v) => String(asNum(v)), group: 'scalar' },
  gpuNodeCount:       { label: 'GPU nodes',           format: (v) => String(asNum(v)), group: 'scalar' },
  failedKubectlProbes: { label: 'Failed kubectl probes', format: (v) => String(asNum(v)), tone: (v) => (asNum(v) > 0 ? 'red' : null), group: 'scalar' },
  totalKubectlProbes:  { label: 'Total kubectl probes',  format: (v) => String(asNum(v)), group: 'scalar' },
  tolerationsCount:    { label: 'Tolerations',           format: (v) => String(asNum(v)), group: 'scalar' },
  deploymentsSeen:     { label: 'Deployments seen',      format: (v) => String(asNum(v)), group: 'scalar' },

  // ---- timing ----
  ageHours:        { label: 'Age',         format: (v) => `${asNum(v).toFixed(1)}h`, group: 'scalar' },
  ageMinutes:      { label: 'Age',         format: (v) => `${asNum(v).toFixed(0)}m`, group: 'scalar' },
  minutes:         { label: 'Duration',    format: (v) => `${asNum(v).toFixed(0)}m`, group: 'scalar' },
  sinceMinutes:    { label: 'Since',       format: (v) => `${asNum(v).toFixed(0)}m`, group: 'scalar' },
  finishedAt:      { label: 'Finished at', format: asString, group: 'scalar' },
  deletionTimestamp: { label: 'Deleted at', format: asString, group: 'scalar' },

  // ---- cost ----
  currentHourly:  { label: 'Current $/hr', format: (v) => `$${asNum(v).toFixed(2)}`, group: 'scalar' },
  floorHourly:    { label: 'Floor $/hr',   format: (v) => `$${asNum(v).toFixed(2)}`, group: 'scalar' },
  savingsHourly:  { label: 'Savings $/hr', format: (v) => `$${asNum(v).toFixed(2)}`, tone: () => 'green', group: 'scalar' },
  currentMonthly: { label: 'Current $/mo', format: (v) => `$${asNum(v).toFixed(0)}`, group: 'scalar' },
  floorMonthly:   { label: 'Floor $/mo',   format: (v) => `$${asNum(v).toFixed(0)}`, group: 'scalar' },
  savingsMonthly: { label: 'Savings $/mo', format: (v) => `$${asNum(v).toFixed(0)}`, tone: () => 'green', group: 'scalar' },
  floorCpuSavingsMonthly: { label: 'CPU consolidation $/mo', format: (v) => `$${asNum(v).toFixed(0)}`, tone: () => 'green', group: 'scalar' },
  floorGpuSavingsMonthly: { label: 'Idle GPU node $/mo', format: (v) => `$${asNum(v).toFixed(0)}`, tone: () => 'green', group: 'scalar' },

  // ---- size / other scalars ----
  sizeBytes:       { label: 'Size',           format: (v) => formatBytes(asNum(v)),       group: 'scalar' },
  deltaPct:        { label: 'Delta',          format: (v) => `${(asNum(v) * 100).toFixed(1)}%`, group: 'scalar' },
  maxUnavailable:  { label: 'maxUnavailable', format: asString, group: 'scalar' },
  minAvailable:    { label: 'minAvailable',   format: asString, group: 'scalar' },
  message:         { label: 'Message',        format: asString, group: 'scalar' },
  snippet:         { label: 'Snippet',        format: asString, group: 'scalar' },
  sampleProbe:     { label: 'Sample probe',   format: asString, group: 'scalar' },
};

const IDENTIFIER_ORDER: string[] = [
  'node', 'pod', 'container', 'controller', 'daemonset', 'deployment', 'pdb',
  'configName', 'orphanConfigName', 'instanceType', 'image', 'kubernetesNamespace',
  'path', 'reason', 'status', 'errorClass',
];

const SCALAR_ORDER: string[] = Object.entries(FIELDS)
  .filter(([, spec]) => spec.group === 'scalar')
  .map(([k]) => k);

export function K8sFindingEvidence({ evidence }: Props) {
  const parts = useMemo(() => splitEvidence(evidence), [evidence]);

  if (!evidence || Object.keys(evidence).length === 0) return null;

  return (
    <div className="space-y-3">
      {parts.identifiers.length > 0 && (
        <KeyValueRows entries={parts.identifiers} />
      )}
      {parts.scalars.length > 0 && (
        <KeyValueRows entries={parts.scalars} />
      )}
      {parts.listOfDicts.map(([key, value]) => (
        <ListOfDictsTable key={key} fieldKey={key} rows={value} />
      ))}
      {parts.dictOfScalars.map(([key, value]) => (
        <DictOfScalarsTable key={key} fieldKey={key} entries={Object.entries(value)} />
      ))}
      {parts.listOfStrings.map(([key, value]) => (
        <ListOfStrings key={key} fieldKey={key} items={value} />
      ))}
      {parts.other.length > 0 && (
        <OtherFields entries={parts.other} />
      )}
    </div>
  );
}

interface ToneEntry {
  key: string;
  label: string;
  display: string;
  tone: Tone;
}

function KeyValueRows({ entries }: { entries: ToneEntry[] }) {
  return (
    <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 text-xs font-mono">
      {entries.map((e) => (
        <div key={e.key} className="flex gap-2 min-w-0">
          <dt className="text-[var(--text-muted)] whitespace-nowrap">{e.label}</dt>
          <dd className={`min-w-0 truncate ${e.tone ? TONE_TEXT[e.tone] : 'text-[var(--text-primary)]'}`} title={e.display}>
            {e.display}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function ListOfDictsTable({ fieldKey, rows }: { fieldKey: string; rows: Array<Record<string, unknown>> }) {
  if (!rows.length) return null;
  const cols = collectColumns(rows);
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] mb-1">
        {labelize(fieldKey)} <span className="text-[var(--text-muted)]">({rows.length})</span>
      </div>
      <div className="border border-white/5 rounded overflow-hidden">
        <div className="grid gap-2 px-2 py-1 text-[10px] uppercase text-[var(--text-muted)] bg-white/[0.02]" style={gridTemplate(cols.length)}>
          {cols.map((c) => <div key={c}>{labelize(c)}</div>)}
        </div>
        <div className="divide-y divide-white/5">
          {rows.slice(0, 50).map((r, i) => (
            <div key={i} className="grid gap-2 px-2 py-1 text-xs font-mono" style={gridTemplate(cols.length)}>
              {cols.map((c) => (
                <span key={c} className="truncate" title={String(r[c] ?? '')}>
                  {String(r[c] ?? '—')}
                </span>
              ))}
            </div>
          ))}
        </div>
        {rows.length > 50 && (
          <div className="px-2 py-1 text-[10px] text-[var(--text-muted)] font-mono">
            …+{rows.length - 50} more
          </div>
        )}
      </div>
    </div>
  );
}

function DictOfScalarsTable({ fieldKey, entries }: { fieldKey: string; entries: Array<[string, unknown]> }) {
  if (!entries.length) return null;
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] mb-1">
        {labelize(fieldKey)}
      </div>
      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 text-xs font-mono">
        {entries.map(([k, v]) => (
          <div key={k} className="flex gap-2 min-w-0">
            <dt className="text-[var(--text-muted)] truncate" title={k}>{k}</dt>
            <dd className="text-[var(--text-primary)] ml-auto">{String(v ?? '—')}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function ListOfStrings({ fieldKey, items }: { fieldKey: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] mb-1">
        {labelize(fieldKey)} <span className="text-[var(--text-muted)]">({items.length})</span>
      </div>
      <ul className="text-xs font-mono space-y-0.5">
        {items.slice(0, 20).map((s, i) => (
          <li key={i} className="text-[var(--text-secondary)]">{s}</li>
        ))}
        {items.length > 20 && (
          <li className="text-[10px] text-[var(--text-muted)]">…+{items.length - 20} more</li>
        )}
      </ul>
    </div>
  );
}

function OtherFields({ entries }: { entries: Array<[string, unknown]> }) {
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-[var(--text-muted)] font-mono">
        {entries.length} additional field{entries.length === 1 ? '' : 's'}
      </summary>
      <dl className="mt-1 grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 font-mono">
        {entries.map(([k, v]) => (
          <div key={k} className="flex gap-2 min-w-0">
            <dt className="text-[var(--text-muted)] whitespace-nowrap">{labelize(k)}</dt>
            <dd className="text-[var(--text-primary)] truncate" title={summarizeValue(v)}>
              {summarizeValue(v)}
            </dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

interface EvidenceParts {
  identifiers: ToneEntry[];
  scalars: ToneEntry[];
  listOfDicts: Array<[string, Array<Record<string, unknown>>]>;
  dictOfScalars: Array<[string, Record<string, unknown>]>;
  listOfStrings: Array<[string, string[]]>;
  other: Array<[string, unknown]>;
}

function splitEvidence(evidence: Record<string, unknown>): EvidenceParts {
  const result: EvidenceParts = {
    identifiers: [], scalars: [], listOfDicts: [], dictOfScalars: [], listOfStrings: [], other: [],
  };
  const known = new Set<string>();

  for (const key of IDENTIFIER_ORDER) {
    if (key in evidence && evidence[key] != null && evidence[key] !== '') {
      const spec = FIELDS[key];
      result.identifiers.push({
        key, label: spec.label,
        display: spec.format(evidence[key]),
        tone: spec.tone ? spec.tone(evidence[key], evidence) : null,
      });
      known.add(key);
    }
  }

  for (const key of SCALAR_ORDER) {
    if (key in evidence && evidence[key] != null && evidence[key] !== '') {
      const spec = FIELDS[key];
      result.scalars.push({
        key, label: spec.label,
        display: spec.format(evidence[key]),
        tone: spec.tone ? spec.tone(evidence[key], evidence) : null,
      });
      known.add(key);
    }
  }

  for (const [key, value] of Object.entries(evidence)) {
    if (known.has(key)) continue;
    if (value == null) continue;
    if (Array.isArray(value)) {
      if (value.length === 0) continue;
      if (typeof value[0] === 'string') {
        result.listOfStrings.push([key, value as string[]]);
        continue;
      }
      if (typeof value[0] === 'object' && value[0] !== null) {
        result.listOfDicts.push([key, value as Array<Record<string, unknown>>]);
        continue;
      }
      result.listOfStrings.push([key, (value as unknown[]).map((v) => String(v))]);
      continue;
    }
    if (typeof value === 'object') {
      const dict = value as Record<string, unknown>;
      const allScalar = Object.values(dict).every((v) => typeof v !== 'object' || v === null);
      if (allScalar) {
        result.dictOfScalars.push([key, dict]);
        continue;
      }
      result.other.push([key, value]);
      continue;
    }
    result.other.push([key, value]);
  }
  return result;
}

function collectColumns(rows: Array<Record<string, unknown>>): string[] {
  const seen = new Set<string>();
  const ordered: string[] = [];
  for (const r of rows) {
    for (const k of Object.keys(r)) {
      if (!seen.has(k)) {
        seen.add(k);
        ordered.push(k);
      }
    }
  }
  return ordered.slice(0, 6);
}

function gridTemplate(n: number): React.CSSProperties {
  return { gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))` };
}

function labelize(key: string): string {
  return key
    .replace(/([A-Z])/g, ' $1')
    .replace(/[_-]+/g, ' ')
    .toLowerCase()
    .replace(/^./, (c) => c.toUpperCase());
}

function summarizeValue(v: unknown): string {
  if (v == null) return '—';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  if (Array.isArray(v)) return `${v.length} items`;
  if (typeof v === 'object') return `structured value — ${Object.keys(v as object).length} keys`;
  return String(v);
}

function asString(v: unknown): string {
  if (v == null) return '—';
  return String(v);
}

function asNum(v: unknown): number {
  if (typeof v === 'number') return v;
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let n = bytes;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n.toFixed(n >= 100 ? 0 : 1)} ${units[i]}`;
}
