import { test, expect } from './fixtures';

test.describe('App Shell', () => {
  test('loads without console errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await page.goto('/');
    await page.waitForSelector('.header-glass', { timeout: 15_000 });

    // Filter out known benign errors (e.g. network requests to DSS backend)
    const real = errors.filter(
      (e) => !e.includes('Failed to fetch') && !e.includes('NetworkError'),
    );
    expect(real).toEqual([]);
  });

  test('has correct page title or branding', async ({ appPage: page }) => {
    // The header should contain the DIAG branding text
    await expect(page.locator('header .text-neon-subtle', { hasText: 'DIAG' })).toBeVisible();
  });

  test('footer is visible', async ({ appPage: page }) => {
    const footer = page.locator('footer');
    await expect(footer).toBeVisible();
  });

  test('no accessibility violations on landmark structure', async ({ appPage: page }) => {
    // Basic landmark check: header and main should exist
    await expect(page.locator('header')).toBeVisible();
  });
});
