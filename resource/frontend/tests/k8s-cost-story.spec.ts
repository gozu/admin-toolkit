import { expect, test, type Page } from '@playwright/test';

async function openCosts(page: Page, variant: 'normal' | 'unpriced' | 'incomplete' | 'legacy' = 'normal') {
  const types = ['m8i.2xlarge', 't3.medium', 'm8i.2xlarge', 'm8i.xlarge', 'm8i.2xlarge'];
  const prices = [.42336, .0416, .42336, .21168, .42336];
  const counts = [5, 5, 11, 13, 6];
  const users = [1, 0, 5, 8, 2];
  const memory = [2392, 1812, 8371, 5619, 16148];
  const capacities = [29903, 3293, 29903, 14052, 29903];
  const nodeBreakdown = types.map((instanceType, i) => ({
    name: `node-${i + 1}`, instanceType, hourly: prices[i], ready: true, isGpu: false,
    podCount: counts[i], userPodCount: users[i], allocatableCpu: i === 1 ? '1930m' : i === 3 ? '3920m' : '7910m',
    allocatableMemory: `${capacities[i]}Mi`, cpuUsageMilli: counts[i] * 10,
    memUsageMib: memory[i], cpuPct: 1, memPct: memory[i] / capacities[i] * 100,
    labels: {}, pods: Array.from({ length: counts[i] }, (_, j) => ({
      name: `pod-${i + 1}-${j + 1}`, ns: j < users[i] ? 'analytics' : 'kube-system',
      isSystem: j >= users[i], isDaemonSet: j >= users[i], phase: 'Running', ready: true, restartCount: 0,
      realCpuMilli: variant === 'incomplete' && i === 0 && j === 0 ? null : 10,
      realMemMib: variant === 'incomplete' && i === 0 && j === 0 ? null : Math.floor(memory[i] / counts[i]) + (j === 0 ? memory[i] % counts[i] : 0),
      requestedCpuMilli: 100, requestedMemMib: 2048,
    })),
  }));
  const all = nodeBreakdown.flatMap(n => n.pods.map(p => ({
    ...p, key: `${p.ns}/${p.name}`, sourceNode: n.name, perNodeService: p.isDaemonSet,
    reservedCpuMilli: 100, reservedMemMib: Math.ceil((p.realMemMib ?? 0) / .75),
  })));
  const work = all.filter(p => !p.isSystem);
  const placementNodes = [
    { id: 'proposed-1', instanceType: 'm8i.large', hourly: .10584, cpuCapacityMilli: 1930, memoryCapacityMib: 7376, pods: [...work.slice(0, 3), ...all.filter(p => p.sourceNode === 'node-1' && p.isSystem)] },
    { id: 'proposed-2', instanceType: 'm8i.2xlarge', hourly: .42336, cpuCapacityMilli: 7910, memoryCapacityMib: 29903, pods: [...work.slice(3), ...all.filter(p => p.sourceNode === 'node-3' && p.isSystem)] },
  ];
  const projection = {
    savingsMonthly: 725.74, floorMonthly: 386.31,
    placementNodes, placementComplete: variant !== 'incomplete',
    unknownSizingPods: variant === 'incomplete' ? ['analytics/pod-1-1'] : [], unplaceablePods: [],
  };
  const requested = { ...projection, savingsMonthly: 0, floorMonthly: 1112.05, placementNodes: nodeBreakdown.map((n, i) => ({
    id: `requested-${i + 1}`, instanceType: n.instanceType, hourly: n.hourly,
    cpuCapacityMilli: parseInt(n.allocatableCpu), memoryCapacityMib: capacities[i], pods: all.filter(p => p.sourceNode === n.name),
  })) };
  const finding = {
    id: 'floor', rule: 'cluster-floor-projection', severity: 'high', category: 'cost',
    title: 'Server consolidation', summary: 'Placement details', costImpactPerMonth: projection.savingsMonthly,
    remediation: [], evidence: variant === 'legacy' ? { currentMonthly: 1112.05, floorMonthly: 386.31 } : {
      currentMonthly: 1112.05, ...projection, projections: { rightsized: projection, requests: requested },
    },
  };
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({ json: body });
    if (path.endsWith('/api/mode')) return json({ mode: 'live', version: 'test', runningVersion: 'test' });
    if (path.endsWith('/api/hosts')) return json([{ id: 'local', label: 'Local DSS', url: '' }]);
    if (path.endsWith('/api/hosts/check')) return json({ ok: true, pluginInstalled: true, adminToolkitProjectExists: true });
    if (path.endsWith('/api/k8s-insights/clusters')) return json({ clusters: [{ id: 'demo', name: 'Demo', state: 'RUNNING' }] });
    if (path.endsWith('/api/k8s-insights/stream')) return route.fulfill({ contentType: 'text/event-stream', body: `event: done\ndata: ${JSON.stringify({
      ok: true, cluster: { id: 'demo', nodeCount: 5, podCount: 40 },
      costSnapshot: { currentMonthly: 1112.05, currentHourly: 1.52336, nodes: nodeBreakdown.map(n => ({ name: n.name, hourly: n.hourly })) },
      pricingStatus: { ok: variant !== 'unpriced', source: 'test', error: 'Prices unavailable' },
      findings: [finding], findingsCount: 1, probes: {}, nodeBreakdown,
    })}\n\n` });
    return json({});
  });
  await page.goto('/');
  await page.getByRole('button', { name: /Local DSS/ }).click();
  await page.locator('aside').getByText('K8s Insights', { exact: true }).click();
  await expect(page.getByRole('region', { name: 'Server costs' })).toBeVisible();
}

test('static comparison uses measured pod widths and priced calculated destinations', async ({ page }) => {
  await openCosts(page);
  const costs = page.getByRole('region', { name: 'Server costs' });
  const before = costs.getByRole('region', { name: 'Current servers' });
  const after = costs.getByRole('region', { name: 'Proposed servers' });
  await expect(costs).toContainText('$1,112.05/mo');
  await expect(costs).toContainText('$386.31/mo');
  await expect(costs).toContainText('$725.74/mo');
  await expect(before.locator('.kp-server')).toHaveCount(5);
  await expect(after.locator('.kp-server')).toHaveCount(2);
  await expect(before.locator('[data-node="node-2"] .kp-price')).toHaveText('$30.37/mo');
  await expect(after.locator('[data-node="proposed-1"] .kp-price')).toHaveText('$77.26/mo');
  await expect(after.locator('[data-node="proposed-2"] .kp-price')).toHaveText('$309.05/mo');
  const source = before.getByRole('button', { name: 'analytics/pod-1-1: 480 MiB', exact: true });
  const destination = after.getByRole('button', { name: 'analytics/pod-1-1: 480 MiB', exact: true });
  const sourceBox = await source.boundingBox();
  const destinationBox = await destination.boundingBox();
  expect(sourceBox!.width).toBeCloseTo(destinationBox!.width, 1);
  const track = await before.locator('[data-node="node-1"] .kp-track').boundingBox();
  expect(sourceBox!.width / track!.width).toBeCloseTo(480 / 29903, 3);
  await source.click();
  await expect(destination).toHaveAttribute('aria-pressed', 'true');
  await expect(costs.locator('.kp-selection')).toContainText('node-1 → proposed-1');
  expect(await destination.evaluate(el => getComputedStyle(el).animationName)).toBe('none');
  await costs.getByRole('button', { name: 'CPU', exact: true }).click();
  await expect(after.getByRole('button', { name: 'analytics/pod-1-1: 10m', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await costs.getByRole('button', { name: 'Current requests', exact: true }).click();
  await expect(after.locator('.kp-server')).toHaveCount(5);
  await expect(costs).toContainText('$0.00/mo');
  await costs.getByRole('button', { name: 'Usage +33%', exact: true }).click();
  await costs.getByRole('button', { name: 'Memory', exact: true }).click();
  await expect(costs).not.toContainText('Where your money goes');
  await expect(costs).not.toContainText('Fit the work onto fewer servers');
  await expect(costs.getByRole('button', { name: /animation|moves/i })).toHaveCount(0);
  await expect(page.locator('aside').getByText('Feedback', { exact: true })).toHaveCount(0);
  await source.click();
  await expect(destination).toHaveAttribute('aria-pressed', 'false');
  await costs.screenshot({ path: '/tmp/atk-placement-dark.png' });
  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'light'));
  await costs.screenshot({ path: '/tmp/atk-placement-light.png' });
  await page.setViewportSize({ width: 760, height: 1000 });
  await costs.screenshot({ path: '/tmp/atk-placement-narrow.png' });
  expect(await costs.evaluate(el => el.scrollWidth > el.clientWidth)).toBe(false);
});

test('unpriced results never show fabricated rents', async ({ page }) => {
  await openCosts(page, 'unpriced');
  const costs = page.getByRole('region', { name: 'Server costs' });
  await expect(costs).not.toContainText('$');
  await expect(costs.locator('.kp-price').first()).toHaveText('Price unavailable');
});

test('unknown usage is flagged and is never drawn as a zero-sized measured pod', async ({ page }) => {
  await openCosts(page, 'incomplete');
  const costs = page.getByRole('region', { name: 'Server costs' });
  await expect(costs).toContainText('Incomplete projection');
  await expect(costs).not.toContainText('$725.74/mo');
  await expect(costs.locator('[data-node="node-1"]')).toContainText('1 unmeasured');
  await expect(costs.locator('.kp-pod[aria-label^="analytics/pod-1-1:"]')).toHaveCount(0);
  await costs.locator('[data-node="node-1"] summary').click();
  await expect(costs.locator('[data-node="node-1"] .kp-pod-list')).toContainText('unknown');
});

test('old audits show current nodes and request a new scan rather than inventing destinations', async ({ page }) => {
  await openCosts(page, 'legacy');
  const costs = page.getByRole('region', { name: 'Server costs' });
  await expect(costs).toContainText('Run a new audit to calculate pod destinations.');
  await expect(costs.getByRole('region', { name: 'Proposed servers' }).locator('.kp-server')).toHaveCount(0);
});
