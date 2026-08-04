import { fetchRaw } from '../utils/api';
import { parseSseStream } from '../utils/sseStream';
import { createSyncStore } from './createSyncStore';
import type { BrokenEnvRow } from '../types';

export interface AdviceEntry {
  status: 'streaming' | 'done' | 'stopped' | 'error';
  llmId: string;
  llmLabel: string;
  text: string;
  error?: string;
}

/** Keyed `${lang}:${name}` so advice survives page navs within a session. */
export const codeEnvAdviceStore = createSyncStore<Record<string, AdviceEntry>>(
  {},
  { sessionScoped: true },
);

const controllers = new Map<string, AbortController>();

export function adviceKey(row: BrokenEnvRow): string {
  return `${row.lang}:${row.name}`;
}

export function clearCodeEnvAdvice(): void {
  for (const controller of controllers.values()) controller.abort();
  controllers.clear();
  codeEnvAdviceStore.set({});
}

/**
 * Stop a streaming request. Whatever streamed so far is kept under 'stopped';
 * a request killed before its first chunk has nothing to show, so its entry is
 * dropped and the row returns to its unasked state.
 */
export function abortCodeEnvAdvice(row: BrokenEnvRow): void {
  const key = adviceKey(row);
  controllers.get(key)?.abort();
  controllers.delete(key);
  const current = codeEnvAdviceStore.get();
  const entry = current[key];
  if (!entry) return;
  if (entry.text) {
    codeEnvAdviceStore.set({ ...current, [key]: { ...entry, status: 'stopped' } });
    return;
  }
  const next = { ...current };
  delete next[key];
  codeEnvAdviceStore.set(next);
}

function patchEntry(key: string, patch: Partial<AdviceEntry>): void {
  const current = codeEnvAdviceStore.get();
  const entry = current[key];
  if (!entry) return;
  codeEnvAdviceStore.set({ ...current, [key]: { ...entry, ...patch } });
}

export async function requestCodeEnvAdvice(
  row: BrokenEnvRow,
  llmId: string,
  llmLabel: string,
): Promise<void> {
  const key = adviceKey(row);
  controllers.get(key)?.abort();
  const controller = new AbortController();
  controllers.set(key, controller);

  codeEnvAdviceStore.set({
    ...codeEnvAdviceStore.get(),
    [key]: { status: 'streaming', llmId, llmLabel, text: '' },
  });

  try {
    const response = await fetchRaw('/api/code-envs/broken/advice', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        llmId,
        envName: row.name,
        envLang: row.lang,
        pythonVersion: row.pythonVersion,
        failureLabel: row.failureLabel,
        errorExcerpt: row.errorExcerpt,
      }),
      signal: controller.signal,
    });
    if (!response.ok || !response.body) {
      throw new Error(`Request failed: ${response.status} ${response.statusText}`);
    }

    let failed = false;
    for await (const { event, payload } of parseSseStream(response.body)) {
      const data = payload as Record<string, unknown>;
      if (event === 'chunk') {
        const chunk = String(data.text || '');
        if (chunk) {
          patchEntry(key, { text: (codeEnvAdviceStore.get()[key]?.text || '') + chunk });
        }
      } else if (event === 'error') {
        failed = true;
        patchEntry(key, { status: 'error', error: String(data.error || 'LLM call failed') });
      }
    }
    if (!failed) patchEntry(key, { status: 'done' });
  } catch (err) {
    const aborted =
      controller.signal.aborted || (err instanceof DOMException && err.name === 'AbortError');
    if (!aborted) {
      patchEntry(key, { status: 'error', error: err instanceof Error ? err.message : String(err) });
    }
  } finally {
    if (controllers.get(key) === controller) controllers.delete(key);
  }
}
