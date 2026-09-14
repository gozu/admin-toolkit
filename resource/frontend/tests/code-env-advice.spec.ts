import { test, expect } from '@playwright/test';

for (const height of [600, 1000]) {
  test(`remediation scrolls to the final section at ${height}px height`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height });
    const answer = '## Diagnosis\n\nUse a compatible package.\n\n' +
      Array.from({ length: 35 }, (_, i) => `### Step ${i + 1}\n\nRebuild and check the environment.\n\n`).join('') +
      '## Verify\n\nFinal verification instruction.\n';
    let releaseAdvice!: () => void;
    const adviceReady = new Promise<void>((resolve) => { releaseAdvice = resolve; });
    const event = (name: string, data: unknown) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;
    await page.route('**/api/**', async (route) => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = {};
      if (path.endsWith('/api/mode')) body = { mode: 'live', version: 'test', runningVersion: 'test' };
      if (path.endsWith('/api/hosts')) body = [{ id: 'local', label: 'Local DSS', url: '' }];
      if (path.endsWith('/api/hosts/check')) {
        body = { ok: true, pluginInstalled: true, adminToolkitProjectExists: true };
      }
      if (path.endsWith('/api/llms')) {
        body = { llms: [{ id: 'test-model', label: 'Test model', connection: 'Demo', model: 'Test model' }] };
      }
      if (path.endsWith('/api/code-envs/broken/scan')) {
        return route.fulfill({ contentType: 'text/event-stream', body:
          event('init', { total: 1, sizesAvailable: false }) +
          event('env', {
            name: 'demo-env', lang: 'PYTHON', deploymentMode: 'DESIGN', pythonVersion: 'PYTHON311',
            status: 'FAILED', failureClass: 'BUILD_FAILURE', failureLabel: 'Build failed',
            logName: 'build.log', errorExcerpt: 'Incompatible package', createdOn: null,
            lastBuildOn: null, sizeBytes: null, usageCount: 0, usages: [], usagesTruncated: false,
          }) + event('done', { total: 1, failed: 1, ok: 0, indeterminate: 0 }),
        });
      }
      if (path.endsWith('/api/code-envs/broken/advice')) {
        await adviceReady;
        return route.fulfill({ contentType: 'text/event-stream', body:
          event('chunk', { text: answer }) + event('done', { llmId: 'test-model' }),
        });
      }
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    });
    await page.goto('/');
    await page.getByRole('button', { name: /Local DSS/ }).click();
    await page.locator('[data-page-id="code-envs-broken"]').click();
    await page.getByRole('button', { name: 'Pick an LLM…' }).click();
    await page.locator('[data-model-id="test-model"]').click();
    await page.getByRole('button', { name: 'Ask LLM', exact: true }).click();
    const modal = page.locator('.modal-content');
    await expect(modal.getByText('Consulting Test model…')).toBeVisible();
    await modal.evaluate((el) => Promise.all(el.getAnimations().map((animation) => animation.finished)));
    releaseAdvice();
    await expect(modal.getByRole('heading', { name: 'Diagnosis', exact: true })).toBeVisible();
    const title = modal.getByRole('heading', { name: 'Remediation — demo-env' });
    const titleBefore = await title.boundingBox();
    const body = modal.locator(':scope > .overflow-auto');
    await body.hover();
    await page.mouse.wheel(0, 10000);
    await expect.poll(() => body.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
    await expect(modal.getByText('Final verification instruction.')).toBeInViewport();
    await expect(title).toBeInViewport();
    const titleAfter = await title.boundingBox();
    expect(titleAfter!.y).toBeCloseTo(titleBefore!.y, 0);
    await modal.getByRole('button', { name: 'Close modal' }).click();
    await expect(modal).not.toBeVisible();
  });
}
