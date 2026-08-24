/**
 * Containerized-execution config resource extraction — shared by the live
 * loader (apiLoader/phase2) and the parity harness so the health-score twins
 * can't drift on how the raw settings are read. Resource fields are NESTED
 * under each config's `kubernetesRuntimeConfig.kubernetesResources` (verified
 * against live DSS general settings) — same read
 * python-runnables/k8s-insights/probes.py ships. Unset or <=0 means
 * "not set" (DSS treats 0/unset alike — rules/dss_drift.py semantics).
 */

export interface ExecResourceConfig {
  name: string;
  type?: string;
  memRequestMB?: number | null;
  memLimitMB?: number | null;
  cpuRequest?: number | null;
  cpuLimit?: number | null;
}

/**
 * Absent/malformed executionConfigs array ⇒ `undefined` (skip semantics for
 * the scorer, like dipHomeStorage); present-but-empty ⇒ `[]` (scores 100).
 */
export function extractExecResourceConfigs(rawSettings: unknown): ExecResourceConfig[] | undefined {
  const container = (rawSettings as { containerSettings?: { executionConfigs?: unknown } } | undefined)
    ?.containerSettings;
  const configs = container?.executionConfigs;
  if (!Array.isArray(configs)) return undefined;
  const out: ExecResourceConfig[] = [];
  for (const cfg of configs) {
    if (!cfg || typeof cfg !== 'object') continue;
    const c = cfg as Record<string, unknown>;
    const krc = (c.kubernetesRuntimeConfig ?? {}) as Record<string, unknown>;
    const res = (krc.kubernetesResources ?? {}) as Record<string, unknown>;
    out.push({
      name: String(c.name ?? ''),
      type: c.type != null ? String(c.type) : undefined,
      memRequestMB: typeof res.memRequestMB === 'number' ? res.memRequestMB : null,
      memLimitMB: typeof res.memLimitMB === 'number' ? res.memLimitMB : null,
      cpuRequest: typeof res.cpuRequest === 'number' ? res.cpuRequest : null,
      cpuLimit: typeof res.cpuLimit === 'number' ? res.cpuLimit : null,
    });
  }
  return out;
}
