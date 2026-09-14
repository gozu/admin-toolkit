import { useEffect, useRef } from 'react';
import type { PageId, ParsedData } from '../types';
import { usageTracker } from '../state/productAnalytics';
import { USAGE_SCAN_FIELDS } from '../utils/usageEvents';
import { MODULE_BY_ID } from '../utils/moduleRegistry';

export function useUsageScanObserver(data: ParsedData): void {
  useEffect(() => {
    const tracker = usageTracker();
    for (const field of USAGE_SCAN_FIELDS) {
      const lifecycle = data[field];
      if (lifecycle) tracker.observeScan(field, lifecycle);
    }
  }, [data]);
}

// Mounted inside the page's Suspense boundary: loading placeholders and hidden
// feature notices are not mistaken for inspected results. Visibility matters;
// a background tab completing its scans has not shown those results yet.
export function ModuleUsage({ page, data }: { page: PageId; data: ParsedData }) {
  const latestData = useRef(data);
  useEffect(() => { latestData.current = data; }, [data]);
  // Progress updates elsewhere on the page must not reset the visibility timer.
  const readiness = MODULE_BY_ID[page].lifecycle.fields.map((field) => {
    const lc = data[field];
    return lc?.phase === 'done' ? `${field}:${lc.startedAt}` : `${field}:${lc?.phase}`;
  }).join('|');
  useEffect(() => { usageTracker().openModule(page); }, [page]);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const view = () => {
      clearTimeout(timer);
      if (document.visibilityState !== 'hidden') {
        timer = setTimeout(() => usageTracker().viewResults(page, latestData.current), 300);
      }
    };
    view();
    document.addEventListener('visibilitychange', view);
    return () => { clearTimeout(timer); document.removeEventListener('visibilitychange', view); };
  }, [page, readiness]);
  return null;
}
