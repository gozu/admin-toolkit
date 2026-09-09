import type { Lifecycle, ParsedData } from '../types';
import { FULL_SCAN_LOADING_FIELDS, MODULES, type LifecycleFieldName } from './moduleRegistry';

const allFields = [...new Set(MODULES.flatMap((m) => m.lifecycle.fields))];

export function overallScanStatus(data: ParsedData, extraWork = false, fatalError?: string | null) {
  const expected = new Set(FULL_SCAN_LOADING_FIELDS);
  const fields = allFields.filter((field) => {
    const lc = data[field];
    return (
      expected.has(field) ||
      lc?.phase === 'running' ||
      (lc?.phase === 'queued' && !!lc.startedAt) ||
      lc?.phase === 'error'
    );
  });
  const pending: { field: LifecycleFieldName; lifecycle: Lifecycle }[] = [];
  const failed: { field: LifecycleFieldName; lifecycle: Lifecycle }[] = [];
  for (const field of fields) {
    const lifecycle = data[field] ?? { phase: 'queued' };
    if (lifecycle.phase === 'running' || lifecycle.phase === 'queued')
      pending.push({ field, lifecycle });
    if (lifecycle.phase === 'error') failed.push({ field, lifecycle });
  }
  return {
    busy:
      extraWork ||
      pending.some(({ lifecycle }) => lifecycle.phase === 'running') ||
      (!fatalError && (!data.dataReady || pending.length > 0)),
    pending,
    failed,
  };
}
