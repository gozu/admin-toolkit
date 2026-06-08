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

export interface CollectedTable {
  name: string;
  columns: string[];
  rows: string[][];
}

// The raw display source behind `tableName` (id / aria-label / nearest heading),
// trimmed but NOT kebab-cased — the backend owns dataset-name sanitization.
function rawTableName(table: HTMLTableElement, i: number): string {
  const heading = table.closest('[class*="card"], section, [class*="Card"]')
    ?.querySelector('h1, h2, h3, h4, h5, h6, [class*="title"], [class*="Title"]');
  const raw = table.id || table.getAttribute('aria-label') || heading?.textContent || `table-${i + 1}`;
  return raw.trim();
}

/**
 * Collect every rendered <table> as structured data for "Save as Datasets".
 * Same DOM source as the CSV export, so dataset names match the UI table names.
 * All values come from innerText, so every column is string-typed downstream.
 */
export function collectTablesForDataset(): CollectedTable[] {
  const tables = Array.from(document.querySelectorAll<HTMLTableElement>('table'));
  const out: CollectedTable[] = [];
  const seen = new Map<string, number>();

  for (const [i, table] of tables.entries()) {
    // Header: last <thead> row's <th>s, else the first row's cells.
    const theadRows = Array.from(table.querySelectorAll('thead tr'));
    let headerRow: HTMLTableRowElement | null = theadRows.length
      ? (theadRows[theadRows.length - 1] as HTMLTableRowElement)
      : null;
    let headerCells = headerRow ? Array.from(headerRow.querySelectorAll('th')) : [];
    if (!headerCells.length) {
      headerRow = table.rows[0] ?? null;
      headerCells = headerRow ? (Array.from(headerRow.cells) as HTMLTableCellElement[]) : [];
    }
    const columns = headerCells.map((c) => c.innerText.trim());
    if (!columns.length) continue; // skip tables with zero columns

    // Body: every row except the header row and any <thead> rows.
    const theadSet = new Set(theadRows);
    const rows = Array.from(table.rows)
      .filter((r) => r !== headerRow && !theadSet.has(r))
      .map((r) => Array.from(r.cells).map((c) => c.innerText));

    let name = rawTableName(table, i);
    const count = (seen.get(name) ?? 0) + 1;
    seen.set(name, count);
    if (count > 1) name += ` (${count})`;

    out.push({ name, columns, rows });
  }

  return out;
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
