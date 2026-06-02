import fs from 'node:fs';
import path from 'node:path';
import { test, expect, type Page } from '@playwright/test';

const LIVE_URL = process.env.LIVE_WEBAPP_URL || '';
const ENABLE_TEST = process.env.ENABLE_LIVE_WEBAPP_TEST === '1';
const SNAPSHOT_INTERVAL_MS = Math.max(1000, Number(process.env.LIVE_SHOT_INTERVAL_MS || 25000));
const SAMPLE_INTERVAL_MS = Math.max(1000, Number(process.env.LIVE_SAMPLE_INTERVAL_MS || 5000));
const PAGE_SETTLE_MS = Math.max(250, Number(process.env.LIVE_PAGE_SETTLE_MS || 1200));
const MAX_DURATION_SEC = Math.max(30, Number(process.env.LIVE_MAX_DURATION_SEC || 900));
const MAX_DURATION_MS = MAX_DURATION_SEC * 1000;
const DEBUG_PANEL_SELECTOR = '.max-h-56.overflow-y-auto';

type TargetPageId = 'code-envs' | 'code-env-cleaner' | 'project-cleaner';

interface TargetPage {
  id: TargetPageId;
  label: string;
  navLabels: string[];
  screenshotStem: string;
}

interface DebugFacts {
  hasLiveDataLoadCompleted: boolean;
  hasLoaderFinalized: boolean;
  loadedCodeEnvsCount: number | null;
  loadedProjectFootprintCount: number | null;
  ceUsageCheckIndex: number | null;
  ceUsageCheckTotal: number | null;
  pjftUsageCheckIndex: number | null;
  pjftUsageCheckTotal: number | null;
  codeEnvsEndpointMs: number | null;
  projectFootprintEndpointMs: number | null;
  dirTreeEndpointMs: number | null;
}

interface PageState {
  visible: boolean;
  tableVisible: boolean;
  rowCount: number;
  placeholderText: string | null;
  loadingText: string | null;
  emptyText: string | null;
  errorText: string | null;
  loadedCount: number | null;
  totalCount: number | null;
  provisionalCount: number | null;
  headingText: string | null;
}

interface SnapshotRecord {
  atMs: number;
  atIso: string;
  pageId: TargetPageId;
  pageLabel: string;
  screenshotPath: string | null;
  debugDumpPath: string | null;
  state: PageState;
  debugFacts: DebugFacts;
}

interface PageSummary {
  pageId: TargetPageId;
  pageLabel: string;
  firstVisibleAtMs: number | null;
  firstRowAtMs: number | null;
  fullyPopulatedAtMs: number | null;
  finalRowCount: number;
  finalLoadedCount: number | null;
  finalTotalCount: number | null;
  lastPlaceholderText: string | null;
  longestFlatWhileDebugAdvancedMs: number;
}

const TARGET_PAGES: TargetPage[] = [
  {
    id: 'code-envs',
    label: 'Code Envs',
    navLabels: ['Code Envs'],
    screenshotStem: 'code-envs',
  },
  {
    id: 'code-env-cleaner',
    label: 'Code Env Cleaner',
    navLabels: ['CodEnv Cleaner', 'Code Env Cleaner'],
    screenshotStem: 'code-env-cleaner',
  },
  {
    id: 'project-cleaner',
    label: 'Project Cleaner',
    navLabels: ['Project Cleaner'],
    screenshotStem: 'project-cleaner',
  },
];

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function toNumber(value: string | null | undefined): number | null {
  if (!value) return null;
  const normalized = value.replace(/,/g, '').trim();
  const parsed = Number.parseInt(normalized, 10);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatMs(ms: number | null): string {
  if (ms == null) return 'n/a';
  return `${(ms / 1000).toFixed(1)}s`;
}

async function ensureDebugPanelOpen(page: Page): Promise<void> {
  const panel = page.locator(DEBUG_PANEL_SELECTOR).first();
  if (await panel.count()) return;

  await page.keyboard.press('d');
  await page.waitForTimeout(250);

  const showDebug = page.getByRole('button', { name: /Show Debug/i }).first();
  if (await showDebug.count()) {
    await showDebug.click({ timeout: 3000 }).catch(() => undefined);
  }

  await expect(page.locator(DEBUG_PANEL_SELECTOR).first()).toBeVisible({ timeout: 10000 });
}

async function readDebugPanelText(page: Page): Promise<string> {
  await ensureDebugPanelOpen(page);
  return page.evaluate((selector) => {
    const element = document.querySelector(selector);
    return (element?.textContent || '').trim();
  }, DEBUG_PANEL_SELECTOR);
}

async function navigateToPage(page: Page, target: TargetPage): Promise<void> {
  for (const label of target.navLabels) {
    const regex = new RegExp(`^${escapeRegExp(label)}$`, 'i');
    const candidates = [
      page.getByRole('button', { name: regex }).first(),
      page.locator('aside button').filter({ hasText: label }).first(),
      page.locator('button').filter({ hasText: label }).first(),
    ];

    for (const candidate of candidates) {
      if ((await candidate.count()) === 0) continue;
      try {
        await candidate.click({ timeout: 4000 });
        await page.waitForTimeout(PAGE_SETTLE_MS);
        const state = await readPageState(page, target.id);
        if (state.visible) return;
      } catch {
        // try next candidate
      }
    }
  }
  throw new Error(`Could not navigate to ${target.label}`);
}

async function waitForPageReady(page: Page, target: TargetPage): Promise<PageState> {
  let state = await readPageState(page, target.id);
  if (target.id !== 'project-cleaner') return state;

  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    if (state.rowCount > 0 || state.emptyText || state.errorText) {
      return state;
    }
    await page.waitForTimeout(500);
    state = await readPageState(page, target.id);
  }
  return state;
}

async function readPageState(page: Page, targetId: TargetPageId): Promise<PageState> {
  return page.evaluate((pageId: TargetPageId) => {
    const parseIntSafe = (value: string | null | undefined) => {
      if (!value) return null;
      const normalized = value.replace(/,/g, '').trim();
      const parsed = Number.parseInt(normalized, 10);
      return Number.isFinite(parsed) ? parsed : null;
    };
    const text = (node: Element | null | undefined) => (node?.textContent || '').trim() || null;
    const headingMatches = (selector: string, pattern: RegExp) =>
      Array.from(document.querySelectorAll(selector)).find((node) => pattern.test((node.textContent || '').trim()));
    const countRows = (table: HTMLTableElement | null) => {
      if (!table) return { rowCount: 0, placeholderText: null as string | null };
      const rows = Array.from(table.querySelectorAll('tbody tr'));
      let rowCount = 0;
      let placeholderText: string | null = null;
      rows.forEach((row) => {
        const cells = Array.from(row.querySelectorAll('td'));
        if (cells.length === 1 && cells[0]?.hasAttribute('colspan')) {
          placeholderText = (cells[0].textContent || '').trim() || null;
          return;
        }
        rowCount += 1;
      });
      return { rowCount, placeholderText };
    };
    const findTable = (requiredHeaders: string[]) => {
      const tables = Array.from(document.querySelectorAll('table')) as HTMLTableElement[];
      return (
        tables.find((table) => {
          const headers = Array.from(table.querySelectorAll('th')).map((th) => (th.textContent || '').trim().toLowerCase());
          return requiredHeaders.every((header) => headers.includes(header.toLowerCase()));
        }) || null
      );
    };
    const findStatValue = (labels: string[]) => {
      const labelNode = Array.from(document.querySelectorAll('div, span, p')).find((node) => {
        const nodeText = (node.textContent || '').trim();
        return labels.includes(nodeText);
      });
      const valueText = text(labelNode?.previousElementSibling || null);
      return valueText;
    };

    if (pageId === 'code-envs') {
      const heading = headingMatches('h3, h4', /(^|\b)Code Envs(\b|$)/i);
      const table = findTable(['Name', 'Owner', 'Version', 'Language']);
      const { rowCount, placeholderText } = countRows(table);
      const bodyText = document.body.innerText || '';
      return {
        visible: Boolean(heading || table),
        tableVisible: Boolean(table),
        rowCount,
        placeholderText,
        loadingText: /Waiting for code environment data/i.test(bodyText)
          ? 'Waiting for code environment data...'
          : null,
        emptyText: null,
        errorText: null,
        loadedCount: rowCount,
        totalCount: null,
        provisionalCount: null,
        headingText: text(heading),
      };
    }

    if (pageId === 'code-env-cleaner') {
      const heading = headingMatches('h3', /Code Env Cleaner/i);
      const table = findTable(['Name', 'Language', 'Owner', 'Usages', 'Actions']);
      const { rowCount, placeholderText } = countRows(table);
      const loadedText = findStatValue(['Loaded / Total', 'Total']);
      const loadedMatch = loadedText?.match(/(\d+)\s*\/\s*(\d+)/);
      const provisionalText = Array.from(document.querySelectorAll('p')).find((node) =>
        /Showing\s+\d+\s+provisional row/i.test((node.textContent || '').trim()),
      );
      const provisionalMatch = text(provisionalText)?.match(/Showing\s+(\d+)\s+provisional row/i);
      const progressText = Array.from(document.querySelectorAll('span, div, p')).find((node) =>
        /Analyzing code environments|Code env analysis:/i.test((node.textContent || '').trim()),
      );
      const emptyText = Array.from(document.querySelectorAll('p, td')).find((node) =>
        /No code environments available yet/i.test((node.textContent || '').trim()),
      );
      return {
        visible: Boolean(heading || table),
        tableVisible: Boolean(table),
        rowCount,
        placeholderText,
        loadingText: text(progressText),
        emptyText: text(emptyText),
        errorText: null,
        loadedCount: loadedMatch ? parseIntSafe(loadedMatch[1]) : parseIntSafe(loadedText),
        totalCount: loadedMatch ? parseIntSafe(loadedMatch[2]) : null,
        provisionalCount: provisionalMatch ? parseIntSafe(provisionalMatch[1]) : 0,
        headingText: text(heading),
      };
    }

    const heading = headingMatches('h3', /Inactive Project Cleaner/i);
    const table = findTable(['Project Name', 'Owner', 'Days Inactive', 'Actions']);
    const { rowCount, placeholderText } = countRows(table);
    const loadingText = Array.from(document.querySelectorAll('p')).find((node) =>
      /Loading inactive project data/i.test((node.textContent || '').trim()),
    );
    const emptyText = Array.from(document.querySelectorAll('p')).find((node) =>
      /No inactive projects found/i.test((node.textContent || '').trim()),
    );
    const errorText = Array.from(document.querySelectorAll('p')).find((node) =>
      /Failed to load inactive projects/i.test((node.textContent || '').trim()),
    );
    return {
      visible: Boolean(heading || table || loadingText || emptyText || errorText),
      tableVisible: Boolean(table),
      rowCount,
      placeholderText,
      loadingText: text(loadingText),
      emptyText: text(emptyText),
      errorText: text(errorText),
      loadedCount: rowCount,
      totalCount: null,
      provisionalCount: null,
      headingText: text(heading),
    };
  }, targetId);
}

function parseDebugFacts(debugText: string): DebugFacts {
  const lastNumber = (regex: RegExp): number | null => {
    const matches = Array.from(debugText.matchAll(regex));
    if (matches.length === 0) return null;
    return toNumber(matches[matches.length - 1]?.[1]);
  };
  const lastPair = (regex: RegExp): { index: number | null; total: number | null } => {
    const matches = Array.from(debugText.matchAll(regex));
    if (matches.length === 0) return { index: null, total: null };
    const last = matches[matches.length - 1];
    return {
      index: toNumber(last?.[1]),
      total: toNumber(last?.[2]),
    };
  };

  const cePair = lastPair(/bench;ce;[^\n]*?code_env_usage_check;\[(\d+)\/(\d+)\]/g);
  const pjftPair = lastPair(/bench;pjft;[^\n]*?code_env_usage_check;\[(\d+)\/(\d+)\]/g);

  return {
    hasLiveDataLoadCompleted: /Live data load completed/.test(debugText),
    hasLoaderFinalized: /Loader finalized/.test(debugText),
    loadedCodeEnvsCount: lastNumber(/Loaded code envs \((\d+)\)/g),
    loadedProjectFootprintCount: lastNumber(/Loaded project footprint \((\d+) projects\)/g),
    ceUsageCheckIndex: cePair.index,
    ceUsageCheckTotal: cePair.total,
    pjftUsageCheckIndex: pjftPair.index,
    pjftUsageCheckTotal: pjftPair.total,
    codeEnvsEndpointMs: lastNumber(/GET \/api\/code-envs OK \((\d+)ms\)/g),
    projectFootprintEndpointMs: lastNumber(/GET \/api\/project-footprint OK \((\d+)ms\)/g),
    dirTreeEndpointMs: lastNumber(/GET \/api\/dir-tree\?maxDepth=3&scope=dss OK \((\d+)ms\)/g),
  };
}

function pageDone(pageId: TargetPageId, state: PageState, facts: DebugFacts): boolean {
  switch (pageId) {
    case 'code-envs':
      return Boolean(facts.loadedCodeEnvsCount && state.rowCount >= facts.loadedCodeEnvsCount);
    case 'code-env-cleaner':
      if (state.totalCount != null && state.loadedCount != null) {
        return state.loadedCount >= state.totalCount && state.totalCount > 0;
      }
      return Boolean(
        facts.loadedCodeEnvsCount &&
          state.rowCount >= facts.loadedCodeEnvsCount &&
          (state.provisionalCount || 0) === 0,
      );
    case 'project-cleaner':
      return Boolean(state.rowCount > 0 || state.emptyText || state.errorText);
    default:
      return false;
  }
}

function buildPageSummary(target: TargetPage, snapshots: SnapshotRecord[]): PageSummary {
  const pageSnapshots = snapshots.filter((snapshot) => snapshot.pageId === target.id);
  let firstVisibleAtMs: number | null = null;
  let firstRowAtMs: number | null = null;
  let fullyPopulatedAtMs: number | null = null;
  let longestFlatWhileDebugAdvancedMs = 0;
  let flatStartAtMs: number | null = null;

  for (let i = 0; i < pageSnapshots.length; i += 1) {
    const current = pageSnapshots[i];
    const prev = i > 0 ? pageSnapshots[i - 1] : null;
    if (firstVisibleAtMs == null && current.state.visible) firstVisibleAtMs = current.atMs;
    if (firstRowAtMs == null && current.state.rowCount > 0) firstRowAtMs = current.atMs;
    if (fullyPopulatedAtMs == null && pageDone(target.id, current.state, current.debugFacts)) {
      fullyPopulatedAtMs = current.atMs;
    }

    if (prev) {
      const debugAdvanced =
        target.id === 'project-cleaner'
          ? false
          : (current.debugFacts.ceUsageCheckIndex || 0) > (prev.debugFacts.ceUsageCheckIndex || 0);
      const rowFlat = current.state.rowCount === prev.state.rowCount;
      if (debugAdvanced && rowFlat) {
        if (flatStartAtMs == null) flatStartAtMs = prev.atMs;
        longestFlatWhileDebugAdvancedMs = Math.max(
          longestFlatWhileDebugAdvancedMs,
          current.atMs - flatStartAtMs,
        );
      } else {
        flatStartAtMs = null;
      }
    }
  }

  const last = pageSnapshots[pageSnapshots.length - 1];
  const maxRowCount = pageSnapshots.reduce((max, snapshot) => Math.max(max, snapshot.state.rowCount), 0);
  const maxLoadedCount = pageSnapshots.reduce((max, snapshot) => {
    const next = snapshot.state.loadedCount ?? 0;
    return Math.max(max, next);
  }, 0);
  const maxTotalCount = pageSnapshots.reduce((max, snapshot) => {
    const next = snapshot.state.totalCount ?? 0;
    return Math.max(max, next);
  }, 0);
  return {
    pageId: target.id,
    pageLabel: target.label,
    firstVisibleAtMs,
    firstRowAtMs,
    fullyPopulatedAtMs,
    finalRowCount: maxRowCount,
    finalLoadedCount: maxLoadedCount > 0 ? maxLoadedCount : last?.state.loadedCount ?? null,
    finalTotalCount: maxTotalCount > 0 ? maxTotalCount : last?.state.totalCount ?? null,
    lastPlaceholderText: last?.state.placeholderText ?? null,
    longestFlatWhileDebugAdvancedMs,
  };
}

function renderConclusion(summary: {
  startedAtIso: string;
  finishedAtIso: string;
  totalDurationMs: number;
  stopReason: string;
  firstDebugUsageCheckAtMs: number | null;
  pageSummaries: PageSummary[];
  finalDebugFacts: DebugFacts;
}): string {
  const page = (id: TargetPageId) => summary.pageSummaries.find((item) => item.pageId === id) || null;
  const codeEnvs = page('code-envs');
  const cleaner = page('code-env-cleaner');
  const projectCleaner = page('project-cleaner');
  const cleanerLag =
    summary.firstDebugUsageCheckAtMs != null && cleaner?.firstRowAtMs != null
      ? cleaner.firstRowAtMs - summary.firstDebugUsageCheckAtMs
      : null;

  const lines = [
    '# Live Webapp Profiling Conclusion',
    '',
    `- Started: ${summary.startedAtIso}`,
    `- Finished: ${summary.finishedAtIso}`,
    `- Stop reason: ${summary.stopReason}`,
    `- Total duration: ${formatMs(summary.totalDurationMs)}`,
    '',
    '## Table Timings',
    `- Code Envs fully populated: ${formatMs(codeEnvs?.fullyPopulatedAtMs ?? null)}`,
    `- Code Env Cleaner fully populated: ${formatMs(cleaner?.fullyPopulatedAtMs ?? null)}`,
    `- Project Cleaner fully populated: ${formatMs(projectCleaner?.fullyPopulatedAtMs ?? null)}`,
    '',
    '## Observations',
  ];

  if (cleanerLag != null) {
    lines.push(`- Code Env Cleaner first visible row lag from first debug usage-check line: ${formatMs(cleanerLag)}.`);
  } else {
    lines.push('- Could not compute cleaner row lag from debug usage-check visibility.');
  }

  if ((cleaner?.longestFlatWhileDebugAdvancedMs || 0) >= SNAPSHOT_INTERVAL_MS) {
    lines.push(
      `- Cleaner row count stayed flat for ${formatMs(cleaner?.longestFlatWhileDebugAdvancedMs || 0)} while debug usage-check progress still advanced.`,
    );
  }

  if (summary.finalDebugFacts.codeEnvsEndpointMs != null) {
    lines.push(`- /api/code-envs completed in ${formatMs(summary.finalDebugFacts.codeEnvsEndpointMs)}.`);
  }
  if (summary.finalDebugFacts.projectFootprintEndpointMs != null) {
    lines.push(`- /api/project-footprint completed in ${formatMs(summary.finalDebugFacts.projectFootprintEndpointMs)}.`);
  }
  if (summary.finalDebugFacts.dirTreeEndpointMs != null) {
    lines.push(`- /api/dir-tree completed in ${formatMs(summary.finalDebugFacts.dirTreeEndpointMs)}.`);
  }

  if (
    summary.finalDebugFacts.codeEnvsEndpointMs != null &&
    summary.finalDebugFacts.projectFootprintEndpointMs != null &&
    summary.finalDebugFacts.codeEnvsEndpointMs > summary.finalDebugFacts.projectFootprintEndpointMs
  ) {
    lines.push('- The code-env pipeline remained the slowest heavy endpoint in this run.');
  }

  return `${lines.join('\n')}\n`;
}

test.describe('Live Webapp Timelapse', () => {
  test('profiles Code Envs, Code Env Cleaner, and Project Cleaner until live processing completes', async ({ page }, testInfo) => {
    test.skip(!ENABLE_TEST, 'Set ENABLE_LIVE_WEBAPP_TEST=1 to run this live external capture');
    test.skip(!LIVE_URL, 'Set LIVE_WEBAPP_URL to the target webapp URL');

    test.setTimeout(Math.max(240000, MAX_DURATION_MS + 180000));

    const startedAt = Date.now();
    const stamp = new Date(startedAt).toISOString().replace(/[:.]/g, '-');
    const outDir = testInfo.outputPath(`live-webapp-timelapse-${stamp}`);
    const snapshotsDir = path.join(outDir, 'snapshots');
    fs.mkdirSync(snapshotsDir, { recursive: true });

    await page.goto(LIVE_URL, { waitUntil: 'domcontentloaded', timeout: 120000 });
    await page.waitForTimeout(1500);
    await ensureDebugPanelOpen(page);

    const records: SnapshotRecord[] = [];
    let nextSnapshotAt = startedAt;
    let snapshotBatch = 0;
    let stopReason = 'timeout';
    let firstDebugUsageCheckAtMs: number | null = null;

    const capturePass = async (captureArtifacts: boolean) => {
      if (captureArtifacts) snapshotBatch += 1;
      for (const target of TARGET_PAGES) {
        await navigateToPage(page, target);
        await ensureDebugPanelOpen(page);
        await page.waitForTimeout(PAGE_SETTLE_MS);

        const state = await waitForPageReady(page, target);
        const debugText = await readDebugPanelText(page);
        const debugFacts = parseDebugFacts(debugText);
        const atMs = Date.now() - startedAt;
        const atIso = new Date(startedAt + atMs).toISOString();

        if (firstDebugUsageCheckAtMs == null && (debugFacts.ceUsageCheckIndex || 0) > 0) {
          firstDebugUsageCheckAtMs = atMs;
        }

        let screenshotPath: string | null = null;
        let debugDumpPath: string | null = null;
        if (captureArtifacts) {
          const prefix = `${String(snapshotBatch).padStart(3, '0')}-${target.screenshotStem}`;
          screenshotPath = path.join(snapshotsDir, `${prefix}.png`);
          debugDumpPath = path.join(snapshotsDir, `${prefix}.debug.txt`);
          await page.screenshot({ path: screenshotPath, fullPage: true });
          fs.writeFileSync(debugDumpPath, `${debugText}\n`, 'utf8');
        }

        records.push({
          atMs,
          atIso,
          pageId: target.id,
          pageLabel: target.label,
          screenshotPath: screenshotPath ? path.relative(outDir, screenshotPath) : null,
          debugDumpPath: debugDumpPath ? path.relative(outDir, debugDumpPath) : null,
          state,
          debugFacts,
        });
      }
    };

    while (Date.now() - startedAt <= MAX_DURATION_MS) {
      const cycleStartedAt = Date.now();
      const shouldCapture = records.length === 0 || cycleStartedAt >= nextSnapshotAt;
      await capturePass(shouldCapture);
      if (shouldCapture) {
        nextSnapshotAt = cycleStartedAt + SNAPSHOT_INTERVAL_MS;
      }

      const finalDebugFacts = records[records.length - 1]?.debugFacts;
      const allPagesDone = TARGET_PAGES.every((target) =>
        records.some((record) => record.pageId === target.id && pageDone(target.id, record.state, record.debugFacts)),
      );
      if (allPagesDone && finalDebugFacts?.hasLiveDataLoadCompleted && finalDebugFacts?.hasLoaderFinalized) {
        stopReason = 'complete';
        break;
      }

      const cycleElapsedMs = Date.now() - cycleStartedAt;
      const sleepMs = Math.max(0, SAMPLE_INTERVAL_MS - cycleElapsedMs);
      if (sleepMs > 0) await page.waitForTimeout(sleepMs);
    }

    const lastRecord = records[records.length - 1];
    if (lastRecord && !lastRecord.screenshotPath) {
      await capturePass(true);
    }

    const finalRecords = [...records];
    const finalDebugFacts = finalRecords[finalRecords.length - 1]?.debugFacts || {
      hasLiveDataLoadCompleted: false,
      hasLoaderFinalized: false,
      loadedCodeEnvsCount: null,
      loadedProjectFootprintCount: null,
      ceUsageCheckIndex: null,
      ceUsageCheckTotal: null,
      pjftUsageCheckIndex: null,
      pjftUsageCheckTotal: null,
      codeEnvsEndpointMs: null,
      projectFootprintEndpointMs: null,
      dirTreeEndpointMs: null,
    };
    const finishedAt = Date.now();
    const pageSummaries = TARGET_PAGES.map((target) => buildPageSummary(target, finalRecords));

    const timeline = {
      liveUrl: LIVE_URL,
      startedAtIso: new Date(startedAt).toISOString(),
      finishedAtIso: new Date(finishedAt).toISOString(),
      snapshotIntervalMs: SNAPSHOT_INTERVAL_MS,
      sampleIntervalMs: SAMPLE_INTERVAL_MS,
      pageSettleMs: PAGE_SETTLE_MS,
      maxDurationMs: MAX_DURATION_MS,
      stopReason,
      records: finalRecords,
    };
    const summary = {
      liveUrl: LIVE_URL,
      startedAtIso: new Date(startedAt).toISOString(),
      finishedAtIso: new Date(finishedAt).toISOString(),
      totalDurationMs: finishedAt - startedAt,
      snapshotIntervalMs: SNAPSHOT_INTERVAL_MS,
      sampleIntervalMs: SAMPLE_INTERVAL_MS,
      stopReason,
      firstDebugUsageCheckAtMs,
      pageSummaries,
      finalDebugFacts,
    };

    fs.writeFileSync(path.join(outDir, 'timeline.json'), JSON.stringify(timeline, null, 2), 'utf8');
    fs.writeFileSync(path.join(outDir, 'summary.json'), JSON.stringify(summary, null, 2), 'utf8');
    fs.writeFileSync(path.join(outDir, 'conclusion.md'), renderConclusion(summary), 'utf8');

    expect(finalRecords.length).toBeGreaterThan(0);
    for (const pageSummary of pageSummaries) {
      expect(pageSummary.firstVisibleAtMs, `${pageSummary.pageLabel} never became visible`).not.toBeNull();
      expect(pageSummary.fullyPopulatedAtMs, `${pageSummary.pageLabel} never fully populated`).not.toBeNull();
    }
    expect(finalDebugFacts.hasLiveDataLoadCompleted, 'Did not observe "Live data load completed" in debug panel').toBeTruthy();
    expect(finalDebugFacts.hasLoaderFinalized, 'Did not observe "Loader finalized" in debug panel').toBeTruthy();
  });
});
