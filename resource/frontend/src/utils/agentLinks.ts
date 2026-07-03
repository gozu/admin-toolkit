// Deep links from agent actions / audit rows into the native DSS UI.
//
// Route contract (same rule as utils/codeEnvUsageLinks.ts): every path here
// corresponds to a concrete (non-abstract) Angular ui-router state, verified
// against the live DSS 14.7 mainpack state table (2026-07-03):
//   admin.general.containers      → /admin/general/containers/
//   admin.codeenvs-design.*-edit  → /admin/code-envs/design/<lang>/<name>/
//   connection admin              → /admin/connections/<name>/
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

/**
 * DSS UI page for an actuator action's target, or null when no page exists.
 * `target` is the audit row / plan canonicalTarget for that action.
 */
export function dssLinkForAction(
  action: string | undefined,
  target: unknown,
  hostId: string | undefined,
): string | null {
  const t = (target && typeof target === 'object' ? target : {}) as Record<string, unknown>;
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
    case 'plugin-deploy':
      return t.pluginId
        ? `${hostBaseUrl(t.targetHostId ? String(t.targetHostId) : hostId)}/plugins/${enc(String(t.pluginId))}/summary/`
        : null;
    case 'k8s-exec-config-tune':
      return `${base}/admin/general/containers/`;
    case 'image-delete':
    default:
      return null;
  }
}
