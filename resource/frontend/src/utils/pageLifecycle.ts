import type { Lifecycle, ParsedData, PageId } from '../types';
import { MODULE_BY_ID, type ModuleDefinition } from './moduleRegistry';
import { aggregateLifecycles } from './lifecycleAggregator';

// A module that hasn't been kicked off yet reads as `queued` (no startedAt).
// The orchestrator overwrites this with `{ phase: 'queued', startedAt }` at
// session start.
const DEFAULT_QUEUED: Lifecycle = { phase: 'queued' };

// Every module declares a non-empty list of Lifecycle-typed fields on
// ParsedData. The resolver gathers them, defaulting any absent field to
// queued, and runs the pure aggregator. Single-field modules pass through
// unchanged because the aggregator returns the sole state as-is.
export function resolveLifecycle(module: ModuleDefinition, d: ParsedData): Lifecycle {
  const states = module.lifecycle.fields.map((f) => d[f] ?? DEFAULT_QUEUED);
  return aggregateLifecycles(states);
}

export function resolveLifecycleById(pageId: PageId, d: ParsedData): Lifecycle {
  const module = MODULE_BY_ID[pageId];
  if (!module) return DEFAULT_QUEUED;
  return resolveLifecycle(module, d);
}
