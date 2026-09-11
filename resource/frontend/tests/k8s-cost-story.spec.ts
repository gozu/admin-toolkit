import { expect, test, type Page } from '@playwright/test';

async function openCosts(page: Page, variant: 'normal' | 'unpriced' | 'incomplete' | 'legacy' | 'retained' = 'normal') {
  const missingUsage = variant === 'incomplete' || variant === 'retained';
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
      realCpuMilli: missingUsage && i === 0 && j === 0 ? null : 10,
      realMemMib: missingUsage && i === 0 && j === 0 ? null : Math.floor(memory[i] / counts[i]) + (j === 0 ? memory[i] % counts[i] : 0),
      requestedCpuMilli: missingUsage && i === 0 && j === 0 ? 0 : 100, requestedMemMib: i === 0 ? j === 0 ? (missingUsage ? 0 : 20000) : 100 : Math.floor(capacities[i] / counts[i] / 2),
    })),
  }));
  const all = nodeBreakdown.flatMap(n => n.pods.map(p => ({
    ...p, key: `${p.ns}/${p.name}`, sourceNode: n.name, perNodeService: p.isDaemonSet,
    reservedCpuMilli: p.isSystem ? 100 : Math.ceil((p.realCpuMilli ?? 0) / .75), reservedMemMib: p.isSystem ? Math.max(p.requestedMemMib, p.realMemMib ?? 0) : Math.ceil((p.realMemMib ?? 0) / .75),
  })));
  const work = all.filter(p => !p.isSystem);
  const retainedNode = {
    id: 'node-1', instanceType: types[0], hourly: prices[0], cpuCapacityMilli: 7910, memoryCapacityMib: capacities[0],
    pods: all.filter(p => p.sourceNode === 'node-1').map(p => ({ ...p, statusReason: p.realMemMib == null ? 'CrashLoopBackOff' : 'Running' })),
    retainedForPods: ['analytics/pod-1-1'], sizeChecks: [],
  };
  const placementNodes = variant === 'retained' ? [retainedNode, {
    id: 'proposed-1', instanceType: types[2], hourly: prices[2], cpuCapacityMilli: 7910, memoryCapacityMib: capacities[2],
    pods: [...work.filter(p => p.sourceNode !== 'node-1'), ...all.filter(p => p.sourceNode === 'node-3' && p.isSystem)], sizeChecks: [],
  }] : [
    { id: 'proposed-1', instanceType: 'm8i.large', hourly: .10584, cpuCapacityMilli: 1930, memoryCapacityMib: 7376, pods: [...work.slice(0, 3), ...all.filter(p => p.sourceNode === 'node-1' && p.isSystem)], sizeChecks: [] },
    { id: 'proposed-2', instanceType: 'm8i.2xlarge', hourly: .42336, cpuCapacityMilli: 7910, memoryCapacityMib: 29903, pods: [...work.slice(3), ...all.filter(p => p.sourceNode === 'node-3' && p.isSystem)], sizeChecks: [{ instanceType: 'm8i.xlarge', blockers: [{ kind: 'memory', required: [...work.slice(3), ...all.filter(p => p.sourceNode === 'node-3' && p.isSystem)].reduce((sum, p) => sum + p.reservedMemMib, 0), capacity: 14052 }] }] },
  ];
  const projection = {
    savingsMonthly: variant === 'retained' ? 493.95 : 725.74, floorMonthly: variant === 'retained' ? 618.10 : 386.31,
    placementNodes, placementComplete: variant !== 'incomplete',
    unknownSizingPods: missingUsage ? ['analytics/pod-1-1'] : [], unplaceablePods: [],
  };
  const requested = { ...projection, savingsMonthly: 0, floorMonthly: 1112.05, placementNodes: nodeBreakdown.map((n, i) => ({
    id: `requested-${i + 1}`, instanceType: n.instanceType, hourly: n.hourly,
    cpuCapacityMilli: parseInt(n.allocatableCpu), memoryCapacityMib: capacities[i], pods: all.filter(p => p.sourceNode === n.name).map(p => ({ ...p, reservedCpuMilli: Math.max(p.requestedCpuMilli, p.realCpuMilli ?? 0), reservedMemMib: Math.max(p.requestedMemMib, p.realMemMib ?? 0) })),
    sizeChecks: i === 0 ? [{ instanceType: 'm8i.xlarge', blockers: [{ kind: 'memory', required: 28192, capacity: 13616 }] }] : [],
  })).map(n => variant === 'retained' && n.id === 'requested-1' ? {
    ...n, id: 'node-1', retainedForPods: ['analytics/pod-1-1'],
    pods: n.pods.map(p => ({ ...p, statusReason: p.realMemMib == null ? 'CrashLoopBackOff' : 'Running' })),
  } : n) };
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

test('static comparison uses matching reservation widths and priced calculated destinations', async ({ page }) => {
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
  const source = before.getByRole('button', { name: 'analytics/pod-1-1: 640 MiB used', exact: true });
  const destination = after.getByRole('button', { name: 'analytics/pod-1-1: 640 MiB used', exact: true });
  const sourceBox = await source.boundingBox();
  const destinationBox = await destination.boundingBox();
  expect(destinationBox!.width / sourceBox!.width).toBeCloseTo(1, 2);
  expect(await source.evaluate(el => getComputedStyle(el).backgroundColor)).toBe(await destination.evaluate(el => getComputedStyle(el).backgroundColor));
  const track = await before.locator('[data-node="node-1"] .kp-track').boundingBox();
  expect(sourceBox!.width / track!.width).toBeCloseTo(640 / 29903, 3);
  await source.click();
  await expect(destination).toHaveAttribute('aria-pressed', 'true');
  await expect(costs.locator('.kp-selection')).toContainText('node-1 → proposed-1');
  expect(await destination.evaluate(el => getComputedStyle(el).animationName)).toBe('none');
  await costs.getByRole('button', { name: 'CPU', exact: true }).click();
  await expect(after.getByRole('button', { name: 'analytics/pod-1-1: 14m used', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(before.getByRole('button', { name: 'analytics/pod-1-1: 14m used', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await costs.getByRole('button', { name: 'Reserved usage', exact: true }).click();
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

test('unknown sizing is flagged and is never drawn as known zero', async ({ page }) => {
  await openCosts(page, 'incomplete');
  const costs = page.getByRole('region', { name: 'Server costs' });
  await expect(costs).toContainText('Incomplete projection');
  await expect(costs).not.toContainText('$725.74/mo');
  await expect(costs.locator('[data-node="node-1"]')).toContainText('1 unsized');
  await expect(costs.locator('.kp-pod[aria-label^="analytics/pod-1-1:"]')).toHaveCount(0);
  await costs.locator('[data-node="node-1"] summary').click();
  await expect(costs.locator('[data-node="node-1"] .kp-pod-list')).toContainText('unknown');
});

test('a crash-looping pod retains its server and rent without hiding other savings', async ({ page }) => {
  await openCosts(page, 'retained');
  const costs = page.getByRole('region', { name: 'Server costs' });
  const after = costs.getByRole('region', { name: 'Proposed servers' });
  const retained = after.locator('[data-node="node-1"]');
  await expect(costs).toContainText('$618.10/mo');
  await expect(costs).toContainText('$493.95/mo');
  await expect(costs).not.toContainText('Incomplete projection');
  await expect(retained.locator('.kp-price')).toHaveText('$309.05/mo');
  await expect(retained).toContainText('1 unsized');
  await expect(retained.locator('[data-pod-key="analytics/pod-1-1"]')).toHaveCount(0);
  await retained.getByText('Kept at current size · missing pod sizing', { exact: true }).click();
  await expect(retained.locator('.kp-retained')).toContainText('analytics/pod-1-1 · CrashLoopBackOff');
  await expect(retained.locator('.kp-retained')).toContainText('Its full rent is included');
  await costs.screenshot({ path: '/tmp/atk-retained-server-dark.png' });
  await costs.getByRole('button', { name: 'Reserved usage', exact: true }).click();
  await expect(costs).toContainText('$0.00/mo');
  await expect(retained.locator('.kp-price')).toHaveText('$309.05/mo');
  await expect(retained).toContainText('Kept at current size');
  await expect(costs).not.toContainText('Incomplete projection');
  await costs.getByRole('button', { name: 'Usage +33%', exact: true }).click();
  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'light'));
  await costs.screenshot({ path: '/tmp/atk-retained-server-light.png' });
  await page.setViewportSize({ width: 760, height: 1000 });
  expect(await costs.evaluate(el => el.scrollWidth > el.clientWidth)).toBe(false);
});

test('mode changes reservation sizes while preserving pod colors and user identities', async ({ page }) => {
  await openCosts(page);
  const costs = page.getByRole('region', { name: 'Server costs' });
  const before = costs.getByRole('region', { name: 'Current servers' });
  const after = costs.getByRole('region', { name: 'Proposed servers' });
  const key = 'analytics/pod-1-1';
  const source = before.locator(`[data-pod-key="${key}"]`);
  const sourceColor = await source.evaluate(el => getComputedStyle(el).backgroundColor);
  const sourceKeys = await before.locator('.kp-pod[data-pod-key^="analytics/"]').evaluateAll(els => els.map(el => el.getAttribute('data-pod-key')).sort());
  await expect(costs.locator('.kp-legend > span')).toHaveText(['User pods', 'System pods']);
  await expect(costs.locator('.kp-reservation-outline, .kp-resource-totals, .kp-size-reason, .kp-unattributed')).toHaveCount(0);
  for (const [mode, value] of [['Reserved usage', 20000], ['Usage +33%', 640]] as const) {
    await costs.getByRole('button', { name: mode, exact: true }).click();
    const destination = after.locator(`[data-pod-key="${key}"]`);
    const track = destination.locator('..');
    const cap = mode === 'Reserved usage' ? 29903 : 7376;
    expect((await destination.boundingBox())!.width / (await track.boundingBox())!.width).toBeCloseTo(value / cap, 3);
    expect((await source.boundingBox())!.width / (await source.locator('..').boundingBox())!.width).toBeCloseTo(value / 29903, 3);
    expect(await destination.evaluate(el => getComputedStyle(el).backgroundColor)).toBe(sourceColor);
    await expect.poll(() => costs.evaluate((el, podKey) => {
      const blocks = el.querySelectorAll(`[data-pod-key="${podKey}"]`);
      return blocks[1].getBoundingClientRect().width / blocks[0].getBoundingClientRect().width;
    }, key)).toBeCloseTo(1, 2);
    await expect(before.locator('.kp-heading small')).toHaveText(mode);
    await expect(after.locator('.kp-heading small')).toHaveText(mode);
    expect(await after.locator('.kp-pod[data-pod-key^="analytics/"]').evaluateAll(els => els.map(el => el.getAttribute('data-pod-key')).sort())).toEqual(sourceKeys);
    await source.click();
    await expect(destination).toHaveAttribute('aria-pressed', 'true');
    await expect(source).toHaveAttribute('aria-label', `${key}: ${value} MiB ${mode === 'Usage +33%' ? 'used' : 'reserved'}`);
    await expect(destination).toHaveAttribute('aria-label', `${key}: ${value} MiB ${mode === 'Usage +33%' ? 'used' : 'reserved'}`);
    await expect(costs.locator('.kp-selection')).toContainText(`${value} MiB ${mode === 'Usage +33%' ? 'used (+33% sizing)' : 'reserved'} · 480 MiB measured`);
    if (mode === 'Usage +33%') expect((await costs.locator('.kp-server').allTextContents()).join(' ')).not.toContain('reserved');
    await source.click();
  }
});

test('old audits show current nodes and request a new scan rather than inventing destinations', async ({ page }) => {
  await openCosts(page, 'legacy');
  const costs = page.getByRole('region', { name: 'Server costs' });
  await expect(costs).toContainText('Run a new audit to calculate pod destinations.');
  await expect(costs.getByRole('region', { name: 'Proposed servers' }).locator('.kp-server')).toHaveCount(0);
});

test('pod detail columns align and user/system names have distinct colors', async ({ page }) => {
  await openCosts(page);
  await page.getByRole('button', { name: 'node-1', exact: true }).click();
  const table = page.getByRole('region', { name: 'Pods on this node', exact: true }).locator('table');
  await expect(table).toBeVisible();
  await expect(table.locator('tbody tr')).toHaveCount(5);
  await expect(table.locator('tbody tr.k8s-pod-user')).toHaveCount(1);
  await expect(table.locator('tbody tr.k8s-pod-system')).toHaveCount(4);
  for (const theme of ['dark', 'light']) {
    await page.evaluate(theme => document.documentElement.setAttribute('data-theme', theme), theme);
    const dimensions = await table.evaluate(el => {
      const headers = [...el.querySelectorAll('th')].map(h => h.getBoundingClientRect());
      return [...el.querySelectorAll('tbody tr')].flatMap(row => [...row.querySelectorAll('td')].map((cell, i) => {
        const rect = cell.getBoundingClientRect();
        return { leftDelta: rect.left - headers[i].left, rightDelta: rect.right - headers[i].right, align: getComputedStyle(cell).textAlign, index: i };
      }));
    });
    for (const cell of dimensions) {
      expect(Math.abs(cell.leftDelta)).toBeLessThan(.5);
      expect(Math.abs(cell.rightDelta)).toBeLessThan(.5);
      if (cell.index >= 2) expect(cell.align).toBe('right');
    }
    const user = table.locator('tr.k8s-pod-user button').first();
    const system = table.locator('tr.k8s-pod-system button').first();
    expect(await user.evaluate(el => getComputedStyle(el).color)).not.toBe(await system.evaluate(el => getComputedStyle(el).color));
    await table.screenshot({ path: `/tmp/atk-pod-detail-${theme}.png` });
  }
});
