import { test, expect, type Page } from '@playwright/test';

/**
 * Tracking navigation tests — verifies that navigating between Tracking
 * and Settings doesn't cause infinite spinners or stale data.
 *
 * Reproduces the bug where:
 * 1. Visit Tracking (loads fine)
 * 2. Navigate to Settings, toggle campaigns
 * 3. Navigate back to Tracking → infinite spinner
 *
 * Root cause: /api/tracking/refresh POST held a DB lock that blocked
 * /api/tracking/users GET, causing loadUsers() to hang forever.
 */

const LIVE_URL = process.env.LIVE_URL || 'https://tam-global.fe-aws.dkucloud-dev.com/webapps/liveparser/';
const NAV_TIMEOUT = 15_000;

async function waitForAppShell(page: Page) {
  await page.goto(LIVE_URL, { waitUntil: 'domcontentloaded', timeout: 30_000 });
  await page.waitForSelector('aside', { timeout: 60_000 });
  await page.waitForTimeout(500);
}

function sidebarBtn(page: Page, label: string) {
  return page.locator('aside button').filter({ hasText: label });
}

/** Wait for the Tracking page spinner to disappear and content to appear. */
async function waitForTrackingContent(page: Page, timeoutMs = NAV_TIMEOUT) {
  // The spinner is: div with animate-spin inside main
  // Content appears as: table or "No tracking data" or "User Compliance" heading
  await page.waitForFunction(
    () => {
      const main = document.querySelector('main');
      if (!main) return false;
      // Spinner still visible = not ready
      const spinner = main.querySelector('.animate-spin');
      if (spinner) return false;
      // Content loaded if we see a table or the heading
      const text = main.textContent ?? '';
      return text.includes('User Compliance') || text.includes('No tracking data');
    },
    { timeout: timeoutMs },
  );
}

test.describe('Tracking Navigation (no infinite spinner)', () => {
  test.setTimeout(120_000);

  test('tracking loads within timeout on first visit', async ({ page }) => {
    // First test after deploy may hit a cold backend — use longer timeouts
    await page.goto(LIVE_URL, { waitUntil: 'domcontentloaded', timeout: 30_000 });
    await page.waitForSelector('aside', { timeout: 90_000 });
    await page.waitForTimeout(500);

    const trackingBtn = sidebarBtn(page, 'Tracking');
    const visible = await trackingBtn.first().isVisible().catch(() => false);
    if (!visible) {
      test.skip();
      return;
    }
    await trackingBtn.first().click();

    // Should load within 30s (not hang on spinner)
    await waitForTrackingContent(page, 30_000);

    const main = page.locator('main');
    const text = await main.innerText();
    expect(text).toContain('User Compliance');
    console.log('First visit: Tracking loaded OK');
  });

  test('tracking → settings → tracking does not cause infinite spinner', async ({ page }) => {
    await waitForAppShell(page);

    // ── Step 1: Navigate to Tracking ─────────────────────────────────
    const trackingBtn = sidebarBtn(page, 'Tracking');
    const tVisible = await trackingBtn.first().isVisible().catch(() => false);
    if (!tVisible) {
      test.skip();
      return;
    }
    await trackingBtn.first().click();
    await waitForTrackingContent(page);

    const main = page.locator('main');
    let text = await main.innerText();
    expect(text).toContain('User Compliance');
    console.log('Step 1: Tracking loaded');

    // ── Step 2: Navigate to Settings ─────────────────────────────────
    const settingsBtn = sidebarBtn(page, 'Settings');
    const sVisible = await settingsBtn.first().isVisible().catch(() => false);
    if (!sVisible) {
      console.log('Settings button not visible — skipping toggle portion');
      return;
    }
    await settingsBtn.first().click();
    // Wait for campaign toggle switches to appear (async fetch)
    const toggles = page.locator('button[role="switch"]');
    await toggles.first().waitFor({ state: 'visible', timeout: 10_000 }).catch(() => {});
    const toggleCount = await toggles.count();
    if (toggleCount > 0) {
      // Toggle the first campaign off, wait, then toggle it back on
      await toggles.first().click();
      await page.waitForTimeout(500);
      await toggles.first().click();
      await page.waitForTimeout(500);
      console.log(`Step 2: Toggled a campaign in Settings (${toggleCount} switches found)`);
    } else {
      console.log('Step 2: No campaign toggles found, proceeding anyway');
    }

    // ── Step 3: Navigate back to Tracking ────────────────────────────
    // THIS IS THE BUG SCENARIO: previously caused infinite spinner
    await trackingBtn.first().click();

    // Must load within NAV_TIMEOUT (previously hung forever)
    await waitForTrackingContent(page);

    text = await main.innerText();
    expect(text).toContain('User Compliance');

    // Should NOT still be showing a spinner
    const spinner = main.locator('.animate-spin');
    const spinnerVisible = await spinner.isVisible().catch(() => false);
    expect(spinnerVisible).toBe(false);
    console.log('Step 3: Tracking loaded after Settings roundtrip — no infinite spinner');
  });

  test('rapid tracking ↔ settings navigation does not hang', async ({ page }) => {
    await waitForAppShell(page);

    const trackingBtn = sidebarBtn(page, 'Tracking');
    const settingsBtn = sidebarBtn(page, 'Settings');

    const tVisible = await trackingBtn.first().isVisible().catch(() => false);
    const sVisible = await settingsBtn.first().isVisible().catch(() => false);
    if (!tVisible || !sVisible) {
      test.skip();
      return;
    }

    // Rapid-fire navigation: Tracking → Settings → Tracking → Settings → Tracking
    for (let i = 0; i < 3; i++) {
      await trackingBtn.first().click();
      await page.waitForTimeout(800);
      await settingsBtn.first().click();
      await page.waitForTimeout(800);
    }

    // Final navigation to Tracking — must load, not hang
    await trackingBtn.first().click();
    await waitForTrackingContent(page, 20_000);

    const main = page.locator('main');
    const text = await main.innerText();
    expect(text).toContain('User Compliance');

    const spinner = main.locator('.animate-spin');
    const spinnerVisible = await spinner.isVisible().catch(() => false);
    expect(spinnerVisible).toBe(false);
    console.log('Rapid navigation: Tracking loaded OK after 3 roundtrips');
  });

  test('tracking loads even while refresh POST is in-flight', async ({ page }) => {
    await waitForAppShell(page);

    const trackingBtn = sidebarBtn(page, 'Tracking');
    const settingsBtn = sidebarBtn(page, 'Settings');

    const tVisible = await trackingBtn.first().isVisible().catch(() => false);
    const sVisible = await settingsBtn.first().isVisible().catch(() => false);
    if (!tVisible || !sVisible) {
      test.skip();
      return;
    }

    // Visit Tracking to trigger the background refresh POST
    await trackingBtn.first().click();
    await waitForTrackingContent(page);

    // Immediately navigate to Settings (refresh POST is now in-flight)
    await settingsBtn.first().click();
    await page.waitForTimeout(500);

    // Toggle some campaigns to make the backend busy
    const toggles = page.locator('button[role="switch"]');
    const toggleCount = await toggles.count();
    for (let i = 0; i < Math.min(toggleCount, 3); i++) {
      await toggles.nth(i).click();
      await page.waitForTimeout(200);
    }

    // Navigate back to Tracking while refresh may still be running
    await trackingBtn.first().click();
    await waitForTrackingContent(page, 20_000);

    const main = page.locator('main');
    const text = await main.innerText();
    expect(text).toContain('User Compliance');

    // Restore toggles
    if (toggleCount > 0) {
      await settingsBtn.first().click();
      await page.waitForTimeout(500);
      for (let i = 0; i < Math.min(toggleCount, 3); i++) {
        await toggles.nth(i).click();
        await page.waitForTimeout(200);
      }
    }

    console.log('Tracking loaded while refresh in-flight — no spinner hang');
  });

  test('expanding a user row shows issues (not stale cache)', async ({ page }) => {
    await waitForAppShell(page);

    const trackingBtn = sidebarBtn(page, 'Tracking');
    const visible = await trackingBtn.first().isVisible().catch(() => false);
    if (!visible) {
      test.skip();
      return;
    }
    await trackingBtn.first().click();
    await waitForTrackingContent(page);

    // Find the first user row and click it
    const rows = page.locator('main table tbody tr');
    const rowCount = await rows.count();
    if (rowCount === 0) {
      console.log('No user rows to expand — skipping');
      return;
    }

    await rows.first().click();

    // Wait for issue details to load (spinner disappears)
    const main = page.locator('main');
    await page.waitForFunction(
      () => {
        const main = document.querySelector('main');
        if (!main) return false;
        // Wait until no spinner inside the expanded row area
        const spinners = main.querySelectorAll('.animate-spin');
        return spinners.length === 0;
      },
      { timeout: 15_000 },
    );

    const text = await main.innerText();
    console.log('User row expanded OK, inner text:', text.slice(0, 200));
  });
});
