import { test, expect, type Locator, type Page } from '@playwright/test';

async function holdAtEdge(page: Page, button: Locator) {
  await button.scrollIntoViewIfNeeded();
  const before = (await button.boundingBox())!;
  await page.mouse.move(before.x + 4, before.y + before.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(160);
  const during = (await button.boundingBox())!;
  await page.mouse.up();
  expect(during.x).toBeCloseTo(before.x, 1);
  expect(during.width).toBeCloseTo(before.width, 1);
}

for (const reducedMotion of ['no-preference', 'reduce'] as const) {
  test.describe(`sidebar press scope (${reducedMotion})`, () => {
    test.use({ reducedMotion });

    test('sidebar keeps press feedback while health expanders stay stationary', async ({ page }) => {
      await page.emulateMedia({ reducedMotion });
      await page.route('**/api/**', async (route) => {
        const path = new URL(route.request().url()).pathname;
        let body: unknown = {};
        if (path.endsWith('/api/mode')) body = { mode: 'live', version: 'test', runningVersion: 'test' };
        if (path.endsWith('/api/hosts')) body = [{ id: 'local', label: 'Local DSS', url: '' }];
        if (path.endsWith('/api/hosts/check')) {
          body = { ok: true, pluginInstalled: true, adminToolkitProjectExists: true };
        }
        if (path.endsWith('/api/settings/raw')) body = { cgroupSettings: { enabled: false } };
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
      });
      await page.goto('/');
      expect(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(reducedMotion === 'reduce');
      await page.getByRole('button', { name: /Local DSS/ }).click();
      const summary = page.locator('aside [data-page-id="summary"]');
      await summary.click();
      const customize = page.getByRole('button', { name: /Customize health checks/ });
      await expect(customize).toBeVisible();
      // Let the existing page entrance and initial open animation finish.
      await page.waitForTimeout(600);

      const before = (await summary.boundingBox())!;
      await summary.hover();
      await page.mouse.down();
      await page.waitForTimeout(160);
      const during = (await summary.boundingBox())!;
      await page.mouse.up();
      expect(during.width).toBeCloseTo(before.width * (reducedMotion === 'reduce' ? 1 : 0.96), 1);

      const controls = customize.locator('..').locator('button').nth(1);
      await expect(controls).toBeVisible();
      await holdAtEdge(page, customize);
      await expect(controls).not.toBeVisible();
      await holdAtEdge(page, customize);
      await expect(controls).toBeVisible();

      const issue = page.getByRole('button', { name: 'CGroups not enabled', exact: true });
      await expect(issue).toBeVisible();
      await holdAtEdge(page, issue);
      await expect(page.getByText('Enable CGroups memory limits for kernels and jobs.', { exact: true })).toBeVisible();
      await holdAtEdge(page, issue);
      await expect(page.getByText('Enable CGroups memory limits for kernels and jobs.', { exact: true })).not.toBeVisible();
    });
  });
}
