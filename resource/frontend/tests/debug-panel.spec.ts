import { test, expect } from './fixtures';

test.describe('DebugPanel', () => {
  test('is collapsed by default on load', async ({ appPage: page }) => {
    // The toggle button should say "Show Debug" when collapsed
    const toggleBtn = page.locator('button', { hasText: /Show Debug/ });
    await expect(toggleBtn).toBeVisible();

    // The log content area should not be visible
    const logArea = page.locator('.max-h-56.overflow-y-auto');
    await expect(logArea).not.toBeVisible();
  });

  test('expands when toggle is clicked', async ({ appPage: page }) => {
    const toggleBtn = page.locator('button', { hasText: /Show Debug/ });
    await toggleBtn.click();

    // Should now show "Hide Debug"
    await expect(page.locator('button', { hasText: /Hide Debug/ })).toBeVisible();

    // The log content area should be visible
    const logArea = page.locator('.max-h-56.overflow-y-auto');
    await expect(logArea).toBeVisible();
  });

  test('collapses again on second click', async ({ appPage: page }) => {
    const toggleBtn = page.locator('button', { hasText: /Show Debug/ });
    await toggleBtn.click();
    await expect(page.locator('button', { hasText: /Hide Debug/ })).toBeVisible();

    await page.locator('button', { hasText: /Hide Debug/ }).click();
    await expect(page.locator('button', { hasText: /Show Debug/ })).toBeVisible();
  });
});
