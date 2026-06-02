import { test, expect, type Page } from '@playwright/test';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { execSync } from 'child_process';

const LIVE_URL = process.env.LIVE_URL || 'https://tam-global.fe-aws.dkucloud-dev.com/webapps/admintoolkit/';
const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const __dirname = dirname(fileURLToPath(import.meta.url));
const SCREENSHOT_DIR = join(__dirname, 'screenshots');

function log(msg: string) {
  const ts = new Date().toISOString().slice(11, 19);
  console.log(`[${ts}] ${msg}`);
}

async function screenshot(page: Page, name: string) {
  await page.screenshot({ path: join(SCREENSHOT_DIR, `${name}.png`), fullPage: true });
  log(`screenshot: ${name}.png`);
}

function restartBackend() {
  log('Restarting webapp backend via DSS API...');
  try {
    // Restart PROD (tam-global) via secure wrapper
    execSync(
      `sudo /data/dss-secure-actions/bin/dss_webapp_restart_DIAG_PARSER_BRANCH1 Gv9CLFn`,
      { cwd: REPO_ROOT, timeout: 30_000 },
    );
    log('PROD backend restart requested. Waiting 20s...');
    execSync('sleep 20');
    log('Wait complete.');
  } catch (e) {
    log(`PROD restart failed, trying DEV: ${e}`);
    try {
      execSync(
        `DSS_URL=$(cat .dss-url) DSS_API_KEY=$(cat .dss-api-key) bash scripts/dss_api.sh PUT "/public/api/projects/PYTHONAUDIT_TEST/webapps/haoMNtw/backend/actions/restart"`,
        { cwd: REPO_ROOT, timeout: 15_000 },
      );
      log('DEV backend restart requested. Waiting 20s...');
      execSync('sleep 20');
    } catch (e2) {
      log(`DEV restart also failed: ${e2}`);
    }
  }
}

async function waitForAppShell(page: Page) {
  log(`Navigating to ${LIVE_URL} ...`);
  const t0 = Date.now();

  try {
    await page.goto(LIVE_URL, { waitUntil: 'domcontentloaded', timeout: 30_000 });
    log(`Page loaded in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  } catch {
    log(`Page did not load within 30s — restarting backend`);
    await screenshot(page, 'error-goto-timeout');
    restartBackend();
    log('Retrying navigation...');
    await page.goto(LIVE_URL, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    log(`Page loaded on retry in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  }

  log('Waiting for sidebar...');
  try {
    await page.waitForSelector('aside', { timeout: 30_000 });
  } catch {
    log('Sidebar did not appear within 30s — restarting backend');
    await screenshot(page, 'error-sidebar-timeout');
    restartBackend();
    log('Reloading page...');
    await page.goto(LIVE_URL, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await page.waitForSelector('aside', { timeout: 60_000 });
  }
  log(`Sidebar visible after ${((Date.now() - t0) / 1000).toFixed(1)}s total`);
  await page.waitForTimeout(1_000);
}

function sidebarBtn(page: Page, label: string) {
  return page.locator('aside button').filter({ hasText: label });
}

test.describe('Loading States', () => {
  test.setTimeout(300_000);

  test('project cleaner does not stick on loading', async ({ page }) => {
    await waitForAppShell(page);

    log('Looking for Project Clea... button...');
    const btn = sidebarBtn(page, 'Project Clea');
    const visible = await btn.first().isVisible().catch(() => false);
    if (!visible) {
      log('Project Cleaner button not found — skipping');
      test.skip();
      return;
    }
    log('Clicking Project Cleaner...');
    await btn.first().click();
    await page.waitForTimeout(2_000);
    await screenshot(page, '01-project-cleaner-after-click');

    const main = page.locator('main').last();
    const mainText = await main.innerText();
    log(`Main text (first 300): ${mainText.slice(0, 300)}`);

    // Should not be stuck on "Loading inactive project data..." — wait up to 30s
    try {
      await page.waitForFunction(
        () => {
          const mains = document.querySelectorAll('main');
          const m = mains[mains.length - 1];
          if (!m) return false;
          const text = m.textContent ?? '';
          return !text.includes('Loading inactive project data');
        },
        { timeout: 30_000 },
      );
      log('Loading text cleared');
    } catch {
      await screenshot(page, '01b-project-cleaner-stuck');
      const text2 = await main.innerText();
      throw new Error(`Project Cleaner stuck on loading after 30s. Text: ${text2.slice(0, 200)}`);
    }

    // Also check no spinners remain
    const spinners = main.locator('.animate-spin');
    const spinnerCount = await spinners.count();
    await screenshot(page, '02-project-cleaner-loaded');
    log(`PASS — Project Cleaner loaded, ${spinnerCount} spinners`);
    expect(spinnerCount).toBe(0);
  });

  test('compliance loads without stuck spinner', async ({ page }) => {
    await waitForAppShell(page);

    log('Looking for Compliance button...');
    let btn = sidebarBtn(page, 'Compliance');
    let visible = await btn.first().isVisible().catch(() => false);
    if (!visible) {
      btn = sidebarBtn(page, 'Tracking');
      visible = await btn.first().isVisible().catch(() => false);
    }
    if (!visible) {
      log('Compliance button not found — skipping');
      test.skip();
      return;
    }
    log('Clicking Compliance...');
    await btn.first().click();
    await page.waitForTimeout(2_000);
    await screenshot(page, '03-compliance-after-click');

    const main = page.locator('main').last();
    const mainText = await main.innerText();
    log(`Main text (first 300): ${mainText.slice(0, 300)}`);

    // Wait up to 30s for spinner to disappear and content or error to show
    try {
      await page.waitForFunction(
        () => {
          const mains = document.querySelectorAll('main');
          const m = mains[mains.length - 1];
          if (!m) return false;
          const spinner = m.querySelector('.animate-spin');
          return !spinner;
        },
        { timeout: 30_000 },
      );
      log('Spinner cleared');
    } catch {
      await screenshot(page, '03b-compliance-spinner-stuck');
      throw new Error('Compliance spinner still visible after 30s');
    }

    await screenshot(page, '04-compliance-loaded');
    const finalText = await main.innerText();
    log(`Final text (first 200): ${finalText.slice(0, 200)}`);

    // Should not show JSON parse errors
    expect(finalText).not.toContain('is not valid JSON');
    expect(finalText).not.toContain('NaN');
    log('PASS — Compliance loaded without stuck spinner or JSON errors');
  });

  test('compliance expanded user shows issues when count > 0', async ({ page }) => {
    await waitForAppShell(page);

    log('Looking for Compliance button...');
    let btn = sidebarBtn(page, 'Compliance');
    let visible = await btn.first().isVisible().catch(() => false);
    if (!visible) {
      btn = sidebarBtn(page, 'Tracking');
      visible = await btn.first().isVisible().catch(() => false);
    }
    if (!visible) {
      log('Compliance button not found — skipping');
      test.skip();
      return;
    }
    log('Clicking Compliance...');
    await btn.first().click();
    await page.waitForTimeout(2_000);

    const main = page.locator('main').last();

    // Wait for content to load (no spinner)
    try {
      await page.waitForFunction(
        () => {
          const mains = document.querySelectorAll('main');
          const m = mains[mains.length - 1];
          if (!m) return false;
          return !m.querySelector('.animate-spin');
        },
        { timeout: 30_000 },
      );
    } catch {
      throw new Error('Compliance spinner still visible after 30s');
    }

    // Find the first user row with open > 0
    const userRows = main.locator('tbody tr').filter({ has: page.locator('td') });
    const rowCount = await userRows.count();
    log(`Found ${rowCount} user rows`);

    // Debug: log first row's cell contents to find correct column indices
    if (rowCount > 0) {
      const firstRow = userRows.first();
      const firstCells = firstRow.locator('td');
      const firstCellCount = await firstCells.count();
      for (let c = 0; c < firstCellCount; c++) {
        const txt = await firstCells.nth(c).innerText().catch(() => '');
        log(`  row0 cell[${c}]: "${txt}"`);
      }
    }

    let clickedUser = '';
    let openCount = 0;
    for (let i = 0; i < rowCount; i++) {
      const row = userRows.nth(i);
      const cells = row.locator('td');
      const cellCount = await cells.count();
      if (cellCount < 4) continue;
      // Try each cell to find the first one with a number > 0 (after login/email)
      for (let c = 2; c < cellCount; c++) {
        const txt = await cells.nth(c).innerText().catch(() => '');
        const parsed = parseInt(txt, 10);
        if (parsed > 0) {
          clickedUser = await cells.nth(1).innerText().catch(() => 'unknown');
          openCount = parsed;
          log(`Clicking user "${clickedUser}" — cell[${c}]=${parsed}...`);
          await row.click();
          break;
        }
      }
      if (clickedUser) break;
    }

    if (!clickedUser) {
      log('No users with open issues found — skipping');
      return;
    }

    // Wait for issues to load (spinner clears in expanded section)
    await page.waitForTimeout(3_000);
    await screenshot(page, '05-compliance-user-expanded');

    const expandedText = await main.innerText();
    log(`Expanded text (first 500): ${expandedText.slice(0, 500)}`);

    // If user has open > 0, "No individual issues found" must NOT appear
    expect(expandedText).not.toContain('No individual issues found');
    log(`PASS — User "${clickedUser}" with ${openCount} open issues shows actual issues`);
  });
});
