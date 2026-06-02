import { ZipWriter, BlobWriter, TextReader } from '@zip.js/zip.js';

function tableToCSV(table: HTMLTableElement): string {
  return Array.from(table.rows)
    .map((row) =>
      Array.from(row.cells)
        .map((c) => `"${c.innerText.replace(/"/g, '""')}"`)
        .join(','),
    )
    .join('\n');
}

function tableName(table: HTMLTableElement, i: number): string {
  const heading = table.closest('[class*="card"], section, [class*="Card"]')
    ?.querySelector('h1, h2, h3, h4, h5, h6, [class*="title"], [class*="Title"]');
  const raw = table.id || table.getAttribute('aria-label') || heading?.textContent || `table-${i + 1}`;
  return raw.trim().replace(/[^a-zA-Z0-9]+/g, '-').replace(/^-|-$/g, '').toLowerCase();
}

export async function exportAllTablesToZip() {
  const tables = document.querySelectorAll<HTMLTableElement>('table');
  if (!tables.length) return;

  const writer = new ZipWriter(new BlobWriter('application/zip'));
  const seen = new Map<string, number>();

  for (const [i, t] of Array.from(tables).entries()) {
    let name = tableName(t, i);
    const count = (seen.get(name) ?? 0) + 1;
    seen.set(name, count);
    if (count > 1) name += `-${count}`;
    await writer.add(`${name}.csv`, new TextReader(tableToCSV(t)));
  }

  const blob = await writer.close();
  const url = URL.createObjectURL(blob);
  Object.assign(document.createElement('a'), { href: url, download: 'tables-export.zip' }).click();
  URL.revokeObjectURL(url);
}
