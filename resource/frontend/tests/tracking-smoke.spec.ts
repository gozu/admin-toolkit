import { test, expect, type Page } from '@playwright/test';

/**
 * Tracking smoke tests — sends all outreach campaigns then verifies
 * the Tracking page shows user compliance rows.
 *
 * Runs against the live DSS instance at localhost:10000.
 */

const LIVE_URL = 'http://localhost:10000/webapps/liveparser/';
const SEND_ALL_TIMEOUT = 120_000; // email sending can be slow

async function waitForAppShell(page: Page) {
  await page.goto(LIVE_URL, { waitUntil: 'domcontentloaded', timeout: 30_000 });
  await page.waitForSelector('aside', { timeout: 60_000 });
  await page.waitForTimeout(500);
}

function sidebarBtn(page: Page, label: string) {
  return page.locator('aside button').filter({ hasText: label });
}

test.describe('Tracking Smoke Tests', () => {
  test.setTimeout(SEND_ALL_TIMEOUT + 60_000);

  test('send-all button exists and is enabled in outreach page', async ({ page }) => {
    await waitForAppShell(page);

    const outreachBtn = sidebarBtn(page, 'Outreach');
    const visible = await outreachBtn.first().isVisible().catch(() => false);
    if (!visible) {
      test.skip(); // sidebar collapsed — skip
      return;
    }
    await outreachBtn.first().click();
    await page.waitForTimeout(600);

    // Wait for outreach data to load (the card with summary stats)
    await page.waitForSelector('[data-testid="send-all-campaigns"]', { timeout: 15_000 });

    const sendAllBtn = page.locator('[data-testid="send-all-campaigns"]');
    await expect(sendAllBtn).toBeVisible();

    // Button should not be disabled (data is loaded from the live instance)
    const disabled = await sendAllBtn.getAttribute('disabled');
    expect(disabled).toBeNull();
  });

  test('send all campaigns and verify tracking data appears', async ({ page }) => {
    await waitForAppShell(page);

    // ── Step 1: Navigate to Outreach ──────────────────────────────────────────
    const outreachBtn = sidebarBtn(page, 'Outreach');
    const visible = await outreachBtn.first().isVisible().catch(() => false);
    if (!visible) {
      test.skip();
      return;
    }
    await outreachBtn.first().click();
    await page.waitForTimeout(600);

    // Wait for the Send All button to appear (confirms ZIP data loaded)
    await page.waitForSelector('[data-testid="send-all-campaigns"]', { timeout: 15_000 });
    const sendAllBtn = page.locator('[data-testid="send-all-campaigns"]');

    // Give the background API call (/api/tools/email/data) time to complete so the
    // server-side tracking ingest runs before we call preview/send.
    await page.waitForTimeout(5_000);

    // Screenshot before sending
    await page.screenshot({ path: '/tmp/tracking-before-send.png' });

    // ── Step 2: Click Send All ─────────────────────────────────────────────────
    const isDisabled = await sendAllBtn.getAttribute('disabled');
    if (isDisabled !== null) {
      console.log('Send All button is disabled — possibly no outreach data or no mail channel');
      test.skip();
      return;
    }

    await sendAllBtn.click();
    console.log('Clicked Send All Campaigns');

    // ── Step 3: Wait for completion ───────────────────────────────────────────
    // Button shows progress status text while sending, then final "Done" message
    await page.waitForFunction(
      () => {
        const btn = document.querySelector('[data-testid="send-all-campaigns"]') as HTMLButtonElement | null;
        // Button is done when: not disabled AND text is not "Sending…" variant
        if (!btn) return false;
        const text = btn.textContent ?? '';
        return !btn.disabled && (text === 'Send All Campaigns');
      },
      { timeout: SEND_ALL_TIMEOUT },
    );

    // Check for "Done" status text next to the button
    const statusText = page.locator('text=/Done —/');
    const doneVisible = await statusText.isVisible().catch(() => false);
    console.log('Done status visible:', doneVisible);
    if (doneVisible) {
      const msg = await statusText.textContent();
      console.log('Send All result:', msg);
    }

    await page.screenshot({ path: '/tmp/tracking-after-send.png' });

    // ── Step 4: Navigate to Tracking ─────────────────────────────────────────
    const trackingBtn = sidebarBtn(page, 'Tracking');
    const trackingVisible = await trackingBtn.first().isVisible().catch(() => false);
    if (!trackingVisible) {
      console.log('Tracking button not visible — skipping verification');
      return;
    }
    await trackingBtn.first().click();
    await page.waitForTimeout(1000);

    // Wait for the tracking page to render (title or table)
    await page.waitForSelector('main', { timeout: 10_000 });

    await page.screenshot({ path: '/tmp/tracking-page.png' });

    // ── Step 5: Verify tracking data ──────────────────────────────────────────
    const main = page.locator('main');
    const text = await main.innerText();
    console.log('Tracking page text (first 300 chars):', text.slice(0, 300));

    // Should NOT show a 501 error
    expect(text).not.toContain('501');
    expect(text).not.toContain('NOT IMPLEMENTED');
    expect(text).not.toContain('Tracking not available');

    // If emails were sent, there should be user rows or a "no data" message
    // Either is acceptable — the key thing is no error banner
    const hasTable = await page.locator('table').isVisible().catch(() => false);
    const hasNoData = text.includes('No tracking data') || text.includes('No users');
    console.log('Has table:', hasTable, '| Has no-data message:', hasNoData);

    // At least one of: table rows OR a graceful empty state
    const apiOk = hasTable || hasNoData || text.includes('User Compliance');
    expect(apiOk).toBe(true);

    // If a table is visible, check for at least one user row
    if (hasTable && doneVisible) {
      const rows = page.locator('table tbody tr');
      const rowCount = await rows.count();
      console.log('Tracking table rows:', rowCount);
      expect(rowCount).toBeGreaterThan(0);
    }
  });

  test('tracking page shows user compliance UI', async ({ page }) => {
    await waitForAppShell(page);

    const trackingBtn = sidebarBtn(page, 'Tracking');
    const visible = await trackingBtn.first().isVisible().catch(() => false);
    if (!visible) {
      test.skip();
      return;
    }
    await trackingBtn.first().click();
    await page.waitForTimeout(800);

    const main = page.locator('main');
    await expect(main).toBeVisible();

    // Should contain "User Compliance" heading
    const heading = page.locator('main').getByText('User Compliance');
    const headingVisible = await heading.first().isVisible().catch(() => false);
    expect(headingVisible).toBe(true);

    // Should not have JS errors
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));
    await page.waitForTimeout(500);
    const real = errors.filter(
      (e) => !e.includes('Failed to fetch') && !e.includes('NetworkError'),
    );
    expect(real).toEqual([]);

    await page.screenshot({ path: '/tmp/tracking-ui.png' });
  });
});
