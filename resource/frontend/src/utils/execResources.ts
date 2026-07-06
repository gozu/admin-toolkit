/**
 * Containerized-execution config resource extraction — shared by the live
 * loader (apiLoader/phase2) and the parity harness so the health-score twins
 * can't drift on how the raw settings are read. Resource fields are FLAT on
 * each config (`containerSettings.executionConfigs[].memRequestMB` etc.) —
 * same read python-runnables/k8s-insights/probes.py ships. Unset or <=0 means
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
    out.push({
      name: String(c.name ?? ''),
      type: c.type != null ? String(c.type) : undefined,
      memRequestMB: typeof c.memRequestMB === 'number' ? c.memRequestMB : null,
      memLimitMB: typeof c.memLimitMB === 'number' ? c.memLimitMB : null,
      cpuRequest: typeof c.cpuRequest === 'number' ? c.cpuRequest : null,
      cpuLimit: typeof c.cpuLimit === 'number' ? c.cpuLimit : null,
    });
  }
  return out;
}
