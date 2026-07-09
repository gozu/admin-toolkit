import {
  ZipWriter,
  ZipReader,
  BlobWriter,
  BlobReader,
  TextReader,
} from '@zip.js/zip.js';
import type {
  ParsedData,
  DebugLogEntry,
  AppMode,
  PageId,
  LayoutMode,
  ComparisonState,
  DiagStateWithComparison,
} from '../types';
import { fetchRaw } from './api';
import { getAppVersion } from '../state/appVersionStore';
import { getActiveHost, hostStore } from '../state/hostStore';
import { getRedState } from '../state/redUnlockStore';
import { feedbackFromPageStore } from '../state/feedbackFromPage';
import { getSessionEpoch } from '../state/sessionCache';
import { getRegisteredScanStores } from '../state/scanStoreRegistry';
import { SHARED_LOADING_FIELDS } from './moduleRegistry';
import { deriveAnalysisLifecycle } from './analysisLifecycle';

// ─────────────────────────────────────────────────────────────────────────
// Diagnostic bundle — one comprehensive .zip capturing *everything* needed to
// diagnose a bug: a snapshot of all in-memory client state plus the cheap
// read-only backend endpoints. Built only when the user clicks "Generate".
// Sensitivity = Raw (no redaction): API keys are never held client-side and
// host presets expose only {id,label,url}. Contents are listed transparently
// in the Feedback UI before sending.
// ─────────────────────────────────────────────────────────────────────────

export interface BundleFetchResult {
  ok: boolean;
  status?: number;
  error?: string;
}

export interface BundleManifest {
  generatedAtUtc: string;
  host: { id: string; label: string; url: string };
  appVersion: string;
  files: string[];
  backendFetches: Record<string, BundleFetchResult>;
}

export interface DiagBundleReport {
  type: string;
  message: string;
  email: string;
  diagnosticsText: string;
}

export interface DiagBundleStateSnapshot {
  parsedData: ParsedData;
  debugLogs: DebugLogEntry[];
  mode: AppMode;
  activePage: PageId;
  layoutMode: LayoutMode;
  activeFilter: string;
  focusedConnectionFilter: { name?: string; type?: string } | null;
  focusedUserFilter: { login?: string } | null;
  comparison: ComparisonState;
}

export interface DiagBundleInput {
  report: DiagBundleReport;
  state: DiagBundleStateSnapshot;
}

const SKIP_KEYS = new Set(['dirTree', 'dataReady']);

/**
 * Serialize every non-empty ParsedData field to a `{ name, json }` pair,
 * applying the shared skip/`*Loading`/empty-value rules. Single source of
 * truth for "what client data is serialized" into `client/parsed-data/`.
 */
export function serializeParsedData(parsedData: ParsedData): { name: string; json: string }[] {
  const out: { name: string; json: string }[] = [];
  for (const [key, value] of Object.entries(parsedData)) {
    if (SKIP_KEYS.has(key)) continue;
    if (key.endsWith('Loading')) continue;
    if (value == null) continue;
    if (typeof value === 'object' && Object.keys(value).length === 0) continue;
    if (Array.isArray(value) && value.length === 0) continue;
    out.push({ name: `${key}.json`, json: JSON.stringify(value, null, 2) });
  }
  return out;
}

/** Project the app's full DiagContext state down to the bundle's snapshot. */
export function snapshotDiagState(s: DiagStateWithComparison): DiagBundleStateSnapshot {
  return {
    parsedData: s.parsedData,
    debugLogs: s.debugLogs,
    mode: s.mode,
    activePage: s.activePage,
    layoutMode: s.layoutMode,
    activeFilter: s.activeFilter,
    focusedConnectionFilter: s.focusedConnectionFilter,
    focusedUserFilter: s.focusedUserFilter,
    comparison: s.comparison,
  };
}

// Cheap, read-only backend endpoints → flat entry name under backend/.
const BACKEND_JSON_TARGETS: ReadonlyArray<readonly [string, string]> = [
  ['/api/overview', 'overview.json'],
  ['/api/sanity-check', 'sanity-check.json'],
  ['/api/settings', 'settings.json'],
  ['/api/debug/workers', 'workers.json'],
  ['/api/host/process-metrics', 'process-metrics.json'],
  ['/api/plugins', 'plugins.json'],
];
const SUPPORT_BUNDLE_PATH = '/api/debug/support-bundle';

function utcStamp(now: Date): string {
  // YYYYMMDD-HHMMSS (UTC)
  return now.toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15);
}

function safeSlug(s: string): string {
  return (s || 'host').replace(/[^A-Za-z0-9_-]/g, '_').slice(0, 40) || 'host';
}

function collectLocalStorage(): Record<string, string | null> {
  const out: Record<string, string | null> = {};
  try {
    const ls = globalThis.localStorage;
    if (!ls) return out;
    for (let i = 0; i < ls.length; i++) {
      const key = ls.key(i);
      if (!key) continue;
      if (key.startsWith('admin-toolkit') || key.startsWith('diagparser')) {
        out[key] = ls.getItem(key);
      }
    }
  } catch {
    /* localStorage unavailable */
  }
  return out;
}

function buildEnv(now: Date): unknown {
  const nav = typeof navigator !== 'undefined' ? navigator : undefined;
  const win = typeof window !== 'undefined' ? window : undefined;
  const perfMemory = (() => {
    try {
      const m = (performance as unknown as { memory?: Record<string, number> }).memory;
      if (!m) return null;
      return {
        usedJSHeapSize: m.usedJSHeapSize,
        totalJSHeapSize: m.totalJSHeapSize,
        jsHeapSizeLimit: m.jsHeapSizeLimit,
      };
    } catch {
      return null;
    }
  })();
  return {
    appVersion: getAppVersion(),
    userAgent: nav?.userAgent ?? '',
    platform: nav?.platform ?? '',
    language: nav?.language ?? '',
    timezone: (() => {
      try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone;
      } catch {
        return '';
      }
    })(),
    viewport: win ? { width: win.innerWidth, height: win.innerHeight } : null,
    screen:
      win && win.screen ? { width: win.screen.width, height: win.screen.height } : null,
    devicePixelRatio: win?.devicePixelRatio ?? null,
    online: nav?.onLine ?? null,
    performanceMemory: perfMemory,
    documentUrl: typeof document !== 'undefined' ? document.location.href : '',
    timestamp: now.toISOString(),
  };
}

function buildAppState(s: DiagBundleStateSnapshot): unknown {
  const c = s.comparison;
  return {
    mode: s.mode,
    activePage: s.activePage,
    fromPage: feedbackFromPageStore.get(),
    layoutMode: s.layoutMode,
    activeFilter: s.activeFilter,
    focusedConnectionFilter: s.focusedConnectionFilter,
    focusedUserFilter: s.focusedUserFilter,
    // The comparison files can be large; record only a summary.
    comparison: {
      hasBefore: c.before != null,
      hasAfter: c.after != null,
      hasResult: c.result != null,
      viewMode: c.viewMode,
      isProcessingBefore: c.isProcessingBefore,
      isProcessingAfter: c.isProcessingAfter,
    },
    sessionEpoch: getSessionEpoch(),
  };
}

function buildLifecycle(parsedData: ParsedData, now: Date): unknown {
  const fields: Record<string, unknown> = {};
  for (const f of SHARED_LOADING_FIELDS) {
    fields[f] = parsedData[f] ?? { phase: 'queued' };
  }
  return {
    fields,
    analysisLoading: parsedData.analysisLoading ?? null,
    aggregate: deriveAnalysisLifecycle(parsedData, SHARED_LOADING_FIELDS, now.toISOString()),
  };
}

function buildScanStores(): unknown {
  return getRegisteredScanStores().map((s) => ({
    field: s.field,
    lifecycle: s.lifecycle(),
    snapshot: s.snapshot ? s.snapshot() : null,
  }));
}

function buildReportText(r: DiagBundleReport): string {
  const lines: string[] = [];
  lines.push(`Type: ${r.type}`);
  if (r.email) lines.push(`Email: ${r.email}`);
  lines.push('');
  lines.push('Message:');
  lines.push(r.message || '(empty)');
  lines.push('');
  lines.push('Diagnostics:');
  lines.push(r.diagnosticsText);
  return lines.join('\n');
}

async function fetchJsonTarget(
  path: string,
  name: string,
): Promise<{ name: string; path: string; result: BundleFetchResult; text: string | null }> {
  try {
    const res = await fetchRaw(path);
    if (!res.ok) {
      return { name, path, result: { ok: false, status: res.status }, text: null };
    }
    const text = await res.text();
    return { name, path, result: { ok: true, status: res.status }, text };
  } catch (err) {
    return {
      name,
      path,
      result: { ok: false, error: err instanceof Error ? err.message : String(err) },
      text: null,
    };
  }
}

async function fetchSupportBundle(): Promise<{
  result: BundleFetchResult;
  entries: Array<{ name: string; blob: Blob }>;
}> {
  try {
    const res = await fetchRaw(SUPPORT_BUNDLE_PATH);
    if (!res.ok) {
      return { result: { ok: false, status: res.status }, entries: [] };
    }
    const blob = await res.blob();
    const reader = new ZipReader(new BlobReader(blob));
    const entries: Array<{ name: string; blob: Blob }> = [];
    try {
      for (const entry of await reader.getEntries()) {
        if (entry.directory || !entry.getData) continue;
        // Copy each entry out flat (basename only) under backend/support/.
        const base = entry.filename.split('/').pop() || entry.filename;
        const data = await entry.getData(new BlobWriter());
        entries.push({ name: base, blob: data });
      }
    } finally {
      await reader.close();
    }
    return { result: { ok: true, status: res.status }, entries };
  } catch (err) {
    return {
      result: { ok: false, error: err instanceof Error ? err.message : String(err) },
      entries: [],
    };
  }
}

/**
 * Build the comprehensive diagnostic bundle. Every backend fetch is best-effort:
 * a failure records a manifest entry and never aborts the bundle.
 */
export async function buildDiagBundle(
  input: DiagBundleInput,
): Promise<{ blob: Blob; filename: string; manifest: BundleManifest }> {
  const now = new Date();
  const host = getActiveHost();
  const { state } = input;
  const backendFetches: Record<string, BundleFetchResult> = {};

  // Fire every backend read in parallel (read-only, no scans); write to the zip
  // sequentially afterwards since ZipWriter is single-threaded.
  const [jsonResults, support] = await Promise.all([
    Promise.all(BACKEND_JSON_TARGETS.map(([path, name]) => fetchJsonTarget(path, name))),
    fetchSupportBundle(),
  ]);

  const writer = new ZipWriter(new BlobWriter('application/zip'));
  const files: string[] = [];
  const addText = async (path: string, text: string) => {
    await writer.add(path, new TextReader(text));
    files.push(path);
  };
  const addJson = (path: string, obj: unknown) => addText(path, JSON.stringify(obj, null, 2));

  // report.txt
  await addText('report.txt', buildReportText(input.report));

  // client/
  await addJson('client/env.json', buildEnv(now));
  await addJson('client/app-state.json', buildAppState(state));
  await addJson('client/host.json', {
    hostStore: hostStore.get(),
    redUnlock: getRedState(),
    localStorage: collectLocalStorage(),
  });
  await addJson('client/lifecycle.json', buildLifecycle(state.parsedData, now));
  await addJson('client/debug-logs.json', state.debugLogs);
  await addJson('client/scan-stores.json', buildScanStores());
  for (const { name, json } of serializeParsedData(state.parsedData)) {
    await addText(`client/parsed-data/${name}`, json);
  }

  // backend/
  for (const r of jsonResults) {
    backendFetches[r.path] = r.result;
    if (r.text != null) await addText(`backend/${r.name}`, r.text);
  }

  // backend/support/ — entries copied flat out of the support bundle zip.
  backendFetches[SUPPORT_BUNDLE_PATH] = support.result;
  for (const entry of support.entries) {
    await writer.add(`backend/support/${entry.name}`, new BlobReader(entry.blob));
    files.push(`backend/support/${entry.name}`);
  }

  const manifest: BundleManifest = {
    generatedAtUtc: now.toISOString(),
    host: { id: host.id, label: host.label, url: host.url },
    appVersion: getAppVersion(),
    files,
    backendFetches,
  };
  // manifest.json last, so its file index is complete.
  await writer.add('manifest.json', new TextReader(JSON.stringify(manifest, null, 2)));

  const blob = await writer.close();
  const filename = `admin-toolkit-diag-${safeSlug(host.id)}-${utcStamp(now)}.zip`;
  return { blob, filename, manifest };
}
