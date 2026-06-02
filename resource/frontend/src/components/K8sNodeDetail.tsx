import { useEffect, useMemo, useRef, useState } from 'react';
import hljs from 'highlight.js';
import 'highlight.js/styles/monokai-sublime.css';
import type { K8sNodeBreakdown, K8sNodeCondition, K8sNodeTaint, K8sPodOnNode } from '../types';
import { pctTone, phaseTone, ratioTone, TONE_BAR, TONE_BG, TONE_TEXT } from '../utils/k8sTone';
import { Modal } from './Modal';
import { fetchText } from '../utils/api';
import { dssUrls } from '../utils/codeEnvUsageLinks';

interface Props {
  node: K8sNodeBreakdown;
  clusterId: string;
}

export function K8sNodeDetail({ node, clusterId }: Props) {
  const taintRows = node.taints || [];
  const condChips = useMemo(() => meaningfulConditions(node.conditions), [node.conditions]);
  const addresses = node.addresses || [];
  const labels = node.selectedLabels || {};
  const pods = node.pods || [];

  return (
    <div className="glass-card p-4 space-y-4 border border-white/10">
      <header className="flex items-center gap-3 flex-wrap">
        <span className="font-mono text-sm">{node.name}</span>
        <span className="font-mono text-xs text-[var(--text-muted)]">{node.instanceType}</span>
        {node.isGpu && (
          <span className="text-[10px] uppercase border border-yellow-500/40 text-yellow-300 rounded px-1">GPU</span>
        )}
        <span className={`text-xs font-mono ${node.ready ? 'text-emerald-300' : 'text-red-300'}`}>
          {node.ready ? '✓ Ready' : '✗ NotReady'}
        </span>
        {node.unschedulable && (
          <span className={`text-[10px] uppercase border rounded px-1 ${TONE_BG.yellow}`}>cordoned</span>
        )}
        {condChips.map((c) => (
          <span key={c.type} className={`text-[10px] uppercase border rounded px-1 ${TONE_BG[c.tone] || 'border-white/10'}`}>
            {c.label}
          </span>
        ))}
      </header>

      {taintRows.length > 0 && (
        <Section title="Taints">
          <ul className="space-y-1">
            {taintRows.map((t, i) => (
              <li key={i} className={`text-xs font-mono border-l-2 pl-2 ${taintBorder(t)}`}>
                <span className="text-[var(--text-primary)]">{t.key}</span>
                {t.value ? <span className="text-[var(--text-muted)]">={t.value}</span> : null}
                <span className="ml-2 text-[var(--text-muted)]">[{t.effect}]</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Capacity / Used">
        <div className="space-y-2">
          <UsageRow
            label="CPU"
            capacity={node.capacity?.cpu}
            allocatable={node.allocatableCpu}
            usedDisplay={node.cpuUsageMilli != null ? `${node.cpuUsageMilli}m` : '—'}
            pct={node.cpuPct}
          />
          <UsageRow
            label="Memory"
            capacity={node.capacity?.memory}
            allocatable={node.allocatableMemory}
            usedDisplay={node.memUsageMib != null ? `${node.memUsageMib}MiB` : '—'}
            pct={node.memPct}
          />
          {node.isGpu && (
            <UsageRow
              label="GPU"
              capacity={node.capacity?.gpu}
              allocatable={node.allocatableGpu}
              usedDisplay={`${countGpuPods(pods)} pod(s) requesting`}
              pct={null}
            />
          )}
        </div>
      </Section>

      {node.isGpu && (
        <Section title="GPU">
          <GpuSummary node={node} pods={pods} />
        </Section>
      )}

      {node.nodeInfo && (
        <Section title="Node info">
          <KeyValueGrid
            items={[
              ['kubelet', node.nodeInfo.kubeletVersion],
              ['runtime', node.nodeInfo.containerRuntimeVersion],
              ['kernel', node.nodeInfo.kernelVersion],
              ['OS image', node.nodeInfo.osImage],
              ['arch', node.nodeInfo.architecture],
            ]}
          />
        </Section>
      )}

      {addresses.length > 0 && (
        <Section title="Network">
          <KeyValueGrid items={addresses.map((a) => [a.type, a.address])} />
        </Section>
      )}

      {Object.keys(labels).length > 0 && (
        <Section title="Labels">
          <KeyValueGrid items={Object.entries(labels)} />
        </Section>
      )}

      {pods.length > 0 && (
        <Section title={`Pods on this node (${pods.length})`}>
          <PodsTable pods={pods} clusterId={clusterId} />
        </Section>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wider text-[var(--text-muted)] mb-1">{title}</div>
      {children}
    </div>
  );
}

function KeyValueGrid({ items }: { items: Array<[string, string | null | undefined]> }) {
  return (
    <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 text-xs font-mono">
      {items
        .filter(([, v]) => v != null && v !== '')
        .map(([k, v]) => (
          <div key={k} className="flex gap-2 min-w-0">
            <dt className="text-[var(--text-muted)] whitespace-nowrap">{k}</dt>
            <dd className="text-[var(--text-primary)] truncate" title={String(v)}>
              {String(v)}
            </dd>
          </div>
        ))}
    </dl>
  );
}

function UsageRow({
  label,
  capacity,
  allocatable,
  usedDisplay,
  pct,
}: {
  label: string;
  capacity: string | null | undefined;
  allocatable: string | null | undefined;
  usedDisplay: string;
  pct: number | null | undefined;
}) {
  const tone = pctTone(pct);
  return (
    <div className="text-xs font-mono">
      <div className="flex gap-3 items-baseline">
        <span className="text-[var(--text-muted)] w-16 shrink-0">{label}</span>
        <span className="text-[var(--text-primary)]">{capacity || '—'}</span>
        <span className="text-[var(--text-muted)]">→ {allocatable || '—'} allocatable</span>
        <span className="text-[var(--text-muted)]">→ {usedDisplay}</span>
        {pct != null && (
          <span className={`ml-auto ${tone ? TONE_TEXT[tone] : ''}`}>
            {pct.toFixed(0)}%
          </span>
        )}
      </div>
      {pct != null && (
        <div className="mt-1 h-1 rounded bg-white/5 overflow-hidden">
          <div
            className={`h-full ${tone ? TONE_BAR[tone] : 'bg-white/30'}`}
            style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
          />
        </div>
      )}
    </div>
  );
}

function GpuSummary({ node, pods }: { node: K8sNodeBreakdown; pods: K8sPodOnNode[] }) {
  const gpuPodCount = countGpuPods(pods);
  const product = node.selectedLabels?.['nvidia.com/gpu.product'];
  const allocated = node.allocatableGpu;
  return (
    <div className="space-y-1 text-xs font-mono">
      <KeyValueGrid items={[
        ['allocatable GPUs', allocated || null],
        ['product', product || null],
        ['GPU-requesting pods', String(gpuPodCount)],
      ]} />
      {gpuPodCount === 0 && (
        <div className={`text-[11px] mt-1 px-2 py-1 rounded border ${TONE_BG.red}`}>
          GPU node with zero GPU-requesting pods — this node is burning money idle.
        </div>
      )}
    </div>
  );
}

function PodsTable({ pods, clusterId }: { pods: K8sPodOnNode[]; clusterId: string }) {
  const [selected, setSelected] = useState<K8sPodOnNode | null>(null);
  const openDescribe = (p: K8sPodOnNode) => setSelected(p);
  const sorted = useMemo(
    () => [...pods].sort((a, b) => Number(a.isSystem) - Number(b.isSystem) || a.ns.localeCompare(b.ns) || a.name.localeCompare(b.name)),
    [pods],
  );
  return (
    <div className="border border-white/5 rounded overflow-hidden">
      <div className="grid grid-cols-[auto_minmax(0,2fr)_auto_auto_auto] gap-2 px-2 py-1 text-[10px] uppercase text-[var(--text-muted)] bg-white/[0.02]">
        <div>Phase</div>
        <div>ns / name</div>
        <div>Restarts</div>
        <div>CPU req → real</div>
        <div>Mem req → real</div>
      </div>
      <div className="divide-y divide-white/5">
        {sorted.map((p) => {
          const phaseT = phaseTone(p.phase);
          const cpuT = ratioTone(p.realCpuMilli, p.requestedCpuMilli);
          const memT = ratioTone(p.realMemMib, p.requestedMemMib);
          const dangerRing = p.oomKilled || p.crashLoopBackOff ? 'ring-1 ring-red-500/40' : '';
          const objUrl = dssObjectUrl(p);
          return (
            <div
              key={`${p.ns}/${p.name}`}
              className={`grid grid-cols-[auto_minmax(0,2fr)_auto_auto_auto] gap-2 px-2 py-1 text-xs font-mono items-center ${dangerRing}`}
            >
              <span className={`uppercase text-[10px] ${phaseT ? TONE_TEXT[phaseT] : 'text-[var(--text-muted)]'}`}>
                {p.phase || '—'}
              </span>
              <span className="min-w-0 flex flex-col">
                <span className="truncate">
                  <span className={p.isSystem ? 'text-[var(--text-muted)]' : 'text-[var(--text-primary)]'} title={`${p.ns}/${p.name}`}>
                    {p.ns}/
                    <button
                      type="button"
                      onClick={() => openDescribe(p)}
                      className="text-[var(--text-primary)] hover:text-[var(--neon-cyan)] hover:underline focus:outline-none focus:underline"
                      title="kubectl describe pod"
                    >
                      {p.name}
                    </button>
                  </span>
                  {(p.oomKilled || p.crashLoopBackOff) && (
                    <span className="ml-2 text-red-300 text-[10px] uppercase">
                      {p.oomKilled ? 'OOM' : ''}
                      {p.oomKilled && p.crashLoopBackOff ? ' · ' : ''}
                      {p.crashLoopBackOff ? 'CrashLoop' : ''}
                    </span>
                  )}
                </span>
                {(objUrl || p.dssSubmitter) && (
                  <span className="truncate text-[10px] text-[var(--text-muted)]">
                    {objUrl ? (
                      <a
                        href={objUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="text-[var(--neon-cyan)] hover:underline"
                        title={`Open ${p.dssObjectType} in DSS`}
                      >
                        open {p.dssObjectType} in DSS ↗
                      </a>
                    ) : null}
                    {p.dssSubmitter ? (
                      <span className={objUrl ? 'ml-2' : ''}>by {p.dssSubmitter}</span>
                    ) : null}
                  </span>
                )}
              </span>
              <span className={p.restartCount > 0 ? TONE_TEXT.red : 'text-[var(--text-muted)]'}>
                {p.restartCount}
              </span>
              <span className={cpuT ? TONE_TEXT[cpuT] : 'text-[var(--text-muted)]'}>
                {p.requestedCpuMilli}m → {p.realCpuMilli != null ? `${p.realCpuMilli}m` : '—'}
              </span>
              <span className={memT ? TONE_TEXT[memT] : 'text-[var(--text-muted)]'}>
                {p.requestedMemMib}MiB → {p.realMemMib != null ? `${p.realMemMib}MiB` : '—'}
              </span>
            </div>
          );
        })}
      </div>
      {selected && (
        <K8sPodDescribeModal
          key={`${selected.ns}/${selected.name}`}
          clusterId={clusterId}
          pod={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

function dssObjectUrl(p: K8sPodOnNode): string | null {
  if (!p.dssProjectKey || !p.dssObjectId) return null;
  if (p.dssObjectType === 'notebook') return dssUrls.notebook(p.dssProjectKey, p.dssObjectId);
  if (p.dssObjectType === 'recipe') return dssUrls.recipe(p.dssProjectKey, p.dssObjectId);
  return null;
}

function describeErrorMessage(e: unknown): string {
  if (e && typeof e === 'object') {
    const body = (e as { body?: { error?: string } }).body;
    if (body?.error) return body.error;
    const msg = (e as { message?: string }).message;
    if (msg) return msg;
  }
  return String(e);
}

// Syntax-color the `kubectl describe` output. It's key/value + indented blocks,
// close enough to YAML that the `yaml` grammar cleanly colors keys vs values.
// Reuses the proven FileViewer highlight.js pattern (monokai-sublime). On text
// change we reset textContent + clear the highlighted flag, then re-highlight.
// The bounded scroll wrapper + whitespace-pre <pre> preserve horizontal scroll;
// the <code> background is transparent so the modal surface shows through.
function K8sPodDescribeBody({ text }: { text: string }) {
  const codeRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const el = codeRef.current;
    if (!el) return;
    el.textContent = text;
    delete el.dataset.highlighted;
    el.className = 'language-yaml hljs bg-transparent p-0';
    try {
      hljs.highlightElement(el);
    } catch {
      /* leave plain text on failure */
    }
  }, [text]);
  return (
    // Explicit bounded scroll container — same proven pattern as
    // FileViewer / ProjectFolderBreakdownModal (max-h + overflow-auto).
    // whitespace-pre keeps lines unwrapped so wide output scrolls
    // horizontally instead of wrapping.
    <div className="k8s-describe max-h-[82vh] overflow-auto rounded-lg border border-[var(--border-default)] bg-[var(--bg-app)]">
      <pre className="font-mono whitespace-pre text-xs leading-relaxed p-4 m-0 text-[var(--text-primary)]">
        <code ref={codeRef} className="language-yaml hljs bg-transparent p-0" />
      </pre>
    </div>
  );
}

// Mounted fresh (keyed) per pod when describe is requested, so initial state is
// "loading" and the effect only issues the fetch — its setState calls live in
// async callbacks, not the effect body.
function K8sPodDescribeModal({
  onClose,
  clusterId,
  pod,
}: {
  onClose: () => void;
  clusterId: string;
  pod: K8sPodOnNode;
}) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const qs = new URLSearchParams({ clusterId, ns: pod.ns, name: pod.name });
    fetchText(`/api/k8s-insights/pod-describe?${qs.toString()}`)
      .then((t) => {
        if (!cancelled) setText(t);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(describeErrorMessage(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [clusterId, pod]);

  return (
    <Modal isOpen onClose={onClose} title={`describe pod ${pod.ns}/${pod.name}`} sizePreset="full">
      {loading && <div className="text-xs font-mono text-[var(--text-muted)]">Running kubectl describe…</div>}
      {error && <div className="text-xs font-mono text-red-300 whitespace-pre-wrap">{error}</div>}
      {text != null && <K8sPodDescribeBody text={text} />}
    </Modal>
  );
}

function countGpuPods(pods: K8sPodOnNode[]): number {
  // Now that the backend forwards per-pod GPU requests, count pods actually
  // requesting an nvidia.com/gpu — the right denominator for "is this GPU
  // node idle".
  return pods.filter((p) => (p.requestedGpu ?? 0) > 0).length;
}

function meaningfulConditions(
  conds: K8sNodeCondition[] | undefined,
): Array<{ type: string; label: string; tone: 'red' | 'yellow' | 'green' }> {
  if (!conds) return [];
  const out: Array<{ type: string; label: string; tone: 'red' | 'yellow' | 'green' }> = [];
  for (const c of conds) {
    if (c.type === 'Ready') continue; // shown separately
    // Pressure conditions are bad when status == True; NetworkUnavailable
    // is bad when True; others surface only on True.
    if (c.status === 'True') {
      const isPressure = c.type.endsWith('Pressure') || c.type === 'NetworkUnavailable';
      out.push({ type: c.type, label: c.type, tone: isPressure ? 'red' : 'yellow' });
    }
  }
  return out;
}

function taintBorder(t: K8sNodeTaint): string {
  if (t.effect === 'NoSchedule' || t.effect === 'NoExecute') return 'border-red-500/40';
  if (t.effect === 'PreferNoSchedule') return 'border-yellow-500/40';
  return 'border-white/20';
}
