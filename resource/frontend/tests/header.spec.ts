import { test, expect, helpers } from './fixtures';

test.describe('Header', () => {
  test('renders with header-glass class', async ({ appPage: page }) => {
    const header = page.locator('header.header-glass');
    await expect(header).toBeVisible();
  });

  test('is sticky with top-0 positioning', async ({ appPage: page }) => {
    const position = await helpers.getComputedStyle(page, 'header.header-glass', 'position');
    expect(position).toBe('sticky');
  });

  test('adds scrolled class on scroll', async ({ appPage: page }) => {
    // Before scroll — no scrolled class
    expect(await helpers.hasClass(page, 'header.header-glass', 'scrolled')).toBe(false);

    // Scroll down past threshold
    await helpers.scrollBy(page, 200);

    expect(await helpers.hasClass(page, 'header.header-glass', 'scrolled')).toBe(true);
  });

  test('removes scrolled class when scrolled back to top', async ({ appPage: page }) => {
    await helpers.scrollBy(page, 200);
    expect(await helpers.hasClass(page, 'header.header-glass', 'scrolled')).toBe(true);

    // Scroll back to top
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(100);

    expect(await helpers.hasClass(page, 'header.header-glass', 'scrolled')).toBe(false);
  });

  test('theme toggle switches between dark and light', async ({ appPage: page }) => {
    const initial = await helpers.getTheme(page);
    const toggleBtn = page.locator('button[aria-label*="Switch to"]');
    await toggleBtn.click();

    const after = await helpers.getTheme(page);
    expect(after).not.toBe(initial);
  });
});
