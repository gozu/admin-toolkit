import { ZipWriter, BlobWriter, TextReader } from '@zip.js/zip.js';
import type { ParsedData } from '../types';

const SKIP_KEYS = new Set(['dirTree', 'dataReady']);

/**
 * Serialize every non-empty ParsedData field to a `{ name, json }` pair,
 * applying the shared skip/`*Loading`/empty-value rules. Single source of
 * truth for "what client data is serialized" — shared by the export-all-to-JSON
 * feature and the diagnostic bundle builder.
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

export async function exportDataToZip(parsedData: ParsedData): Promise<void> {
  const writer = new ZipWriter(new BlobWriter('application/zip'));

  for (const { name, json } of serializeParsedData(parsedData)) {
    await writer.add(name, new TextReader(json));
  }

  const blob = await writer.close();
  const company = parsedData.company ?? 'unknown';
  const date = new Date().toISOString().slice(0, 10);
  const filename = `admin-toolkit-export-${company}-${date}.zip`;

  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}
