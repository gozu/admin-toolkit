import { test, expect, type Page } from '@playwright/test';

/**
 * Inactive Project Cleaner tool — verifies the project-cleaner tab
 * loads and displays inactive projects from the live DSS instance.
 */

const LIVE_URL = 'http://localhost:10000/webapps/liveparser/';

async function waitForAppShell(page: Page) {
  await page.goto(LIVE_URL, { waitUntil: 'domcontentloaded', timeout: 30_000 });
  await page.waitForSelector('aside', { timeout: 60_000 });
  await page.waitForTimeout(500);
}

/**
 * Navigate to the project-cleaner tab.
 * The sidebar has two "Projects" buttons — one under INSIGHTS and one under TOOLS.
 * We need the second one (under TOOLS section).
 */
async function navigateToProjectCleaner(page: Page): Promise<boolean> {
  // Find all sidebar buttons labeled "Projects"
  const projectButtons = page.locator('aside button').filter({ hasText: /^Projects$/ });
  const count = await projectButtons.count();
  if (count < 2) {
    // If only one exists, it might be the TOOLS one (sidebar may be in a different state)
    if (count === 1) {
      await projectButtons.first().click();
    } else {
      return false;
    }
  } else {
    // The second "Projects" button is the one under TOOLS
    await projectButtons.nth(1).click();
  }
  await page.waitForTimeout(1000);
  return true;
}

test.describe('Inactive Project Cleaner', () => {
  test.setTimeout(120_000);

  test('navigates to project-cleaner tab and shows inactive projects', async ({ page }) => {
    await waitForAppShell(page);

    const navigated = await navigateToProjectCleaner(page);
    if (!navigated) {
      test.skip();
      return;
    }

    // Wait for the heading — may show loading state first, then data
    const heading = page.locator('text=Inactive Project Cleaner');
    await expect(heading.first()).toBeVisible({ timeout: 60_000 });

    // Wait for loading to complete (should not show "Loading" text anymore)
    await page.waitForFunction(
      () => {
        const main = document.querySelector('main');
        if (!main) return false;
        return !main.textContent?.includes('Loading inactive project data');
      },
      { timeout: 60_000 },
    );

    await page.screenshot({ path: '/tmp/project-cleaner.png' });

    const main = page.locator('main');
    const text = await main.innerText();

    // Should not show an error
    expect(text).not.toContain('500');

    // Check for either a populated table or the empty message
    const hasTable = await page.locator('table').isVisible().catch(() => false);
    const hasEmptyMsg = text.includes('No inactive projects found');

    console.log('Has table:', hasTable, '| Has empty message:', hasEmptyMsg);
    console.log('Page text (first 500 chars):', text.slice(0, 500));

    // At least one must be true
    expect(hasTable || hasEmptyMsg).toBe(true);

    // If table is visible, verify it has rows (threshold=1 should catch most projects)
    if (hasTable) {
      const rows = page.locator('table tbody tr');
      const rowCount = await rows.count();
      console.log('Inactive project rows:', rowCount);
      expect(rowCount).toBeGreaterThan(0);

      // Verify column headers
      const headers = page.locator('table thead th');
      const headerTexts = await headers.allInnerTexts();
      console.log('Table headers:', headerTexts);
      expect(headerTexts.some((h) => h.includes('Project Name'))).toBe(true);
      expect(headerTexts.some((h) => h.includes('Owner'))).toBe(true);
      expect(headerTexts.some((h) => h.includes('Days Inactive'))).toBe(true);

      // Verify each row has a "Days Inactive" badge
      const firstRowDays = rows.first().locator('span').filter({ hasText: /\d+d/ });
      await expect(firstRowDays).toBeVisible();

      // Verify delete button exists
      const deleteBtn = rows.first().locator('button', { hasText: 'Delete' });
      await expect(deleteBtn).toBeVisible();
    }
  });

  test('delete modal allows typing confirmation text', async ({ page }) => {
    await waitForAppShell(page);

    const navigated = await navigateToProjectCleaner(page);
    if (!navigated) {
      test.skip();
      return;
    }

    // Wait for the table to load
    const heading = page.locator('text=Inactive Project Cleaner');
    await expect(heading.first()).toBeVisible({ timeout: 60_000 });

    await page.waitForFunction(
      () => {
        const main = document.querySelector('main');
        if (!main) return false;
        return !main.textContent?.includes('Loading inactive project data');
      },
      { timeout: 60_000 },
    );

    const hasTable = await page.locator('table').isVisible().catch(() => false);
    if (!hasTable) {
      console.log('No inactive projects to test delete modal');
      test.skip();
      return;
    }

    // Click the first Delete button
    const deleteBtn = page.locator('table tbody tr').first().locator('button', { hasText: 'Delete' });
    await deleteBtn.click();
    await page.waitForTimeout(500);

    // Modal should be open with title "Confirm Deletion"
    const modalTitle = page.locator('text=Confirm Deletion');
    await expect(modalTitle).toBeVisible({ timeout: 5_000 });

    await page.screenshot({ path: '/tmp/project-cleaner-modal-open.png' });

    // The input field should be visible and clickable
    const confirmInput = page.locator('input[placeholder^="delete "]');
    await expect(confirmInput).toBeVisible();

    // Click and type in the input — this should NOT close the modal
    await confirmInput.click();
    await page.waitForTimeout(300);

    // Modal should still be open after clicking the input
    await expect(modalTitle).toBeVisible();

    // Type something in the input
    await confirmInput.fill('test');
    await page.waitForTimeout(300);

    // Modal should still be open
    await expect(modalTitle).toBeVisible();
    expect(await confirmInput.inputValue()).toBe('test');

    await page.screenshot({ path: '/tmp/project-cleaner-modal-typed.png' });

    // Close modal via Cancel button
    const cancelBtn = page.locator('button', { hasText: 'Cancel' });
    await cancelBtn.click();
    await page.waitForTimeout(300);

    // Modal should be closed
    await expect(modalTitle).not.toBeVisible();
  });

  test('stats bar shows project count', async ({ page }) => {
    await waitForAppShell(page);

    const navigated = await navigateToProjectCleaner(page);
    if (!navigated) {
      test.skip();
      return;
    }

    const heading = page.locator('text=Inactive Project Cleaner');
    await expect(heading.first()).toBeVisible({ timeout: 60_000 });

    await page.waitForFunction(
      () => {
        const main = document.querySelector('main');
        if (!main) return false;
        return !main.textContent?.includes('Loading inactive project data');
      },
      { timeout: 60_000 },
    );

    // Check for stats section (Total count and Backed Up & Deleted count)
    const totalLabel = page.locator('text=Total');
    const hasTotal = await totalLabel.first().isVisible().catch(() => false);

    if (hasTotal) {
      const statsSection = page.locator('.grid.grid-cols-2');
      await expect(statsSection.first()).toBeVisible();
      console.log('Stats section found');
    } else {
      const emptyMsg = page.locator('text=No inactive projects found');
      await expect(emptyMsg).toBeVisible();
      console.log('No projects — empty state shown');
    }

    await page.screenshot({ path: '/tmp/project-cleaner-stats.png' });
  });
});
