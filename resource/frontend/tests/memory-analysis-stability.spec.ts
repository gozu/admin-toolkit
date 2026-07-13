import { test, expect, type Route } from '@playwright/test';

const overviewMemory = {
  total: '31 GB',
  used: '27 GB',
  free: '1 GB',
  available: '7 GB',
  'buff/cache': '3 GB',
};

const streamedSample = {
  ok: true,
  ts: 1_783_965_600,
  cpu: {
    user: 100,
    nice: 0,
    system: 50,
    idle: 850,
    iowait: 0,
    irq: 0,
    softirq: 0,
    steal: 0,
    cpuCount: 4,
  },
  mem: {
    totalKb: 32_212_255,
    freeKb: 660_480,
    availableKb: 7_423_918,
    buffersKb: 100_000,
    cachedKb: 2_740_000,
    swapTotalKb: 0,
    swapFreeKb: 0,
  },
};

async function json(route: Route, body: unknown): Promise<void> {
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

test('System Memory waits for live data without replacing the analysis snapshot', async ({ page }) => {
  let releaseSample!: () => void;
  const sampleGate = new Promise<void>((resolve) => { releaseSample = resolve; });

  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;

    if (path.endsWith('/api/mode')) return json(route, { mode: 'live', version: 'test' });
    if (path.endsWith('/api/hosts')) {
      return json(route, [{ id: 'local', label: 'Local DSS', url: '' }]);
    }
    if (path.endsWith('/api/hosts/check')) {
      return json(route, {
        ok: true,
        pluginInstalled: true,
        adminToolkitProjectExists: true,
      });
    }
    if (path.endsWith('/api/tracking/backend-status')) {
      return json(route, {
        sql_connection_configured: false,
        effective_backend: 'sqlite',
        sqlite_exists: true,
      });
    }
    if (path.endsWith('/api/overview')) {
      return json(route, {
        dssVersion: '14.0.0',
        cpuCores: '4',
        memoryInfo: overviewMemory,
        instanceInfo: {},
        disabledFeatures: {},
      });
    }
    if (path.endsWith('/api/settings/raw')) {
      return json(route, {
        maxRunningActivities: 10,
        jekSettings: { maxRunningJobs: 1 },
        containerSettings: { executionConfigs: [] },
        cgroupSettings: {
          enabled: true,
          cgroups: [{ limits: [{ key: 'memory', value: '38g' }] }],
        },
      });
    }
    if (path.endsWith('/api/project-standards/raw')) return json(route, {});
    if (path.endsWith('/api/java-memory')) {
      return route.fulfill({
        status: 200,
        contentType: 'text/plain',
        body: [
          'export DKU_BACKEND_JAVA_OPTS="-Xmx8g"',
          'export DKU_JEK_JAVA_OPTS="-Xmx2g"',
        ].join('\n'),
      });
    }
    if (path.endsWith('/api/host/process-metrics')) {
      return json(route, { ok: true, processes: [] });
    }
    if (path.endsWith('/api/host/resource-stream')) {
      await sampleGate;
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: `event: sample\ndata: ${JSON.stringify(streamedSample)}\n\n`,
      });
    }

    return json(route, {});
  });

  await page.goto('/');
  await page.getByRole('button', { name: /Local DSS/ }).click();
  await expect(page.locator('aside')).toBeVisible({ timeout: 30_000 });
  await page.getByRole('button', { name: /^Resources$/ }).click();

  const analysis = page.locator('#memory-analysis');
  const systemMemory = page.locator('#memory-chart');

  await expect(analysis).toContainText('Instance total31 GB');
  await expect(systemMemory).toContainText('Waiting for live memory sample');
  await expect(systemMemory).not.toContainText('Total Memory31 GB');

  releaseSample();

  await expect(systemMemory).toContainText('Used Memory27.38 GB');
  await expect(systemMemory).toContainText('Total Memory30.72 GB');
  await expect(analysis).not.toContainText('30.72 GB');
  await expect(analysis).not.toContainText('3026 GB');
});
