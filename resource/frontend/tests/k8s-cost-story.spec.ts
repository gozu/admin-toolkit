import { expect, test, type Page } from '@playwright/test';

async function openCosts(page: Page, variant: 'normal' | 'unpriced' | 'incomplete' | 'legacy' = 'normal') {
  const projection = {
    savingsMonthly: 726, consolidationSavingsMonthly: 695.6, idleNodeSavingsMonthly: 30.4,
    floorMonthly: 386, summary: 'Technical packing evidence', floorBreakdown: [],
    podsWithoutRequestsOrUsage: variant === 'incomplete' ? 2 : 0,
  };
  const finding = {
    id: 'floor', rule: 'cluster-floor-projection', severity: 'high', category: 'cost',
    title: 'Cluster floor: technical title', summary: 'Technical packing evidence',
    costImpactPerMonth: 726, remediation: [],
    evidence: {
      currentMonthly: 1112, ...projection,
      ...(variant === 'legacy' ? {} : { projections: {
        rightsized: projection,
        requests: { ...projection, savingsMonthly: 0, consolidationSavingsMonthly: 0, idleNodeSavingsMonthly: 0, floorMonthly: 1112 },
      } }),
    },
  };
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({ json: body });
    if (path.endsWith('/api/mode')) return json({ mode: 'live', version: 'test', runningVersion: 'test' });
    if (path.endsWith('/api/hosts')) return json([{ id: 'local', label: 'Local DSS', url: '' }]);
    if (path.endsWith('/api/hosts/check')) return json({ ok: true, pluginInstalled: true, adminToolkitProjectExists: true });
    if (path.endsWith('/api/k8s-insights/clusters')) return json({ clusters: [{ id: 'demo', name: 'Demo', state: 'RUNNING' }] });
    if (path.endsWith('/api/k8s-insights/stream')) return route.fulfill({
      contentType: 'text/event-stream',
      body: `event: done\ndata: ${JSON.stringify({
        ok: true, cluster: { id: 'demo', nodeCount: 5, podCount: 40 },
        costSnapshot: { currentMonthly: variant === 'unpriced' ? null : 1112, currentHourly: 1.52, nodes: [] },
        pricingStatus: { ok: variant !== 'unpriced', source: 'test', error: 'Prices unavailable' },
        findings: [finding], findingsCount: 1, probes: {}, nodeBreakdown: [],
      })}\n\n`,
    });
    return json({});
  });
  await page.goto('/');
  await page.getByRole('button', { name: /Local DSS/ }).click();
  await page.locator('aside').getByText('K8s Insights', { exact: true }).click();
  await expect(page.getByRole('region', { name: 'Server costs' })).toBeVisible();
}

test('cost story stays consistent across estimates and keeps technical evidence optional', async ({ page }) => {
  await openCosts(page);
  const costs = page.getByRole('region', { name: 'Server costs' });
  await expect(costs).toContainText('$1,112/mo');
  await expect(costs).toContainText('$386/mo');
  await expect(costs).toContainText('$726/mo');
  await expect(page.locator('aside').getByText('Feedback', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Feedback', exact: true })).toBeVisible();
  await page.getByRole('button', { name: /Run the same work on fewer servers/ }).click();
  await expect(page.locator('p').filter({ hasText: /^Technical packing evidence$/ })).not.toBeVisible();
  await page.getByText('Technical details and action steps', { exact: true }).click();
  await expect(page.locator('p').filter({ hasText: /^Technical packing evidence$/ })).toBeVisible();
  const before = await costs.boundingBox();
  await costs.getByRole('button', { name: 'Keep resource settings' }).click();
  await expect(costs.getByRole('button', { name: 'Keep resource settings' })).toHaveAttribute('aria-pressed', 'true');
  await expect(costs).toContainText('$0.00/mo');
  await expect(costs).toContainText('No savings found with these resource settings.');
  await expect(costs).not.toContainText('$386/mo');
  expect((await costs.boundingBox())?.height).toBe(before?.height);
  await costs.getByRole('button', { name: 'Adjust resource settings' }).click();
  await expect(costs).toContainText('$726/mo');
  expect((await costs.boundingBox())?.height).toBe(before?.height);
  await costs.screenshot({ path: '/tmp/atk-cost-story-dark.png' });
  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'light'));
  await costs.screenshot({ path: '/tmp/atk-cost-story-light.png' });
});

test('missing prices never imply a free cluster', async ({ page }) => {
  await openCosts(page, 'unpriced');
  const costs = page.getByRole('region', { name: 'Server costs' });
  await expect(costs).not.toContainText('$');
  await expect(costs).toContainText('No savings estimate available');
});

test('incomplete sizing stays visible without expanding technical details', async ({ page }) => {
  await openCosts(page, 'incomplete');
  await expect(page.getByText(/Savings may be overstated/)).toBeVisible();
});

test('older audits retain the readable cost story without offering unavailable projections', async ({ page }) => {
  await openCosts(page, 'legacy');
  const costs = page.getByRole('region', { name: 'Server costs' });
  await expect(costs).toContainText('$386/mo');
  await expect(costs.getByRole('group', { name: 'Savings estimate mode' })).toHaveCount(0);
});
