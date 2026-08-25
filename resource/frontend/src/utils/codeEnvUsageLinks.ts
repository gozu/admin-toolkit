import { getBackendUrl } from './api';
import { getActiveHost } from '../state/hostStore';
import type { CodeEnvUsageRef } from '../types';

function encodeSegment(value: string | undefined): string {
  return encodeURIComponent(value || '');
}

function objectType(usage: CodeEnvUsageRef): string {
  return String(usage.objectType || usage.usageType || 'OBJECT').toUpperCase();
}

/** Origin serving THIS webapp (the primary/local DSS). Use only for links that
 * must stay on the webapp's own instance (self-links, cross-host fallbacks). */
export function webappOriginBaseUrl(): string {
  const backendUrl = getBackendUrl('/');
  const parsed = new URL(backendUrl, window.location.origin);
  return `${parsed.protocol}//${parsed.host}`;
}

/** Base URL of the DSS instance whose data is on screen: the active host's
 * configured url for remotes, the webapp's own origin for 'local'. */
export function getDssBaseUrl(): string {
  const host = getActiveHost();
  if (host.id !== 'local' && host.url) return host.url.replace(/\/+$/, '');
  return webappOriginBaseUrl();
}

export function objectLabel(usage: CodeEnvUsageRef): string {
  const type = objectType(usage);
  if (type.includes('RECIPE')) return 'Recipe';
  if (type.includes('NOTEBOOK') || type.includes('JUPYTER')) return 'Notebook';
  if (type.includes('WEBAPP')) return 'Webapp';
  if (type.includes('SCENARIO')) return 'Scenario';
  if (type.includes('PROJECT')) return 'Project';
  return type.replace(/_/g, ' ').toLowerCase();
}

export function projectUrl(baseUrl: string, projectKey: string | undefined): string {
  return `${baseUrl}/projects/${encodeSegment(projectKey)}/`;
}

export function objectUrl(baseUrl: string, usage: CodeEnvUsageRef): string {
  const pk = encodeSegment(usage.projectKey);
  const id = encodeSegment(usage.objectId);
  const type = objectType(usage);
  if (type.includes('RECIPE')) return `${baseUrl}/projects/${pk}/recipes/${id}/`;
  if (type.includes('NOTEBOOK') || type.includes('JUPYTER')) return `${baseUrl}/projects/${pk}/notebooks/jupyter/${id}/`;
  if (type.includes('WEBAPP')) return `${baseUrl}/projects/${pk}/webapps/${id}/`;
  if (type.includes('SCENARIO')) return `${baseUrl}/projects/${pk}/scenarios/${id}/settings`;
  return projectUrl(baseUrl, usage.projectKey);
}

const enc = encodeSegment;

// Canonical DSS UI URL builders. **Do NOT guess routes from training data** —
// every URL here corresponds to a concrete (non-abstract) Angular ui-router
// state in DSS. See `docs/dss-ui-urls.md` for the route table and how to
// verify new entries.
export const dssUrls = {
  project: (pk: string) => `${getDssBaseUrl()}/projects/${enc(pk)}/`,
  recipe: (pk: string, name: string) => `${getDssBaseUrl()}/projects/${enc(pk)}/recipes/${enc(name)}/`,
  notebook: (pk: string, id: string) => `${getDssBaseUrl()}/projects/${enc(pk)}/notebooks/jupyter/${enc(id)}/`,
  // The webapp route requires `{id}_{name}` plus a leaf state. With only an id, link to the list.
  webapp: (pk: string, id: string, name?: string) =>
    name
      ? `${getDssBaseUrl()}/projects/${enc(pk)}/webapps/${enc(id)}_${enc(name)}/view`
      : `${getDssBaseUrl()}/projects/${enc(pk)}/webapps/`,
  // The scenario state's url is `/:scenarioId` with NO trailing slash (unlike
  // recipes/jobs/notebooks), so `/scenarios/<id>/` matches no state at all —
  // `/settings` is the concrete leaf. Verified against the live 14.7 state table.
  scenario: (pk: string, id: string) => `${getDssBaseUrl()}/projects/${enc(pk)}/scenarios/${enc(id)}/settings`,
  // Dataset parent state is abstract — `/explore/` is the canonical leaf.
  dataset: (pk: string, name: string) => `${getDssBaseUrl()}/projects/${enc(pk)}/datasets/${enc(name)}/explore/`,
  // Saved-model parent state is abstract — `/versions/` is the canonical leaf.
  savedModel: (pk: string, id: string) => `${getDssBaseUrl()}/projects/${enc(pk)}/savedmodels/${enc(id)}/versions/`,
  // Plugin-agent config editor: the saved-model version id is
  // `S-<projectKey>-<smId>-<version>` (verified live). Opens the version's
  // params screen where per-agent flags like `allow_red_actions` live.
  agentConfig: (pk: string, id: string, version: string) =>
    `${getDssBaseUrl()}/projects/${enc(pk)}/savedmodels/${enc(id)}/agent/S-${enc(pk)}-${enc(id)}-${enc(version)}`,
  // In-project API service ("lambda") parent state is abstract — `/endpoints/` is the canonical leaf.
  apiService: (pk: string, id: string) => `${getDssBaseUrl()}/projects/${enc(pk)}/api-designer/${enc(id)}/endpoints/`,
  // No per-bundle route exists; the design-bundles list takes `?bundleId=...` to focus one.
  bundle: (pk: string, id: string) => `${getDssBaseUrl()}/projects/${enc(pk)}/bundles-design/?bundleId=${enc(id)}`,
  codeEnv: (lang: string, name: string) => `${getDssBaseUrl()}/admin/code-envs/design/${enc(lang)}/${enc(name)}/`,
  codeStudio: (pk: string, id: string) => `${getDssBaseUrl()}/projects/${enc(pk)}/code-studios/${enc(id)}/view`,
  // Admin connection edit — `:connectionName/` segment, no `/edit/` prefix.
  llmConn: (name: string) => `${getDssBaseUrl()}/admin/connections/${enc(name)}/`,
  // User profile — `profile.user` (`/profile/:userLogin/`) is abstract, but its
  // `profile.user.view` leaf has url "" so this exact URL is the concrete
  // "Profile" page. Verified against the akaos instance's ui-router state
  // table (mainpack bundle, 2026-06-12).
  dssUser: (login: string) => `${getDssBaseUrl()}/profile/${enc(login)}/`,
  // Plugin parent states are abstract — `plugin.summary` (`/summary/`) is the
  // canonical leaf for installed plugins, `plugindev.definition`
  // (`/definition/`) for dev plugins. Verified against the akaos instance's
  // ui-router state table (mainpack bundle, 2026-06-12).
  plugin: (id: string, isDev?: boolean) =>
    isDev
      ? `${getDssBaseUrl()}/plugins/development/${enc(id)}/definition/`
      : `${getDssBaseUrl()}/plugins/${enc(id)}/summary/`,
};
