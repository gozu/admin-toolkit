import { getActiveHostId } from '../state/hostStore';
import { markLockedFromServer } from '../state/redUnlockStore';
import { parseSseStream, type SseFrame } from './sseStream';

export function getBackendUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) {
    return path;
  }
  if (/^\/?web-apps-backends\//i.test(path)) {
    return path.startsWith('/') ? path : `/${path}`;
  }
  const maybeDataiku = (globalThis as unknown as { dataiku?: { getWebAppBackendUrl?: (p: string) => string } }).dataiku;
  if (maybeDataiku?.getWebAppBackendUrl) {
    return maybeDataiku.getWebAppBackendUrl(path);
  }
  return path;
}

export class ApiRequestError extends Error {
  status: number;
  statusText: string;
  url: string;
  bodySnippet: string;
  body?: unknown;

  constructor(status: number, statusText: string, url: string, bodySnippet: string, body?: unknown) {
    const withBody = bodySnippet ? ` - ${bodySnippet}` : '';
    super(`Request failed: ${status} ${statusText} (${url})${withBody}`);
    this.name = 'ApiRequestError';
    this.status = status;
    this.statusText = statusText;
    this.url = url;
    this.bodySnippet = bodySnippet;
    this.body = body;
  }
}

async function toApiError(response: Response, url: string): Promise<ApiRequestError> {
  let raw = '';
  try {
    raw = await response.text();
  } catch {
    raw = '';
  }
  let body: unknown;
  try {
    body = raw ? JSON.parse(raw) : undefined;
  } catch {
    body = undefined;
  }
  if (response.status === 409 && body && typeof body === 'object'
      && (body as { error?: string }).error === 'macro-project-missing') {
    globalThis.dispatchEvent?.(new CustomEvent('admin-toolkit:macro-project-missing', { detail: body }));
  }
  // The server-side advanced gate rejected us: the unlock cookie is missing,
  // expired, or the admin rotated the password. Reflect the locked state in the
  // UI (the cookie itself is HttpOnly; the server is the source of truth) so the
  // next attempt re-prompts, and let listeners (the toolbar pill) re-render.
  if (response.status === 403 && body && typeof body === 'object'
      && (body as { error?: string }).error === 'advanced-locked') {
    markLockedFromServer();
    globalThis.dispatchEvent?.(new CustomEvent('admin-toolkit:advanced-locked', { detail: body }));
  }
  const compact = raw.replace(/\s+/g, ' ').trim().slice(0, 240);
  return new ApiRequestError(response.status, response.statusText, url, compact, body);
}

function withHostHeader(init?: RequestInit): RequestInit {
  const headers = new Headers(init?.headers);
  if (!headers.has('X-DSS-Host-Id')) {
    headers.set('X-DSS-Host-Id', getActiveHostId());
  }
  // The advanced gate authorizes via the HttpOnly `admin_toolkit_red` cookie,
  // which `credentials: 'same-origin'` attaches automatically — no header needed.
  return { credentials: 'same-origin', ...init, headers };
}

/**
 * Multi-instance HTTP chokepoint. Returns the raw Response with the
 * `X-DSS-Host-Id` header injected. Use this when you need to inspect status,
 * headers, or consume the body manually (e.g. SSE via `parseSseStream`).
 * For JSON/text responses, prefer `fetchJson` / `fetchText`.
 *
 * Even for the raw path, a 409 macro-project-missing response triggers the
 * `admin-toolkit:macro-project-missing` CustomEvent so SSE endpoints can pop
 * the bootstrap modal too. We clone() the response so the caller still owns
 * the body stream untouched.
 */
export async function fetchRaw(path: string, init?: RequestInit): Promise<Response> {
  const url = getBackendUrl(path);
  const response = await fetch(url, withHostHeader(init));
  if (response.status === 409) {
    try {
      const peek = response.clone();
      const body = await peek.json();
      if (body && typeof body === 'object'
          && (body as { error?: string }).error === 'macro-project-missing') {
        globalThis.dispatchEvent?.(new CustomEvent('admin-toolkit:macro-project-missing', { detail: body }));
      }
    } catch {
      /* body was non-JSON or already consumed by the caller */
    }
  }
  return response;
}

export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const url = getBackendUrl(path);
  const response = await fetch(url, withHostHeader(init));
  if (!response.ok) {
    throw await toApiError(response, url);
  }
  return response.json() as Promise<T>;
}

export async function fetchText(path: string, init?: RequestInit): Promise<string> {
  const url = getBackendUrl(path);
  const response = await fetch(url, withHostHeader(init));
  if (!response.ok) {
    throw await toApiError(response, url);
  }
  return response.text();
}

export async function* fetchSse(path: string, init?: RequestInit): AsyncGenerator<SseFrame> {
  const url = getBackendUrl(path);
  const response = await fetch(url, withHostHeader(init));
  if (!response.ok) {
    throw await toApiError(response, url);
  }
  if (!response.body) return;
  yield* parseSseStream(response.body);
}
