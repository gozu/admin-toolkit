// Audit msgType → workload-bucket taxonomy. Rules were derived from a census
// of a real 2.1GB / ~3M-line audit fixture (2026-03-19 diag): dominant types
// were compute-resource-usage-* (~645k, machine telemetry),
// security-admin-user-* (~520k, API-key admin churn), flow-object-* /
// flow-job-* (~110k runs), application-open (~25k, business-app consumption)
// and a long tail of get/list/read UI polling. First matching rule wins;
// results are memoized (a few hundred distinct msgTypes at most).
//
// Bucketing itself happens server-side in the adoption-events macro
// (python-runnables/adoption-events/runnable.py) — this module re-classifies
// only the top-N msgType list to color it. KEEP THE RULES IN SYNC with the
// macro's _BUCKET_RULES.

export type AuditEventBucket = 'build' | 'run' | 'explore' | 'consume' | 'other';

export const AUDIT_EVENT_BUCKETS: AuditEventBucket[] = [
  'build',
  'run',
  'explore',
  'consume',
  'other',
];

export const AUDIT_EVENT_BUCKET_LABELS: Record<AuditEventBucket, string> = {
  build: 'Build (edits & saves)',
  run: 'Run (jobs & scenarios)',
  explore: 'Explore (reads & lists)',
  consume: 'Consume (dashboards, apps, exports)',
  other: 'Other / system',
};

const RULES: Array<{ re: RegExp; bucket: AuditEventBucket }> = [
  // Machine telemetry and admin/API plumbing first — never a workload signal.
  { re: /^compute-resource-usage-/, bucket: 'other' },
  {
    re: /^security-|^admin-|^publicapi-|api-key|^login|^pnotifications|^dss-internal-|^internal-|^unified-monitoring-/,
    bucket: 'other',
  },
  // Consumption: dashboards/insights, business apps, exports & downloads.
  { re: /^dashboard|^insight|^application-open$|export|download/, bucket: 'consume' },
  // Runs: job/scenario execution and its lifecycle events. Deliberately
  // narrow — job-get-status / jobs-list are UI polling and fall to explore.
  {
    re: /^flow-job-|^flow-object-|^job-start$|^job-abort$|^job-retry$|^scenario-run|^scenario-fire-trigger$|execute|^runnable-run$|^future-abort$|^dataset-clear-samples$/,
    bucket: 'run',
  },
  // Builds: anything that writes config — saves, creates, deletes, commits,
  // renames, uploads, variable/settings writes.
  {
    re: /save|create|delete|commit|rename|upload|import|write-session|schedule|-edit$|^set-|-set$|set-settings/,
    bucket: 'build',
  },
  // Explores: reads, gets, lists, searches, samples, status polling.
  {
    re: /read|-get$|-get-|^get-|list|search|browse|^samples$|^interests-|^discussion|^tags-|counts$|^catalog-|status$/,
    bucket: 'explore',
  },
];

const cache = new Map<string, AuditEventBucket>();

export function classifyMsgType(msgType: string): AuditEventBucket {
  const cached = cache.get(msgType);
  if (cached) return cached;
  let bucket: AuditEventBucket = 'other';
  for (const rule of RULES) {
    if (rule.re.test(msgType)) {
      bucket = rule.bucket;
      break;
    }
  }
  cache.set(msgType, bucket);
  return bucket;
}
