#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// Frontend sources live under resource/frontend/src. Resolve paths against the
// frontend root derived from this script's location so the checker works no
// matter where it is invoked from (project root per CLAUDE.md, or the frontend
// dir via the package.json `check:contracts` script).
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'resource', 'frontend');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');
const fail = (msg) => {
  console.error(`[contracts] ${msg}`);
  process.exitCode = 1;
};

// types/ is split into domain files behind a barrel index.ts — concatenate
// them all so symbol checks (e.g. the PageId union) survive future moves.
const types = fs
  .readdirSync(path.join(root, 'src/types'))
  .filter((f) => f.endsWith('.ts'))
  .map((f) => read(path.join('src/types', f)))
  .join('\n');
const registry = read('src/utils/moduleRegistry.ts');
const sidebar = read('src/components/layout/Sidebar.tsx');
const palette = read('src/components/CommandPalette.tsx');
const containerExecs = read('src/components/ContainerExecs.tsx');
const containerExecsStore = read('src/state/containerExecsStore.ts');
const healthScoreCard = read('src/components/HealthScoreCard.tsx');
const progressIndicator = read('src/components/common/ProgressIndicator.tsx');
const sseStream = read('src/utils/sseStream.ts');
const useApiDataLoader = read('src/hooks/useApiDataLoader.ts');

const pageUnion = types.match(/export type PageId =([\s\S]*?);/);
const pageIds = pageUnion
  ? [...pageUnion[1].matchAll(/'([^']+)'/g)].map((m) => m[1])
  : [];

for (const pageId of pageIds) {
  if (!registry.includes(`id: '${pageId}'`)) {
    fail(`PageId "${pageId}" is missing from moduleRegistry.ts`);
  }
}

if (!sidebar.includes('MODULE_NAV_SECTIONS') || !sidebar.includes('MODULE_BY_ID')) {
  fail('Sidebar must render from moduleRegistry metadata.');
}

if (!palette.includes('COMMAND_PALETTE_MODULES')) {
  fail('CommandPalette must render from moduleRegistry metadata.');
}

if (!containerExecs.includes('ProgressIndicator')) {
  fail('ContainerExecs must use the shared ProgressIndicator.');
}

if (!containerExecsStore.includes('createModuleScanStore')) {
  fail('Container exec store must be built on the shared createModuleScanStore factory.');
}

if (!containerExecsStore.includes('/api/container-execs/stream')) {
  fail('Container exec store must reference the streaming scan endpoint for real progress.');
}

// Module Bootstrap Contract — module pages must not refire bootstrap fetches
// on remount. Bootstrap fetches live in state/<module>Store.ts; the page
// calls <store>.load() (or equivalent) from a single mount effect. A
// mount-only useEffect(..., []) that references an /api/ literal not
// wrapped in *.load(...) fails the build. User-action / user-input-keyed
// refetches are exempt (per-connection drilldowns, per-provider lookups,
// user-clicked Rescan).
const MODULE_PAGES = [
  ['src/components/ContainerExecs.tsx',           'containerExecsScan'],
  ['src/components/CSTemplateReplacement.tsx',    'csTemplateScan'],
  ['src/components/pages/CodeEnvsPage.tsx',       'managedFoldersScan'],
  ['src/components/pages/DbHealthPage.tsx',       'dbHealthConnectionsStore'],
  ['src/components/ImageCleaner.tsx',             'imageCleanerDetectScan'],
  ['src/components/ProjectSqlPushdownTable.tsx',  'sqlPushdownScan'],
];

function findMountOnlyEffects(body) {
  // Match useEffect(() => { ... }, []) — capture the body between the
  // opening { and the matching `, []` close. Greedy match within balance
  // is hard without a real parser; we use a non-greedy scan and balance
  // braces manually.
  const out = [];
  const re = /useEffect\s*\(\s*\(\s*\)\s*=>\s*\{/g;
  let m;
  while ((m = re.exec(body)) !== null) {
    let i = m.index + m[0].length;
    let depth = 1;
    while (i < body.length && depth > 0) {
      const ch = body[i];
      if (ch === '{') depth += 1;
      else if (ch === '}') depth -= 1;
      i += 1;
    }
    // After the closing `}`, expect `, []`. Allow whitespace.
    const tail = body.slice(i, i + 16);
    if (/^\s*,\s*\[\s*\]/.test(tail)) {
      out.push(body.slice(m.index + m[0].length, i - 1));
    }
  }
  return out;
}

for (const [page, storeName] of MODULE_PAGES) {
  let body;
  try {
    body = read(page);
  } catch {
    fail(`Module page ${page} is missing — update MODULE_PAGES in check_frontend_contracts.mjs.`);
    continue;
  }
  if (!body.includes(storeName)) {
    fail(`${page} must import or reference '${storeName}' — bootstrap fetches must live in the module store.`);
  }
  const effects = findMountOnlyEffects(body);
  for (const eff of effects) {
    // Find any /api/ literal in the effect body that is not on a line which
    // also contains `<storeName>.load(` or `start<...>Scan(` or `<...>.load(`.
    const apiRefs = [...eff.matchAll(/['"`](\/api\/[^'"`]+)['"`]/g)];
    for (const ref of apiRefs) {
      const start = Math.max(0, ref.index - 200);
      const window = eff.slice(start, ref.index + ref[0].length);
      if (/\b\w+\s*\.\s*load\s*\(/.test(window)) continue;
      if (/\bstart[A-Z]\w*Scan\s*\(/.test(window)) continue;
      if (/\b\w+Store\s*\.\s*load\s*\(/.test(window)) continue;
      fail(
        `${page} fires an /api/ fetch (${ref[1]}) from a mount-only useEffect — bootstrap fetches must go through ${storeName}.load() (or equivalent).`,
      );
    }
  }
}

const healthSummaryMatch = healthScoreCard.match(/export function HealthScoreCard[\s\S]*$/);
const healthSummaryBody = healthSummaryMatch ? healthSummaryMatch[0] : '';
for (const forbidden of ['Issues Detected', 'No Issues Detected', 'Customize health checks']) {
  if (healthSummaryBody.includes(forbidden)) {
    fail(`HealthScoreCard summary view must not render "${forbidden}"; move it to Issues.`);
  }
}

const progressToneChecks = [
  ['loading', 'bg-[var(--text-tertiary)]'],
  ['active', 'bg-[var(--neon-yellow)]'],
  ['ready', 'bg-white'],
  ['error', 'bg-[var(--neon-red)]'],
];

for (const [tone, colorClass] of progressToneChecks) {
  if (!progressIndicator.includes(colorClass)) {
    fail(`ProgressIndicator tone "${tone}" must preserve its contract color (${colorClass}).`);
  }
}

const propsBlockMatch = progressIndicator.match(/interface ProgressIndicatorProps\s*\{([\s\S]*?)\}/);
if (propsBlockMatch && /\btone\?\s*:/.test(propsBlockMatch[1])) {
  fail('ProgressIndicator must not accept a `tone` prop — tone is derived from loading state.');
}

if (sidebar.includes("isDimmed ? 'opacity-40'") || sidebar.includes("text-[var(--warning)] opacity-75")) {
  fail('Sidebar must not tone-encode load state on the row label — use the trailing SidebarItemStatus glyph instead.');
}
if (!sidebar.includes('SidebarItemStatus')) {
  fail('Sidebar must render SidebarItemStatus to surface per-row load state via a trailing glyph.');
}

// SSE parser uniqueness — buffer.split('\n\n') and ^event: regex must only live in sseStream.ts
const sseParserCallsites = [
  'src/state/containerExecsStore.ts',
  'src/state/sqlPushdownScan.ts',
  'src/state/createModuleScanStore.ts',
  'src/components/ContainerExecs.tsx',
  'src/components/ConnectionHealthCard.tsx',
  'src/components/ConnectionUsageCard.tsx',
  'src/hooks/useApiDataLoader.ts',
];

for (const file of sseParserCallsites) {
  let body;
  try {
    body = read(file);
  } catch {
    continue;
  }
  if (body.includes("buffer.split('\\n\\n')")) {
    fail(`${file} must use parseSseStream() — raw SSE parser leaked.`);
  }
  if (/(^|[^\w])\/\^event:/m.test(body) && !file.endsWith('sseStream.ts')) {
    fail(`${file} must not parse SSE 'event:' lines directly; use parseSseStream().`);
  }
}

if (!sseStream.includes("buffer.split('\\n\\n')")) {
  fail('sseStream.ts must own the SSE buffer-split implementation.');
}

if (!useApiDataLoader.includes('parseSseStream')) {
  fail('useApiDataLoader must read connection-health SSE via parseSseStream().');
}

// track() lint: every glyph-bearing fetch in useApiDataLoader.ts must flow
// through the track(...) chokepoint so its Lifecycle is mechanically tied to
// its promise (one place opens running, one settles done/error). A fetch fired
// outside track() — and not on the exempt list — is the one remaining way to
// produce a divergent glyph, so it fails the build.
//
// Exempt (no sidebar glyph / not part of the global aggregate): bootstrap
// fetches, the bare Phase-2 members, the rows-only progress polls, and the
// dir-tree pre-warm. Progress polls build their URL from a template literal, so
// the plain-string matcher below skips them naturally; the explicit /progress
// rule documents the intent.
{
  const TRACK_EXEMPT = [
    '/api/overview',
    '/api/settings/raw',
    '/api/project-standards/raw',
    '/api/plugins',
    '/api/java-memory',
    '/api/mail-channels',
    '/api/settings',
    '/api/projects',
    '/api/dir-tree',
  ];
  const isTrackExempt = (url) =>
    TRACK_EXEMPT.some((e) => url === e || url.startsWith(`${e}?`) || url.startsWith(`${e}/`)) ||
    /\/api\/[^'"`]*\/progress/.test(url);

  // Char-index ranges spanned by each track(...) call, so we can test whether a
  // fetch call sits (transitively) inside one — e.g. fetchJson nested in
  // timedFetch nested in track, or fetchRaw inside an async IIFE passed to track.
  const trackRanges = [];
  const trackCallRe = /\btrack\s*(?:<[^(]*>)?\s*\(/g;
  let tm;
  while ((tm = trackCallRe.exec(useApiDataLoader)) !== null) {
    let i = tm.index + tm[0].length;
    let depth = 1;
    while (i < useApiDataLoader.length && depth > 0) {
      const ch = useApiDataLoader[i];
      if (ch === '(') depth += 1;
      else if (ch === ')') depth -= 1;
      i += 1;
    }
    trackRanges.push([tm.index, i]);
  }
  const insideTrack = (idx) => trackRanges.some(([s, e]) => idx >= s && idx < e);

  // Plain-string fetch call sites only (template-literal URLs are the progress
  // polls, which are exempt by design).
  const fetchRe = /\bfetch(?:Json|Raw|Text)\s*(?:<[^(]*>)?\s*\(\s*['"]([^'"]+)['"]/g;
  let fm;
  while ((fm = fetchRe.exec(useApiDataLoader)) !== null) {
    const url = fm[1];
    if (isTrackExempt(url)) continue;
    if (!insideTrack(fm.index)) {
      fail(
        `useApiDataLoader: fetch to '${url}' is not wrapped in track(...) and is not on the exempt list. ` +
          'Glyph-bearing fetches must flow through track() so their Lifecycle is tied to the promise.',
      );
    }
  }
}

// Trends contract: every module with trends:true should have a key `id-with-_` in trends_registry.py
let trendsPy;
try {
  trendsPy = read('../../python-lib/trends_registry.py');
} catch {
  trendsPy = '';
}
if (trendsPy) {
  const trendsModules = [...registry.matchAll(/\{[^{}]*\bid:\s*'([^']+)'[^{}]*\btrends:\s*true[^{}]*\}/g)].map((m) => m[1]);
  for (const id of trendsModules) {
    const key = id.replace(/-/g, '_');
    if (!new RegExp(`TrendSnapshotTable\\(\\s*'${key}'`).test(trendsPy)) {
      fail(`Module '${id}' declares trends:true but no TrendSnapshotTable('${key}', ...) exists.`);
    }
  }
}

// Multi-instance contract: every frontend HTTP call must go through utils/api.ts
function scanForDirectFetch(dir, results = []) {
  if (!fs.existsSync(path.join(root, dir))) return results;
  for (const entry of fs.readdirSync(path.join(root, dir), { withFileTypes: true })) {
    const rel = `${dir}/${entry.name}`;
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules' || entry.name === 'tests' || entry.name.startsWith('.')) continue;
      scanForDirectFetch(rel, results);
      continue;
    }
    if (!/\.(ts|tsx)$/.test(entry.name)) continue;
    if (rel === 'src/utils/api.ts') continue; // The chokepoint owns fetch().
    const body = fs.readFileSync(path.join(root, rel), 'utf8');
    if (/(?<![\w.])fetch\(/.test(body)) {
      results.push(rel);
    }
  }
  return results;
}

const directFetchOffenders = scanForDirectFetch('src');
for (const offender of directFetchOffenders) {
  fail(`${offender} uses fetch() directly — multi-instance routing requires fetchJson/fetchText/fetchSse from utils/api.ts.`);
}

function scanForPreResolvedBackendUrls(dir, results = []) {
  if (!fs.existsSync(path.join(root, dir))) return results;
  for (const entry of fs.readdirSync(path.join(root, dir), { withFileTypes: true })) {
    const rel = `${dir}/${entry.name}`;
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules' || entry.name === 'tests' || entry.name.startsWith('.')) continue;
      scanForPreResolvedBackendUrls(rel, results);
      continue;
    }
    if (!/\.(ts|tsx)$/.test(entry.name)) continue;
    if (rel === 'src/utils/api.ts') continue;
    const body = fs.readFileSync(path.join(root, rel), 'utf8');
    if (/fetch(?:Raw|Json|Text|Sse)\s*\(\s*getBackendUrl\s*\(/.test(body)) {
      results.push(rel);
    }
  }
  return results;
}

for (const offender of scanForPreResolvedBackendUrls('src')) {
  fail(`${offender} passes getBackendUrl(...) into a fetch helper — helpers own backend URL resolution.`);
}

// ─────────────────────────────────────────────────────────────────────────
// Unified lifecycle contracts (post-refactor)
// ─────────────────────────────────────────────────────────────────────────
const pageLifecycle = (() => {
  try {
    return read('src/utils/pageLifecycle.ts');
  } catch {
    return '';
  }
})();
const analysisLifecycle = (() => {
  try {
    return read('src/utils/analysisLifecycle.ts');
  } catch {
    return '';
  }
})();
const scanStoreRegistry = (() => {
  try {
    return read('src/state/scanStoreRegistry.ts');
  } catch {
    return '';
  }
})();

// 1) Every MODULES entry must declare `lifecycle: { fields: [...] }` — a
//    non-empty list of Lifecycle-typed fields. No `kind: 'static' |
//    'derived' | 'scanStore' | 'parsedData'` escape hatches remain — every
//    page follows the same queued → running → done ritual sourced from one
//    or more ParsedData Lifecycle fields.
for (const forbiddenKind of [
  "kind: 'static'",
  "kind: 'derived'",
  "kind: 'scanStore'",
  "kind: 'parsedData'",
  'lifecycle: { field:',
  'lifecycle: { field :',
]) {
  if (registry.includes(forbiddenKind)) {
    fail(`moduleRegistry.ts still contains "${forbiddenKind}" — collapse to lifecycle: { fields: [...] }.`);
  }
}

const moduleEntries = [...registry.matchAll(/\{\s*id:\s*'([^']+)'[\s\S]*?\}\s*,/g)];
const lifecycleFieldsByModule = new Map();
for (const m of moduleEntries) {
  const id = m[1];
  if (!pageIds.includes(id)) continue;
  const block = m[0];
  const fieldsMatch = block.match(/lifecycle:\s*\{\s*fields:\s*\[([\s\S]*?)\]\s*\}/);
  if (!fieldsMatch) {
    fail(`Module '${id}' has no lifecycle: { fields: [...] } source.`);
    continue;
  }
  const inside = fieldsMatch[1];
  const fields = [...inside.matchAll(/'([^']+)'/g)].map((mm) => mm[1]);
  if (fields.length === 0) {
    fail(`Module '${id}' has an empty lifecycle fields tuple — must list at least one field.`);
    continue;
  }
  for (const field of fields) {
    if (!/Loading$/.test(field)) {
      fail(`Module '${id}' lifecycle field '${field}' must end with 'Loading'.`);
    }
  }
  lifecycleFieldsByModule.set(id, fields);
}

// 2) Every lifecycle field referenced by MODULES must be declared as a
//    Lifecycle on ParsedData.
for (const [id, fields] of lifecycleFieldsByModule.entries()) {
  for (const field of fields) {
    if (!new RegExp(`\\b${field}\\?:\\s*Lifecycle\\b`).test(types)) {
      fail(`Module '${id}' references lifecycle field '${field}' but it is not declared as 'Lifecycle' on ParsedData.`);
    }
  }
}

// 3) The initial/terminal-writer audit is gone: with the orchestrator deleted
//    and the loader's hand-maintained markX calls collapsed into the track()
//    chokepoint, "every field has both a start and an end writer" is now a
//    structural property (track opens running at call, settles on resolve/
//    reject) enforced by the track() lint above, not a regex over writers.

// 5) Sidebar must implement the ghost-fade ritual (cross-fade + done-ghost).
if (!sidebar.includes('ghost-fade')) {
  fail('Sidebar.tsx must contain the ghost-fade marker — the completion-glyph fade ritual is required.');
}
if (!sidebar.includes('motion-reduce:transition-none')) {
  fail('Sidebar.tsx must include motion-reduce:transition-none on the glyph transition for accessibility.');
}

// 6) Sidebar must not depend on legacy availability/justReady/initialized for
//    glyph rendering. The new resolver is the only path to a glyph.
if (/getPageAvailability\b/.test(sidebar)) {
  fail('Sidebar must not import getPageAvailability — glyphs are derived from Lifecycle.');
}
if (/justReadyPages|prevAvailRef|setInitialized/.test(sidebar)) {
  fail('Sidebar must not retain justReadyPages/prevAvailRef/initialized — Lifecycle replaces them.');
}

// 7) ProgressIndicator must not infer completion from progressPct >= 100.
if (/progressPct\s*>=\s*100/.test(progressIndicator)) {
  fail('ProgressIndicator must not derive tone from `progressPct >= 100`; use Lifecycle.phase.');
}

// 8) scanStoreRegistry: every scan store is a lifecycle store. The previous
//    fieldKind branching is gone.
if (/fieldKind/.test(scanStoreRegistry)) {
  fail('scanStoreRegistry must not branch on fieldKind — every scan store is a lifecycle store.');
}

// 9) analysisLifecycle helper must exist and be called from both the loader
//    and the scan-store mirror.
if (!/deriveAnalysisLifecycle/.test(analysisLifecycle)) {
  fail('analysisLifecycle.ts must export deriveAnalysisLifecycle.');
}
if (!useApiDataLoader.includes('deriveAnalysisLifecycle')) {
  fail('useApiDataLoader must call deriveAnalysisLifecycle so loader patches feed the aggregate.');
}
try {
  const mirror = read('src/hooks/useScanStoreLoadingMirror.ts');
  if (!mirror.includes('deriveAnalysisLifecycle')) {
    fail('useScanStoreLoadingMirror must call deriveAnalysisLifecycle so scan-store updates feed the aggregate.');
  }
} catch {
  fail('useScanStoreLoadingMirror.ts is missing.');
}

// 10) The orchestrator is gone: the initial `queued` state is now the natural
//     default (absent field ⇒ queued, both in pageLifecycle.resolveLifecycle
//     and deriveAnalysisLifecycle), and the connections-usage kickoff moved
//     into useApiDataLoader. No session-start writer to enforce.

// 11) Strict resolver: pageLifecycle.ts is a one-line read. No deriveLifecycle
//     switch or liftLoadingProgress helper survives the refactor.
if (/deriveLifecycle\b/.test(pageLifecycle)) {
  fail('pageLifecycle.ts must not export deriveLifecycle — the resolver is a one-line ParsedData read.');
}
if (/liftLoadingProgress\b/.test(pageLifecycle)) {
  fail('pageLifecycle.ts must not contain liftLoadingProgress — that lifting belonged to the old derived/static split.');
}

// ─────────────────────────────────────────────────────────────────────────
// Flat-table contract: flat row/column tables must render through the unified
// DataGrid engine (src/components/common/DataGrid.tsx). Any raw <table> in
// src/components/** must be either the engine itself, the transposed key/value
// DataTable, the filesystem DirTreeTable, or one of the explicitly
// structurally-different / grouped / comparison / SSE-card views grandfathered
// below (out of the flat-table migration scope — see docs/ui-ux-contracts.md).
// The 8 migrated flat tables are intentionally NOT on this list, so any
// regression back to a hand-rolled <table> (or a brand-new flat table in a new
// component) fails the build.
const RAW_TABLE_ALLOWLIST = new Set([
  'src/components/common/DataGrid.tsx', // the engine
  'src/components/DataTable.tsx', // transposed key/value view
  'src/components/DirTreeTable.tsx', // filesystem tree
  'src/components/CodeEnvCompareTable.tsx', // code-env comparison sections
  'src/components/CodeEnvsTable.tsx', // grouped code-env view
  'src/components/comparison/ComparisonMemoryAnalysisCard.tsx',
  'src/components/comparison/ComparisonSettingsSection.tsx',
  'src/components/ConnectionHealthCard.tsx', // SSE health result card
  'src/components/ConnectionUsageCard.tsx', // SSE usage result card
  'src/components/ContainerExecs.tsx', // container-execs grouping
  'src/components/DebugPanel.tsx', // key/value debug dump
  'src/components/ImageCleaner.tsx',
  'src/components/InactiveProjectCleaner.tsx',
  'src/components/LocalFilesystemMigrationCard.tsx',
  'src/components/MemoryAnalysisCard.tsx',
  'src/components/MemoryChart.tsx',
  'src/components/pages/CodeEnvsPage.tsx',
  'src/components/pages/DbHealthPage.tsx',
  'src/components/PluginComparator.tsx', // plugin comparison
  'src/components/SanityCheckCard.tsx',
]);

function scanForRawTables(dir, results = []) {
  if (!fs.existsSync(path.join(root, dir))) return results;
  for (const entry of fs.readdirSync(path.join(root, dir), { withFileTypes: true })) {
    const rel = `${dir}/${entry.name}`;
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules' || entry.name === 'tests' || entry.name.startsWith('.')) continue;
      scanForRawTables(rel, results);
      continue;
    }
    if (!/\.(ts|tsx)$/.test(entry.name)) continue;
    if (RAW_TABLE_ALLOWLIST.has(rel)) continue;
    const body = fs.readFileSync(path.join(root, rel), 'utf8');
    if (/<table[\s>]/.test(body)) {
      results.push(rel);
    }
  }
  return results;
}

for (const offender of scanForRawTables('src/components')) {
  fail(`${offender} renders a raw <table> — flat row/column tables must use <DataGrid> (src/components/common/DataGrid.tsx).`);
}

if (process.exitCode) {
  process.exit();
}

console.log(`[contracts] ${pageIds.length} page modules validated`);
