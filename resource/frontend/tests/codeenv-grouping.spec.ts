import { test, expect, type Page } from '@playwright/test';

/**
 * Code Env Expanded View — Project Grouping
 *
 * Verifies that expanding a recipient row in a code-env campaign
 * shows project group headers with nested code envs (hierarchical),
 * not a flat list.
 *
 * Runs against the live DSS instance at localhost:10000.
 */

const LIVE_URL = 'http://localhost:10000/webapps/liveparser/';

async function navigateToOutreach(page: Page) {
  await page.goto(LIVE_URL, { waitUntil: 'domcontentloaded', timeout: 30_000 });
  await page.waitForSelector('aside', { timeout: 60_000 });
  await page.waitForTimeout(500);

  // Click Outreach in the main sidebar
  await page.locator('aside button').filter({ hasText: 'Outreach' }).first().click();
  await page.waitForTimeout(2_000);

  // Wait for outreach data to load
  await page.waitForSelector('table', { timeout: 20_000 });
  await page.waitForTimeout(2_000);
}

/** Click a campaign tab by label — picks the visible (desktop) one */
async function selectCampaign(page: Page, label: string) {
  const btn = page.locator('button:visible').filter({ hasText: label });
  await btn.first().click();
  await page.waitForTimeout(500);
}

test.describe('Code Env Expanded View — Project Grouping', () => {
  test.setTimeout(90_000);

  test('code env sprawl recipients show project group headers when expanded', async ({ page }) => {
    await navigateToOutreach(page);

    // Code Env Sprawl is the default campaign — click it to be sure
    await selectCampaign(page, 'Code Env Sprawl');
    await page.screenshot({ path: '/tmp/codeenv-grouping-1-campaign.png' });

    // Find recipient rows (they have ▶ expand arrow)
    const recipientRows = page.locator('table tbody tr').filter({
      has: page.locator('td:has-text("▶")'),
    });
    const rowCount = await recipientRows.count();
    console.log(`Recipient rows: ${rowCount}`);

    if (rowCount === 0) {
      test.skip();
      return;
    }

    // Click the first recipient to expand
    await recipientRows.first().click();
    await page.waitForTimeout(500);
    await page.screenshot({ path: '/tmp/codeenv-grouping-2-expanded.png' });

    // Project group headers: uppercase, font-semibold, tracking-wider in a td with pl-8
    const groupHeaders = page.locator('table tbody tr td.uppercase');
    const groupCount = await groupHeaders.count();
    console.log(`Project group headers: ${groupCount}`);

    for (let i = 0; i < groupCount; i++) {
      const text = await groupHeaders.nth(i).textContent();
      console.log(`  Group ${i + 1}: "${text?.trim()}"`);
    }

    expect(groupCount, 'Expected project group headers — grouping is not working').toBeGreaterThan(0);

    // Nested code env rows have pl-14 indentation (grouped) vs pl-8 (flat)
    const nestedRows = page.locator('table tbody tr td.pl-14');
    const nestedCount = await nestedRows.count();
    console.log(`Nested code env rows (pl-14): ${nestedCount}`);
    expect(nestedCount, 'Expected nested code env rows under project groups').toBeGreaterThan(0);

    // No flat sin rows (pl-8 text-sm would mean flat fallback rendering)
    const flatRows = page.locator('table tbody tr td.pl-8.text-sm');
    const flatCount = await flatRows.count();
    console.log(`Flat sin rows (pl-8 text-sm): ${flatCount} (should be 0)`);
    expect(flatCount, 'Flat fallback rows found — grouping fell back').toBe(0);

    await page.screenshot({ path: '/tmp/codeenv-grouping-3-verified.png' });
  });

  test('non-code-env campaign uses flat rendering', async ({ page }) => {
    await navigateToOutreach(page);

    // Select a non-code-env campaign
    await selectCampaign(page, 'Empty Projects');
    await page.waitForTimeout(500);

    const recipientRows = page.locator('table tbody tr').filter({
      has: page.locator('td:has-text("▶")'),
    });
    const rowCount = await recipientRows.count();
    console.log(`Recipient rows in Empty Projects: ${rowCount}`);

    if (rowCount === 0) {
      test.skip();
      return;
    }

    await recipientRows.first().click();
    await page.waitForTimeout(500);

    // Should NOT have project group headers
    const groupHeaders = page.locator('table tbody tr td.uppercase');
    const groupCount = await groupHeaders.count();
    console.log(`Group headers in non-code-env campaign: ${groupCount} (should be 0)`);
    expect(groupCount).toBe(0);

    await page.screenshot({ path: '/tmp/codeenv-grouping-4-flat.png' });
  });
});
