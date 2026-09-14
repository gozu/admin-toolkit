import { test, expect, type Locator, type Page } from '@playwright/test';

async function openAudit(page: Page) {
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path.endsWith('/api/mode')) body = { mode: 'live', version: 'test', runningVersion: 'test' };
    if (path.endsWith('/api/hosts')) body = [{ id: 'local', label: 'Local DSS', url: '' }];
    if (path.endsWith('/api/hosts/check')) {
      body = { ok: true, pluginInstalled: true, adminToolkitProjectExists: true };
    }
    if (path.endsWith('/api/projects/sql_pushdown_audit')) {
      const result = {
        ownerGroups: [{
          ownerLogin: 'demo', ownerDisplayName: 'Demo Owner', ownerEmail: '', totalRecipes: 1,
          projects: [{
            projectKey: 'DEMO', projectName: 'Demo Project',
            recipes: [{
              recipeName: 'prepare_demo', recipeType: 'shaker', connection: 'warehouse',
              inputs: ['source'], outputs: ['prepared'],
            }],
          }],
        }],
      };
      return route.fulfill({
        contentType: 'text/event-stream',
        body: `event: init\ndata: {"total":1}\n\nevent: done\ndata: ${JSON.stringify(result)}\n\n`,
      });
    }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto('/');
  await page.getByRole('button', { name: /Local DSS/ }).click();
  await page.locator('aside').getByRole('button', { name: 'Compute', exact: true }).click();
  await expect(page.getByRole('button', { name: /Demo Owner/ })).toBeVisible();
}

// Holding the pointer lets pressJuice reach its minimum scale before release.
// A normal Playwright click is too fast to reliably reproduce the missed click.
async function pressWithoutMoving(page: Page, row: Locator, target: Locator) {
  await row.scrollIntoViewIfNeeded();
  const before = await row.boundingBox();
  const hit = await target.boundingBox();
  expect(before).not.toBeNull();
  expect(hit).not.toBeNull();
  await page.mouse.move(hit!.x + hit!.width / 2, hit!.y + hit!.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(160);
  const during = await row.boundingBox();
  await page.mouse.up();
  expect(during!.x).toBeCloseTo(before!.x, 1);
  expect(during!.width).toBeCloseTo(before!.width, 1);
}

for (const reducedMotion of ['no-preference', 'reduce'] as const) {
  test.describe(`SQL audit disclosures (${reducedMotion})`, () => {
    test.use({ viewport: { width: 3000, height: 1000 }, reducedMotion });

    test('arrow and name clicks keep rows stationary and expand exactly once', async ({ page }) => {
      await openAudit(page);
      const owner = page.getByRole('button', { name: /Demo Owner/ });
      const project = page.getByRole('button', { name: /Demo Project/ });
      await expect(owner).toHaveAttribute('aria-expanded', 'false');
      await pressWithoutMoving(page, owner, owner.locator('svg'));
      await expect(owner).toHaveAttribute('aria-expanded', 'true');
      await expect(project).toBeVisible();

      await pressWithoutMoving(page, project, project.locator('svg'));
      await expect(project).toHaveAttribute('aria-expanded', 'true');
      await expect(page.getByText('prepare_demo', { exact: true })).toBeVisible();

      await owner.focus();
      await page.keyboard.press('Space');
      await expect(owner).toHaveAttribute('aria-expanded', 'false');
      await expect(project).not.toBeVisible();

      await pressWithoutMoving(page, owner, owner.getByText('Demo Owner', { exact: true }));
      await expect(owner).toHaveAttribute('aria-expanded', 'true');
      await expect(project).toBeVisible();
      await owner.focus();
      await page.keyboard.press('Enter');
      await expect(owner).toHaveAttribute('aria-expanded', 'false');
    });
  });
}
