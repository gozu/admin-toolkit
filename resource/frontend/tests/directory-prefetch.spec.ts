import { test, expect, helpers } from './fixtures';

const rootTreeResponse = {
  root: {
    name: 'dss_data',
    path: '/data/dataiku/dss_data',
    size: 1000,
    ownSize: 0,
    isDirectory: true,
    children: [
      {
        name: 'projects',
        path: '/data/dataiku/dss_data/projects',
        size: 600,
        ownSize: 0,
        isDirectory: true,
        children: [],
        fileCount: 10,
        depth: 1,
        hasHiddenChildren: true,
      },
      {
        name: 'code-envs',
        path: '/data/dataiku/dss_data/code-envs',
        size: 250,
        ownSize: 0,
        isDirectory: true,
        children: [],
        fileCount: 5,
        depth: 1,
        hasHiddenChildren: true,
      },
      {
        name: 'jobs',
        path: '/data/dataiku/dss_data/jobs',
        size: 100,
        ownSize: 0,
        isDirectory: true,
        children: [],
        fileCount: 2,
        depth: 1,
        hasHiddenChildren: true,
      },
      {
        name: 'config',
        path: '/data/dataiku/dss_data/config',
        size: 50,
        ownSize: 0,
        isDirectory: true,
        children: [],
        fileCount: 1,
        depth: 1,
        hasHiddenChildren: false,
      },
    ],
    fileCount: 18,
    depth: 0,
    hasHiddenChildren: false,
  },
  totalSize: 1000,
  totalFiles: 18,
  rootPath: '/data/dataiku/dss_data',
  scope: 'dss',
  projectKey: null,
};

function expandedNode(path: string, name: string, size: number) {
  return {
    node: {
      name,
      path,
      size,
      ownSize: 0,
      isDirectory: true,
      children: [
        {
          name: `${name}-child`,
          path: `${path}/${name}-child`,
          size: Math.max(1, Math.floor(size / 2)),
          ownSize: Math.max(1, Math.floor(size / 2)),
          isDirectory: false,
          children: [],
          fileCount: 1,
          depth: 1,
          hasHiddenChildren: false,
        },
      ],
      fileCount: 1,
      depth: 0,
      hasHiddenChildren: false,
    },
  };
}

test.describe('Directory Prefetch', () => {
  test('defers dir-tree until Directory page and bounds speculative expand count', async ({ page }) => {
    const dirTreeRequests: string[] = [];

    await page.route('**/api/tracking/backend-status', async (route) => {
      await route.fulfill({
        json: {
          sql_connection_configured: false,
          sql_connection_healthy: null,
          instance_has_compatible_sql: false,
          table_prefix: null,
          effective_backend: 'sqlite',
          connection_name: null,
          sqlite_exists: true,
          sqlite_has_data: true,
          migration_running: false,
        },
      });
    });

    await page.route('**/api/overview', async (route) => {
      await route.fulfill({
        json: {
          dssVersion: 'test',
          disabledFeatures: {},
        },
      });
    });

    await page.route('**/api/dir-tree**', async (route) => {
      const url = new URL(route.request().url());
      dirTreeRequests.push(`${url.pathname}?${url.searchParams.toString()}`);

      const path = url.searchParams.get('path');
      if (!path) {
        await route.fulfill({ json: rootTreeResponse });
        return;
      }

      if (path === '/data/dataiku/dss_data/projects') {
        await route.fulfill({ json: expandedNode(path, 'projects', 600) });
        return;
      }
      if (path === '/data/dataiku/dss_data/code-envs') {
        await route.fulfill({ json: expandedNode(path, 'code-envs', 250) });
        return;
      }
      if (path === '/data/dataiku/dss_data/jobs') {
        await route.fulfill({ json: expandedNode(path, 'jobs', 100) });
        return;
      }

      await route.fulfill({ json: { node: null } });
    });

    await helpers.waitForApp(page);
    await page.waitForTimeout(500);
    expect(dirTreeRequests).toHaveLength(0);

    await page.getByRole('button', { name: /Dir Usage/i }).click();

    await page.waitForResponse((response) => response.url().includes('/api/dir-tree?maxDepth=3&scope=dss'));
    await expect(page.getByText('Treemap View')).toBeVisible();

    await page.waitForTimeout(1500);

    expect(dirTreeRequests[0]).toBe('/api/dir-tree?maxDepth=3&scope=dss');
    expect(dirTreeRequests).toHaveLength(4);
    expect(dirTreeRequests.slice(1).sort()).toEqual([
      '/api/dir-tree?path=%2Fdata%2Fdataiku%2Fdss_data%2Fcode-envs&maxDepth=3&scope=dss',
      '/api/dir-tree?path=%2Fdata%2Fdataiku%2Fdss_data%2Fjobs&maxDepth=3&scope=dss',
      '/api/dir-tree?path=%2Fdata%2Fdataiku%2Fdss_data%2Fprojects&maxDepth=3&scope=dss',
    ]);
  });
});
