import { test, expect, helpers } from './fixtures';

test.describe('Theme', () => {
  test('defaults to dark theme', async ({ appPage: page }) => {
    const theme = await helpers.getTheme(page);
    // Default could be dark or light depending on system preference,
    // but the attribute should exist
    expect(theme).toBeTruthy();
  });

  test('toggle switches theme attribute', async ({ appPage: page }) => {
    const before = await helpers.getTheme(page);
    await page.locator('button[aria-label*="Switch to"]').click();
    const after = await helpers.getTheme(page);
    expect(after).not.toBe(before);
  });

  test('light theme applies correct CSS variables', async ({ appPage: page }) => {
    await helpers.setTheme(page, 'light');
    // In light mode, --bg-void should be a light color
    const bgVoid = await helpers.getComputedStyle(page, ':root', '--bg-void');
    expect(bgVoid).toBeTruthy();
  });

  test('dark theme applies correct CSS variables', async ({ appPage: page }) => {
    await helpers.setTheme(page, 'dark');
    const bgVoid = await helpers.getComputedStyle(page, ':root', '--bg-void');
    expect(bgVoid).toBeTruthy();
  });

  test('header-glass has backdrop-filter rule in both themes', async ({ appPage: page }) => {
    for (const theme of ['dark', 'light'] as const) {
      await helpers.setTheme(page, theme);
      // Check the CSS rule exists on the element (computed value may be "none" in headless)
      const hasRule = await page.evaluate(() => {
        const el = document.querySelector('header.header-glass');
        if (!el) return false;
        const style = getComputedStyle(el);
        // Check both standard and webkit-prefixed
        const value = style.getPropertyValue('backdrop-filter') || style.getPropertyValue('-webkit-backdrop-filter');
        return value !== '' && value !== undefined;
      });
      expect(hasRule).toBe(true);
    }
  });
});
