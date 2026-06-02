import { test as base, expect, type Page } from '@playwright/test';

/**
 * Shared helpers and extended fixtures for Diag Parser tests.
 *
 * Usage:
 *   import { test, expect, helpers } from './fixtures';
 */

// ---------------------------------------------------------------------------
// Helper utilities (stateless, importable anywhere)
// ---------------------------------------------------------------------------
export const helpers = {
  /** Wait for the app shell to be ready (header visible). */
  async waitForApp(page: Page) {
    await page.goto('/');
    await page.waitForSelector('.header-glass', { timeout: 15_000 });
  },

  /** Set the theme via the data-theme attribute on <html>. */
  async setTheme(page: Page, theme: 'dark' | 'light') {
    await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme);
  },

  /** Get the current theme from <html data-theme>. */
  async getTheme(page: Page): Promise<string | null> {
    return page.evaluate(() => document.documentElement.getAttribute('data-theme'));
  },

  /** Scroll the page by a given number of pixels. */
  async scrollBy(page: Page, y: number) {
    await page.evaluate((py) => window.scrollBy(0, py), y);
    // Give the scroll handler time to fire
    await page.waitForTimeout(100);
  },

  /** Check if an element has a specific CSS class. */
  async hasClass(page: Page, selector: string, className: string): Promise<boolean> {
    return page.evaluate(
      ([sel, cls]) => {
        const el = document.querySelector(sel);
        return el ? el.classList.contains(cls) : false;
      },
      [selector, className] as const,
    );
  },

  /** Get computed style property for an element. */
  async getComputedStyle(page: Page, selector: string, prop: string): Promise<string> {
    return page.evaluate(
      ([sel, p]) => {
        const el = document.querySelector(sel);
        return el ? getComputedStyle(el).getPropertyValue(p) : '';
      },
      [selector, prop] as const,
    );
  },
};

// ---------------------------------------------------------------------------
// Extended test fixture (auto-navigates to the app)
// ---------------------------------------------------------------------------
export const test = base.extend<{ appPage: Page }>({
  // eslint-disable-next-line react-hooks/rules-of-hooks
  appPage: async ({ page }, use) => {
    await helpers.waitForApp(page);
    await use(page);
  },
});

export { expect };
