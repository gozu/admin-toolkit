import type { ModuleAvailabilityPolicy } from './moduleRegistry';
import type { Cluster, ContainerExecDefaults, Lifecycle } from '../types';

// ─────────────────────────────────────────────────────────────────────────
// Module availability — auto-hide modules that cannot apply to this host
// (no K8s, no Docker registry, no runtime DB, no LLM usage). Semantics:
// a module is 'unavailable' ONLY on a settled, definitive absence signal;
// anything unknown / still loading / errored resolves 'unknown', and the
// nav surfaces keep unknown modules visible (with an optional sticky
// per-host seed so the next session starts out right).
// ─────────────────────────────────────────────────────────────────────────

export type ModuleAvailability = 'available' | 'unavailable' | 'unknown';

export interface ModuleAvailabilityInputs {
  /** Zip-mode cluster list (NOT populated in API mode). */
  clusters: readonly Cluster[] | undefined;
  /** API-mode cheap cluster count from /api/k8s-insights/cluster-count. */
  clusterCount: number | null;
  clusterCountLoaded: boolean;
  containerExecDefaults: ContainerExecDefaults | undefined;
  /** Docker registry detect scan (session-early) — settled + its provider. */
  registryDetectSettled: boolean;
  registryProvider: string | null;
  llmAuditLoading: Lifecycle | undefined;
  /** Runtime-DB connections discovery (session-early warmup). */
  dbHealthLoaded: boolean;
  dbHealthErrored: boolean;
  dbHealthConfiguredConnection: string | null;
}

export function resolveModuleAvailability(
  policy: ModuleAvailabilityPolicy,
  inputs: ModuleAvailabilityInputs,
): ModuleAvailability {
  switch (policy) {
    case 'always':
      return 'available';

    case 'clusters': {
      // Zip mode carries the parsed cluster list; API mode never populates it
      // and relies on the dedicated cluster-count signal (null = unknown).
      if (inputs.clusters !== undefined) {
        return inputs.clusters.length > 0 ? 'available' : 'unavailable';
      }
      if (inputs.clusterCountLoaded && inputs.clusterCount !== null) {
        return inputs.clusterCount > 0 ? 'available' : 'unavailable';
      }
      return 'unknown';
    }

    case 'container-exec': {
      const d = inputs.containerExecDefaults;
      if (d === undefined) return 'unknown';
      const none =
        d.executionConfigsCount === 0 &&
        d.userCodeMode === 'NONE' &&
        d.visualRecipesMode === 'NONE';
      return none ? 'unavailable' : 'available';
    }

    case 'container-registry': {
      if (!inputs.registryDetectSettled) return 'unknown';
      return inputs.registryProvider ? 'available' : 'unavailable';
    }

    case 'llm': {
      const lc = inputs.llmAuditLoading;
      if (lc?.phase !== 'done') return 'unknown'; // errors stay visible
      return lc.isEmpty ? 'unavailable' : 'available';
    }

    case 'runtime-db': {
      // A configured-but-empty DB must stay visible → gate on the configured
      // connection, never on emptiness. Discovery errors stay visible.
      if (!inputs.dbHealthLoaded || inputs.dbHealthErrored) return 'unknown';
      return inputs.dbHealthConfiguredConnection ? 'available' : 'unavailable';
    }
  }
  // Exhaustiveness guard (no default branch, per the module contract): adding
  // a ModuleAvailabilityPolicy member without a case above is a compile error.
  const exhausted: never = policy;
  return exhausted;
}
