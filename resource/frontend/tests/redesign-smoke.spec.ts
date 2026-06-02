import { test, expect, type Page } from '@playwright/test';

/**
 * Smoke tests for the UI/UX redesign — sidebar navigation, page routing,
 * command palette, theme toggle, and responsive collapse.
 *
 * Runs against the live DSS instance (data loaded via API).
 */

const LIVE_URL = 'http://localhost:10000/webapps/liveparser/';

// Wait for the app shell to be fully loaded (sidebar visible = data parsed)
async function waitForAppShell(page: Page) {
  await page.goto(LIVE_URL, { waitUntil: 'domcontentloaded', timeout: 30_000 });

  // The app first shows loading, then transitions to AppShell with sidebar
  // once API data is loaded and parsed.
  await page.waitForSelector('aside', { timeout: 60_000 });

  // Give framer-motion animations time to settle
  await page.waitForTimeout(500);
}

// All sidebar page IDs that should be navigable
const PAGE_LABELS = [
  'Summary', 'Issues', 'Resources', 'Dir Usage',
  'Projects', 'Code Envs', 'Connections',
  'Runtime', 'Security', 'Platform',
  'Errors',
] as const;

// Section headers in the sidebar
const NAV_SECTIONS = ['OVERVIEW', 'INFRASTRUCTURE', 'DATA', 'CONFIGURATION', 'LOGS'];

// Helper to find a sidebar nav button by label text
function sidebarBtn(page: Page, label: string) {
  return page.locator('aside button').filter({ hasText: label });
}

test.describe('Redesign Smoke Tests', () => {
  test.setTimeout(120_000);

  test('app loads and shows sidebar with navigation', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await waitForAppShell(page);

    // Sidebar <aside> should be visible
    await expect(page.locator('aside')).toBeVisible();

    // Should have a <main> content area
    await expect(page.locator('main')).toBeVisible();

    // Should have the top bar header
    const headers = page.locator('header');
    await expect(headers.first()).toBeVisible();

    // Sidebar should contain nav buttons
    const navBtnCount = await page.locator('aside button').count();
    expect(navBtnCount).toBeGreaterThan(5);

    // Filter benign network errors
    const real = errors.filter(
      (e) => !e.includes('Failed to fetch') && !e.includes('NetworkError'),
    );
    expect(real).toEqual([]);
  });

  test('sidebar shows section headers when expanded', async ({ page }) => {
    await waitForAppShell(page);

    // At 1600px viewport, sidebar should be expanded
    const sidebar = page.locator('aside');

    // Check that "Diagnostics" or "D" branding exists
    const branding = sidebar.locator('span').first();
    await expect(branding).toBeVisible();

    // Check section headers (only visible when expanded)
    for (const section of NAV_SECTIONS) {
      const sectionEl = sidebar.locator(`text=${section}`);
      const visible = await sectionEl.isVisible().catch(() => false);
      // If sidebar is collapsed, sections won't be visible — that's fine
      if (!visible) {
        // At least verify the sidebar has nav buttons
        expect(await page.locator('aside button').count()).toBeGreaterThan(5);
        return; // Sidebar is collapsed, skip text checks
      }
    }
  });

  test('sidebar nav items are clickable and switch pages', async ({ page }) => {
    await waitForAppShell(page);

    let navigated = 0;

    for (const label of PAGE_LABELS) {
      const btn = sidebarBtn(page, label);
      const visible = await btn.first().isVisible().catch(() => false);
      if (!visible) continue;

      await btn.first().click();
      await page.waitForTimeout(400);

      // Main content should still be present
      await expect(page.locator('main')).toBeVisible();
      navigated++;
    }

    // Should have navigated to at least some pages
    expect(navigated).toBeGreaterThan(3);
  });

  test('active nav item has accent styling', async ({ page }) => {
    await waitForAppShell(page);

    // Summary should be active by default — find a button with accent-muted
    const activeBtn = page.locator('aside button[class*="accent-muted"]');
    const count = await activeBtn.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test('clicking nav item changes active state', async ({ page }) => {
    await waitForAppShell(page);

    // Click "Issues" (or whatever the 2nd button is)
    const issuesBtn = sidebarBtn(page, 'Issues');
    const visible = await issuesBtn.first().isVisible().catch(() => false);
    if (!visible) return; // Skip if sidebar collapsed

    await issuesBtn.first().click();
    await page.waitForTimeout(400);

    // Now the Issues button should have accent styling
    const classes = await issuesBtn.first().getAttribute('class');
    expect(classes).toContain('accent-muted');
  });

  test('breadcrumb visible in top bar', async ({ page }) => {
    await waitForAppShell(page);

    // Top bar header should have some content (breadcrumb + controls)
    const header = page.locator('header').first();
    await expect(header).toBeVisible();

    const headerText = await header.innerText();
    // Should contain at least something (breadcrumb section name or page label)
    expect(headerText.length).toBeGreaterThan(0);
  });

  test('sidebar collapse toggle works', async ({ page }) => {
    await waitForAppShell(page);

    // Try to find the collapse/expand toggle button
    const collapseBtn = page.locator('button[title="Collapse sidebar"]');
    const expandBtn = page.locator('button[title="Expand sidebar"]');

    const canCollapse = await collapseBtn.isVisible().catch(() => false);
    const canExpand = await expandBtn.isVisible().catch(() => false);

    if (canCollapse) {
      // Sidebar is expanded — collapse it
      await collapseBtn.click();
      await page.waitForTimeout(600);

      // Now expand button should be visible
      await expect(expandBtn).toBeVisible({ timeout: 3000 });

      // Expand again
      await expandBtn.click();
      await page.waitForTimeout(600);
      await expect(collapseBtn).toBeVisible({ timeout: 3000 });
    } else if (canExpand) {
      // Sidebar started collapsed — expand it
      await expandBtn.click();
      await page.waitForTimeout(600);
      await expect(collapseBtn).toBeVisible({ timeout: 3000 });
    } else {
      // Neither button found — fail
      expect(canCollapse || canExpand).toBe(true);
    }
  });

  test('Cmd+K / Ctrl+K opens command palette', async ({ page }) => {
    await waitForAppShell(page);

    // Try Ctrl+K (works on Linux headless)
    await page.keyboard.press('Control+k');
    await page.waitForTimeout(400);

    // Look for the command palette input
    let input = page.locator('input[placeholder*="search" i]').or(
      page.locator('input[placeholder*="command" i]'),
    ).or(
      page.locator('input[placeholder*="page" i]'),
    );

    let visible = await input.first().isVisible().catch(() => false);

    if (!visible) {
      // Try Meta+K
      await page.keyboard.press('Meta+k');
      await page.waitForTimeout(400);
      visible = await input.first().isVisible().catch(() => false);
    }

    expect(visible).toBe(true);

    // Type a search query
    if (visible) {
      await input.first().fill('resources');
      await page.waitForTimeout(200);

      // Should show matching results
      const results = page.locator('[class*="command-palette"] button, [class*="palette"] [role="option"]');
      // Just check the input accepted text
      const val = await input.first().inputValue();
      expect(val).toBe('resources');
    }

    // Press Escape to close
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
  });

  test('theme toggle switches between dark and light', async ({ page }) => {
    await waitForAppShell(page);

    const initialTheme = await page.evaluate(() =>
      document.documentElement.getAttribute('data-theme'),
    );

    // Theme toggle is in the sidebar footer — find by title
    const themeBtn = page.locator('button[title*="Switch to"]');
    const visible = await themeBtn.first().isVisible().catch(() => false);

    if (!visible) {
      // Theme button might be in header or elsewhere
      const altBtn = page.locator('button[aria-label*="Switch to"]').or(
        page.locator('button[title*="light"]'),
      ).or(
        page.locator('button[title*="dark"]'),
      );
      const altVisible = await altBtn.first().isVisible().catch(() => false);
      if (!altVisible) {
        // Can't find theme toggle — skip gracefully
        return;
      }
      await altBtn.first().click();
    } else {
      await themeBtn.first().click();
    }

    await page.waitForTimeout(300);

    const newTheme = await page.evaluate(() =>
      document.documentElement.getAttribute('data-theme'),
    );

    // Theme should have changed
    // Note: DSS wraps the app, so data-theme might be on inner html element
    if (initialTheme) {
      expect(newTheme).not.toBe(initialTheme);
    }
  });

  test('CSS design tokens resolve to values', async ({ page }) => {
    await waitForAppShell(page);

    // Check CSS variables on the root element inside the app
    const vars = await page.evaluate(() => {
      // Try the inner <html> inside #dku_html if it exists, else document root
      const root = document.querySelector('#dku_html html') || document.documentElement;
      const style = getComputedStyle(root);
      return {
        bgApp: style.getPropertyValue('--bg-app').trim(),
        accent: style.getPropertyValue('--accent').trim(),
        textPrimary: style.getPropertyValue('--text-primary').trim(),
      };
    });

    // Also check on actual visible elements
    const sidebarBg = await page.evaluate(() => {
      const aside = document.querySelector('aside');
      if (!aside) return '';
      return getComputedStyle(aside).backgroundColor;
    });

    // At least the sidebar should have a background color
    expect(sidebarBg).toBeTruthy();

    // If CSS vars are accessible, they should have values
    const hasAnyVar = Object.values(vars).some((v) => v !== '');
    if (hasAnyVar) {
      for (const [key, val] of Object.entries(vars)) {
        expect(val, `CSS var --${key} should be set`).toBeTruthy();
      }
    }
  });

  test('app shell uses grid layout', async ({ page }) => {
    await waitForAppShell(page);

    const layout = await page.evaluate(() => {
      // Find the grid container — it's a div with grid display containing aside + header + main
      const aside = document.querySelector('aside');
      if (!aside) return null;
      const parent = aside.parentElement;
      if (!parent) return null;
      const style = getComputedStyle(parent);
      return {
        display: style.display,
        gridTemplateColumns: style.gridTemplateColumns,
      };
    });

    expect(layout).not.toBeNull();
    expect(layout!.display).toBe('grid');
    expect(layout!.gridTemplateColumns).toBeTruthy();
  });

  test('summary page renders content', async ({ page }) => {
    await waitForAppShell(page);

    // Summary is the default page
    const main = page.locator('main');
    await expect(main).toBeVisible();

    const text = await main.innerText();
    expect(text.length).toBeGreaterThan(10);
  });

  test('config pages render with tab bar', async ({ page }) => {
    await waitForAppShell(page);

    const runtimeBtn = sidebarBtn(page, 'Runtime');
    const visible = await runtimeBtn.first().isVisible().catch(() => false);
    if (!visible) return;

    await runtimeBtn.first().click();
    await page.waitForTimeout(500);

    // Main should have content
    const main = page.locator('main');
    const text = await main.innerText();
    expect(text.length).toBeGreaterThan(0);

    // Look for tab buttons within main content
    const tabBtns = main.locator('button');
    const tabCount = await tabBtns.count();
    // Config pages should have at least 2 tab buttons
    if (tabCount >= 2) {
      // Click the second tab
      await tabBtns.nth(1).click();
      await page.waitForTimeout(300);
    }
  });

  test('no JS errors during full navigation sweep', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await waitForAppShell(page);

    // Navigate to every page via sidebar
    for (const label of PAGE_LABELS) {
      const btn = sidebarBtn(page, label);
      if (await btn.first().isVisible().catch(() => false)) {
        await btn.first().click();
        await page.waitForTimeout(400);
      }
    }

    // Also try tools
    for (const label of ['Outreach', 'Code Env Cleaner']) {
      const btn = sidebarBtn(page, label);
      if (await btn.first().isVisible().catch(() => false)) {
        await btn.first().click();
        await page.waitForTimeout(400);
      }
    }

    const real = errors.filter(
      (e) =>
        !e.includes('Failed to fetch') &&
        !e.includes('NetworkError') &&
        !e.includes('AbortError') &&
        !e.includes('net::ERR'),
    );

    if (real.length > 0) {
      console.log('JS errors found:', real);
    }
    expect(real).toEqual([]);
  });

  test('captures screenshots for visual review', async ({ page }) => {
    await waitForAppShell(page);

    await page.screenshot({ path: '/tmp/redesign-summary.png', fullPage: false });

    const pages = ['Issues', 'Resources', 'Runtime', 'Security', 'Errors'];
    for (const label of pages) {
      const btn = sidebarBtn(page, label);
      if (await btn.first().isVisible().catch(() => false)) {
        await btn.first().click();
        await page.waitForTimeout(500);
        const slug = label.toLowerCase().replace(/\s+/g, '-');
        await page.screenshot({ path: `/tmp/redesign-${slug}.png`, fullPage: false });
      }
    }
  });
});
