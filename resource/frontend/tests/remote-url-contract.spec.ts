import { test, expect, type Route } from '@playwright/test';

const backendStatus = {
  sql_connection_configured: true,
  sql_connection_healthy: true,
  instance_has_compatible_sql: true,
  table_prefix: 'test',
  effective_backend: 'sql',
  connection_name: 'admin_toolkit',
  sqlite_exists: false,
  sqlite_has_data: false,
  migration_running: false,
};

async function fulfillJson(route: Route, body: unknown) {
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

test('backend URL helper does not double-prefix Dataiku webapp backend paths', async ({ page }) => {
  const requested: string[] = [];
  await page.addInitScript(() => {
    (window as unknown as { dataiku: { getWebAppBackendUrl(path: string): string } }).dataiku = {
      getWebAppBackendUrl(path: string) {
        return `/web-apps-backends/PYTHONAUDIT_TEST/run123${path.startsWith('/') ? path : `/${path}`}`;
      },
    };
  });
  const mockApi = async (route: Route) => {
    requested.push(route.request().url());
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/api/tracking/backend-status')) return fulfillJson(route, backendStatus);
    if (path.endsWith('/api/mode')) return fulfillJson(route, { mode: 'live' });
    if (path.endsWith('/api/hosts')) return fulfillJson(route, [{ id: 'local', label: 'Local DSS', url: '' }]);
    if (path.endsWith('/api/hosts/check')) {
      return fulfillJson(route, { ok: true, pluginInstalled: true, pluginVersion: 'test', adminToolkitProjectExists: true });
    }
    if (path.endsWith('/api/overview')) {
      return fulfillJson(route, { dssVersion: '14.0.0', instanceInfo: {}, disabledFeatures: {} });
    }
    if (path.endsWith('/api/settings/raw')) return fulfillJson(route, {});
    return fulfillJson(route, {});
  };
  await page.route('**/api/**', mockApi);
  await page.route('**/web-apps-backends/**/api/**', mockApi);

  await page.goto('/');
  await page.getByRole('button', { name: /Local DSS/ }).click();
  await expect(page.locator('aside')).toBeVisible({ timeout: 30_000 });

  expect(requested.some((url) => url.includes('/web-apps-backends/') && url.match(/web-apps-backends/g)?.length > 1)).toBe(false);
});
