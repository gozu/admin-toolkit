import { webappOriginBaseUrl } from './codeEnvUsageLinks';

// Deep links back to *this* webapp's own DSS page — the one place an admin can
// stop and start its Python backend.
//
// Routes checked by hand against a live DSS 14.4 UI — do NOT guess these from
// training data:
//   /projects/{PK}/webapps/{id}_{name}/edit → webapp settings; hosts the
//       "Backend" section, and the ACTIONS menu carries Start/Stop backend.
//   /projects/{PK}/webapps/{id}_{name}/view → the webapp itself, same ACTIONS.
//   /projects/{PK}/webapps/{id}/view        → **404**. The `_{name}` suffix is
//       mandatory, so an id alone can only reach the project's webapp list.
//   /projects/{PK}/webapps/                 → that list. Always valid.
//
// The catch: the SPA runs inside DSS's iframe at
// `/dip/api/webapps/view?projectKey=…&webAppId=…`, which gives us the id but
// never the name. The parent frame's URL *does* carry `{id}_{name}` and is
// same-origin, so read it when we can and fall back to the list when we can't.

export interface WebappSelfLink {
  href: string;
  /** 'settings' lands on the webapp's own settings page; 'list' on the project's webapp list. */
  kind: 'settings' | 'list';
}

/** Path of the DSS shell page hosting this webapp, '' when unreadable. */
function shellPath(): string {
  try {
    // Same-origin in DSS, so this read succeeds; a sandboxed or cross-origin
    // embed throws SecurityError and we degrade to the query-string route.
    const parent = window.parent;
    if (parent && parent !== window) return String(parent.location.pathname || '');
  } catch {
    /* cross-origin parent — nothing to read */
  }
  return '';
}

function trimBase(href: string): string {
  return href.replace(/\/+$/, '');
}

/**
 * Best available link to this webapp's backend controls. Returns null only when
 * neither the shell URL nor the iframe's own query string identifies the webapp
 * (e.g. the bundle is being served outside DSS entirely).
 */
export function webappSelfLink(): WebappSelfLink | null {
  const base = trimBase(webappOriginBaseUrl());

  // 1. Shell URL (or our own, when the app was opened un-framed): carries the
  //    `{id}_{name}` segment the settings route requires.
  const shell = shellPath() || String(window.location.pathname || '');
  const shellMatch = shell.match(/^\/projects\/([^/]+)\/webapps\/([^/]+)/);
  if (shellMatch) {
    const [, projectKey, segment] = shellMatch;
    return { href: `${base}/projects/${projectKey}/webapps/${segment}/edit`, kind: 'settings' };
  }

  // 2. The iframe's own query string — id without name, so aim at the list.
  const projectKey =
    new URLSearchParams(window.location.search).get('projectKey') ||
    // `/public-webapps/{PK}/{id}/` — the no-auth published URL.
    (window.location.pathname.match(/^\/public-webapps\/([^/]+)\//) || [])[1] ||
    '';
  if (projectKey) {
    return { href: `${base}/projects/${encodeURIComponent(projectKey)}/webapps/`, kind: 'list' };
  }

  return null;
}
