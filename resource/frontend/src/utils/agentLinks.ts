// Deep links from agent actions / audit rows into the native DSS UI.
//
// Route contract (same rule as utils/codeEnvUsageLinks.ts): every path here
// corresponds to a concrete (non-abstract) Angular ui-router state, verified
// against the live DSS 14.7 mainpack state table (2026-07-03; clusters
// re-verified 2026-07-07):
//   admin.general.containers      → /admin/general/containers/
//   admin.codeenvs-design.*-edit  → /admin/code-envs/design/<lang>/<name>/
//   connection admin              → /admin/connections/<name>/
//   admin.clusters.cluster        → /admin/clusters/<clusterId>/
//   plugin.summary                → /plugins/<id>/summary/
//   project home                  → /projects/<KEY>/
// image-delete has no DSS page (registry-side object) → no link.

import { getDssBaseUrl } from './codeEnvUsageLinks';
import { hostStore } from '../state/hostStore';

const enc = encodeURIComponent;

/** Base URL of a fleet host: hostStore url, or the local DSS for 'local'. */
export function hostBaseUrl(hostId: string | undefined): string {
  const id = hostId || 'local';
  if (id !== 'local') {
    const host = hostStore.get().hosts.find((h) => h.id === id);
    if (host?.url) return host.url.replace(/\/+$/, '');
  }
  return getDssBaseUrl();
}

/** Trace Explorer's native "Explore trace" handoff key (utils/config.ts of
 * the traces-explorer plugin): write raw trace JSON here, then open the
 * explorer with ?readTraceFromLS=true and it POSTs the trace to its backend
 * and navigates straight to it. Same-origin only — localStorage is per-origin. */
export const EXPLORE_TRACE_STORAGE_KEY = 'ls.llm.traceExplorer.trace';

/**
 * Raw webapp-content URL for the readTraceFromLS handoff. CRITICAL: this must
 * be /dip/api/webapps/view (the SPA reads its own window.location), NOT the
 * /projects/.../view shell page — the DSS shell doesn't forward query params
 * into the iframe.
 */
export function traceExplorerHandoffUrl(
  hostId: string | undefined,
  projectKey: string,
  webAppId: string,
): string {
  return `${hostBaseUrl(hostId)}/dip/api/webapps/view?projectKey=${enc(projectKey)}&webAppId=${enc(webAppId)}&readTraceFromLS=true`;
}

function targetValue(v: unknown): string {
  if (Array.isArray(v)) return v.map(targetValue).join(', ');
  return String(v);
}

/**
 * Human-readable form of an audit/plan target — never raw JSON.
 * `{connection: "X", table: "Y"}` → `X · Y`; single-key objects → the value.
 */
export function humanTarget(target: unknown): string {
  if (target == null) return '';
  if (typeof target !== 'object') return String(target);
  if (Array.isArray(target)) return targetValue(target);
  const values = Object.values(target as Record<string, unknown>).filter((v) => v != null);
  return values.map(targetValue).join(' · ');
}

/** `key: value` list for tooltips — readable, not minified JSON. */
export function targetTitle(target: unknown): string {
  if (target == null) return '';
  if (typeof target !== 'object' || Array.isArray(target)) return humanTarget(target);
  return Object.entries(target as Record<string, unknown>)
    .filter(([, v]) => v != null)
    .map(([k, v]) => `${k}: ${targetValue(v)}`)
    .join(', ');
}

/**
 * DSS UI page for an actuator action's target, or null when no page exists.
 * `target` is the audit row / plan canonicalTarget for that action. Batched
 * canonicals ({batchTargets: [...]}) link to their FIRST target's page.
 */
export function dssLinkForAction(
  action: string | undefined,
  target: unknown,
  hostId: string | undefined,
): string | null {
  let t = (target && typeof target === 'object' ? target : {}) as Record<string, unknown>;
  if (Array.isArray(t.batchTargets) && t.batchTargets.length > 0) {
    const first = t.batchTargets[0];
    t = (first && typeof first === 'object' ? first : {}) as Record<string, unknown>;
  }
  const base = hostBaseUrl(hostId);
  switch (action) {
    case 'project-delete':
      return t.projectKey ? `${base}/projects/${enc(String(t.projectKey))}/` : null;
    case 'code-env-delete':
      return t.name
        ? `${base}/admin/code-envs/design/${enc(String(t.lang || 'python'))}/${enc(String(t.name))}/`
        : null;
    case 'db-vacuum':
    case 'db-analyze':
      return t.connection ? `${base}/admin/connections/${enc(String(t.connection))}/` : null;
    case 'connection-test':
    case 'connection-update':
    case 'connection-delete':
      return t.name ? `${base}/admin/connections/${enc(String(t.name))}/` : null;
    case 'cluster-detach':
      return t.clusterId ? `${base}/admin/clusters/${enc(String(t.clusterId))}/` : null;
    case 'plugin-uninstall':
      return t.pluginId ? `${base}/plugins/${enc(String(t.pluginId))}/summary/` : null;
    case 'project-clear-webapp-runs':
      return t.projectKey ? `${base}/projects/${enc(String(t.projectKey))}/` : null;
    case 'plugin-deploy':
      return t.pluginId
        ? `${hostBaseUrl(t.targetHostId ? String(t.targetHostId) : hostId)}/plugins/${enc(String(t.pluginId))}/summary/`
        : null;
    case 'k8s-exec-config-tune':
      return `${base}/admin/general/containers/`;
    case 'settings-set':
      return `${base}/admin/general/`;
    case 'code-env-consolidate':
      return t.targetEnvName
        ? `${base}/admin/code-envs/design/${enc(String(t.language || 'python'))}/${enc(String(t.targetEnvName))}/`
        : null;
    // log-cleanup / docker-prune / k8s-apply-fix act on host-level objects
    // with no DSS page — no link, same as image-delete.
    case 'image-delete':
    default:
      return null;
  }
}
