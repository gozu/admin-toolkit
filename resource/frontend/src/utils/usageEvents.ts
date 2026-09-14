import type { Lifecycle, PageId, ParsedData } from '../types';
import { MODULES, MODULE_BY_ID, type LifecycleFieldName } from './moduleRegistry';

export type UsageEventName =
  | 'adtk_activity'
  | 'adtk_webapp_opened' | 'adtk_module_opened' | 'adtk_results_viewed'
  | 'adtk_scan_started' | 'adtk_scan_completed' | 'adtk_scan_failed'
  | 'adtk_scan_cancelled' | 'adtk_scan_results_viewed'
  | 'adtk_snapshot_created' | 'adtk_comparison_completed';
export type ScanTrigger = 'automatic' | 'manual' | 'on_demand' | 'unknown';
export interface UsageProperties {
  module_id?: PageId;
  scan_key?: LifecycleFieldName;
  visit_id?: string;
  scan_id?: string;
  duration_ms?: number;
  trigger?: ScanTrigger;
  is_empty?: boolean;
  start_observed?: boolean;
  results_state?: 'partial' | 'complete';
  format?: 'diagnostic_bundle' | 'snapshot';
  data_source?: 'api' | 'zip';
}
export type UsageEmitter = (event: UsageEventName, properties: UsageProperties) => void;
export const USAGE_SCAN_FIELDS = [...new Set(MODULES.flatMap((module) => module.lifecycle.fields))];

// DSS also supports HTTP installations, where crypto.randomUUID is absent.
// getRandomValues works there; the final fallback keeps analytics non-blocking.
export function usageUuid(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** Lifecycle events count underlying scans once, even when several pages share them.
 * Rendered module results get their own visit ID, while scan funnels hold scan_id
 * constant. Ready-on-open pages therefore do not need a new scan to convert.
 */
export function createUsageTracker(emit: UsageEmitter, uuid = usageUuid, minimumStartedAt = -Infinity) {
  const scans = new Map<LifecycleFieldName, {
    id: string; startedAt: string; phase: Lifecycle['phase'] | 'cancelled'; trigger: ScanTrigger;
  }>();
  let visit: { page: PageId; id: string; viewed: boolean; scansViewed: Set<string> } | null = null;

  const observeScan = (field: LifecycleFieldName, lifecycle: Lifecycle, trigger: ScanTrigger = 'unknown') => {
    if (lifecycle.phase === 'queued') return;
    if (Date.parse(lifecycle.startedAt) < minimumStartedAt) return;
    const previous = scans.get(field);
    if (previous && Date.parse(lifecycle.startedAt) < Date.parse(previous.startedAt)) return;
    const cancelled = lifecycle.phase === 'running' && lifecycle.subPhase === 'aborted';
    const phase = cancelled ? 'cancelled' : lifecycle.phase;
    let scan = previous;
    if (!scan || scan.startedAt !== lifecycle.startedAt) {
      if (cancelled) return;
      scan = { id: uuid(), startedAt: lifecycle.startedAt, phase: 'running', trigger };
      scans.set(field, scan);
      emit('adtk_scan_started', {
        scan_key: field, scan_id: scan.id, trigger,
        start_observed: lifecycle.phase === 'running',
      });
    }
    if (phase === 'running' || scan.phase === phase) return;
    // A terminal state is emitted only on a phase transition, never on a
    // percentage, render, or timestamp-only enrichment update.
    scan.phase = phase;
    const props: UsageProperties = { scan_key: field, scan_id: scan.id, trigger: scan.trigger };
    if (lifecycle.phase === 'done' || lifecycle.phase === 'error') {
      const duration = Date.parse(lifecycle.finishedAt) - Date.parse(lifecycle.startedAt);
      if (Number.isFinite(duration)) props.duration_ms = Math.max(0, duration);
    }
    if (lifecycle.phase === 'done') props.is_empty = lifecycle.isEmpty;
    emit(phase === 'done' ? 'adtk_scan_completed' : phase === 'cancelled' ? 'adtk_scan_cancelled' : 'adtk_scan_failed', props);
  };

  return {
    observeScan,
    openModule(page: PageId) {
      if (visit?.page === page) return;
      visit = { page, id: uuid(), viewed: false, scansViewed: new Set() };
      emit('adtk_module_opened', { module_id: page, visit_id: visit.id });
    },
    leaveModule() { visit = null; },
    viewResults(page: PageId, data: ParsedData) {
      if (!visit || visit.page !== page) return;
      const fields = MODULE_BY_ID[page].lifecycle.fields;
      const done = fields.filter((field) => {
        const lifecycle = data[field];
        return lifecycle?.phase === 'done' && Date.parse(lifecycle.startedAt) >= minimumStartedAt;
      });
      if (!done.length) return;
      const props: UsageProperties = {
        module_id: page, visit_id: visit.id,
        results_state: done.length === fields.length ? 'complete' : 'partial',
      };
      if (!visit.viewed) {
        visit.viewed = true;
        emit('adtk_results_viewed', props);
      }
      for (const field of done) {
        const scan = scans.get(field);
        if (!scan || scan.phase !== 'done' || visit.scansViewed.has(scan.id)) continue;
        visit.scansViewed.add(scan.id);
        emit('adtk_scan_results_viewed', { ...props, scan_key: field, scan_id: scan.id, trigger: scan.trigger });
      }
    },
  };
}
