import { fetchJson } from './api';

interface ArchiveStoreResult {
  stored?: boolean;
  reason?: string;
  error?: string;
  folderId?: string;
  name?: string;
}

/**
 * Best-effort copy of a client-built export zip into the plugin's
 * 'admin-toolkit-archive' managed folder. Server no-ops ({stored:false})
 * unless the Archive Folders Connection plugin setting is configured, so
 * callers fire-and-forget — the browser download is the primary path.
 */
export async function storeExportInArchive(blob: Blob, name: string): Promise<void> {
  try {
    const result = await fetchJson<ArchiveStoreResult>(
      `/api/archive/store?name=${encodeURIComponent(name)}`,
      { method: 'POST', body: blob, headers: { 'Content-Type': 'application/zip' } },
    );
    if (result.stored) {
      console.info(`[archive] stored ${name} in managed folder ${result.folderId}`);
    }
  } catch (err) {
    console.warn('[archive] export archive copy failed:', err);
  }
}
