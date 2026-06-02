import { test } from '@playwright/test';

test('debug campaign tab matching', async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto('http://localhost:10000/webapps/liveparser/', {
    waitUntil: 'domcontentloaded',
    timeout: 30_000,
  });
  await page.waitForSelector('aside', { timeout: 60_000 });
  await page.waitForTimeout(1_000);

  // Click outreach
  await page.locator('aside button').filter({ hasText: 'Outreach' }).first().click();
  await page.waitForTimeout(3_000);

  // Try to find the "Deprecated Python Versions" button
  const depBtn = page.locator('button').filter({ hasText: 'Deprecated Python Versions' });
  const depCount = await depBtn.count();
  console.log(`"Deprecated Python Versions" button count: ${depCount}`);
  for (let i = 0; i < depCount; i++) {
    const vis = await depBtn.nth(i).isVisible().catch(() => false);
    const text = await depBtn.nth(i).textContent();
    const bb = await depBtn.nth(i).boundingBox().catch(() => null);
    console.log(`  ${i}: visible=${vis}, text=${JSON.stringify(text?.trim())}, box=${JSON.stringify(bb)}`);
  }

  // Also try Code Env Ownership which we know has recipients
  const ceoBtn = page.locator('button').filter({ hasText: 'Code Env Ownership' });
  const ceoCount = await ceoBtn.count();
  console.log(`\n"Code Env Ownership" button count: ${ceoCount}`);
  for (let i = 0; i < ceoCount; i++) {
    const vis = await ceoBtn.nth(i).isVisible().catch(() => false);
    const text = await ceoBtn.nth(i).textContent();
    console.log(`  ${i}: visible=${vis}, text=${JSON.stringify(text?.trim())}`);
  }

  // Try Code Env Sprawl (currently selected, has 1 recipient)
  const cesBtn = page.locator('button').filter({ hasText: 'Code Env Sprawl' });
  const cesCount = await cesBtn.count();
  console.log(`\n"Code Env Sprawl" button count: ${cesCount}`);
  for (let i = 0; i < cesCount; i++) {
    const vis = await cesBtn.nth(i).isVisible().catch(() => false);
    const text = await cesBtn.nth(i).textContent();
    console.log(`  ${i}: visible=${vis}, text=${JSON.stringify(text?.trim())}`);
  }

  // Now click Deprecated Python Versions and expand the first recipient
  if (depCount > 0) {
    const visibleDep = depBtn.first();
    await visibleDep.click();
    await page.waitForTimeout(1_000);
    await page.screenshot({ path: '/tmp/debug-deprecated-campaign.png', fullPage: true });

    // Check table
    const table = page.locator('table');
    const tableCount = await table.count();
    console.log(`\nTables on page: ${tableCount}`);

    // Get all rows in the visible table
    const rows = page.locator('table tbody tr');
    const rowCount = await rows.count();
    console.log(`Table rows: ${rowCount}`);
    for (let i = 0; i < Math.min(rowCount, 10); i++) {
      const text = await rows.nth(i).textContent();
      console.log(`  row ${i}: ${JSON.stringify(text?.trim().slice(0, 100))}`);
    }
  }
});
