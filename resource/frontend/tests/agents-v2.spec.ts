import { test, expect, type Page, type Route } from '@playwright/test';

/**
 * Agents v2 flow against a fully mocked backend:
 * sensor checklist → handoff to actuator → plan cards (with item_ref) →
 * batch approve → execution cards. No live DSS needed.
 */

const AGENTS = [
  { id: 'triage1', name: 'ATK Health Triage' },
  { id: 'scoping1', name: 'ATK Scoping Architect' },
  { id: 'actuator1', name: 'ATK Ops Actuator' },
];

function sse(event: string, payload: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`;
}

const ACTION_ITEMS_EVENT = sse('agent_event', {
  eventKind: 'action_items',
  eventData: {
    batchId: 'aib-cafe0001',
    count: 3,
    items: [
      {
        id: 'ai-00000001', title: 'ANALYZE story.events (stale stats)', why: 'Planner stats are 40 days old.',
        host: 'local', risk: 'green', action: 'db-analyze',
        target: { connection: 'runtimedb', table: 'story.events' },
        evidence: ['db_health tables: last analyze 40d ago (host=local)'], actionable: true, validation: null,
      },
      {
        id: 'ai-00000002', title: 'VACUUM story.audit (dead tuples)', why: '1.2M dead tuples.',
        host: 'local', risk: 'amber', action: 'db-vacuum',
        target: { connection: 'runtimedb', table: 'story.audit' },
        evidence: ['db_health tables: 1.2M dead tuples (host=local)'], actionable: true, validation: null,
      },
      {
        id: 'ai-00000003', title: 'Investigate recurring GC pauses', why: 'Log errors show GC overhead.',
        host: 'local', risk: 'red', action: null, target: null,
        evidence: ['log_errors: GC overhead group x42'], actionable: false,
        validation: "action 'jvm-tune' is not in the actuator catalog — downgraded to advisory",
      },
    ],
  },
});

function planEvent(n: 1 | 2): string {
  const items = {
    1: { action: 'db-analyze', table: 'story.events', item: 'ai-00000001', token: 'tok-analyze.sig1' },
    2: { action: 'db-vacuum', table: 'story.audit', item: 'ai-00000002', token: 'tok-vacuum.sig2' },
  }[n];
  return sse('agent_event', {
    eventKind: 'plan',
    eventData: {
      action: items.action,
      host: 'local',
      canonicalTarget: { connection: 'runtimedb', table: items.table },
      plan: { summary: `${items.action.toUpperCase()} table ${items.table} on connection runtimedb.` },
      confirm_token: items.token,
      expiresInSeconds: 900,
      itemRef: { batchId: 'aib-cafe0001', itemId: items.item },
    },
  });
}

function executionEvent(n: 1 | 2): string {
  const items = {
    1: { action: 'db-analyze', table: 'story.events', item: 'ai-00000001', audit: 101 },
    2: { action: 'db-vacuum', table: 'story.audit', item: 'ai-00000002', audit: 102 },
  }[n];
  return sse('agent_event', {
    eventKind: 'execution',
    eventData: {
      action: items.action,
      host: 'local',
      status: 'ok',
      auditId: items.audit,
      target: { connection: 'runtimedb', table: items.table },
      itemRef: { batchId: 'aib-cafe0001', itemId: items.item },
    },
  });
}

async function mockAgentsBackend(page: Page) {
  let chatCalls = 0;
  // Host picker gate: a healthy local host lets Enter reach the app shell.
  await page.route('**/api/hosts', (route: Route) =>
    route.fulfill({ json: [{ id: 'local', label: 'Local DSS', url: '' }] }),
  );
  await page.route('**/api/hosts/check', (route: Route) =>
    route.fulfill({ json: { ok: true, pluginInstalled: true, adminToolkitProjectExists: true } }),
  );
  await page.route('**/api/agents', (route: Route) =>
    route.fulfill({ json: { available: true, projectKey: 'AGENTOPS', agents: AGENTS } }),
  );
  await page.route('**/api/agents/actions**', (route: Route) =>
    route.fulfill({
      json: {
        available: true,
        actions: [
          { id: 101, ts: '2026-07-03T10:00:00', agent: 'ops-actuator', host: 'local',
            action: 'db-analyze', target: { connection: 'runtimedb', table: 'story.events' },
            params: { batchId: 'aib-cafe0001', itemId: 'ai-00000001' }, status: 'ok' },
        ],
      },
    }),
  );
  await page.route('**/api/agents/chat', async (route: Route) => {
    chatCalls += 1;
    const body = JSON.parse(route.request().postData() || '{}') as {
      agentId: string;
      messages: { content: string }[];
    };
    const lastMessage = body.messages[body.messages.length - 1]?.content || '';
    let frames: string;
    if (body.agentId !== 'actuator1') {
      // Sensor turn → text + checklist.
      frames =
        sse('chunk', { text: 'Sweep done — two DB maintenance items and one advisory.' }) +
        ACTION_ITEMS_EVENT +
        sse('done', { finishReason: 'stop', durationMs: 1200 });
    } else if (lastMessage.startsWith('Action-item batch handoff')) {
      // Handoff → two plans carrying item_ref.
      frames =
        sse('chunk', { text: 'Planning both items now.' }) +
        planEvent(1) +
        planEvent(2) +
        sse('chunk', { text: 'Both plans are ready — awaiting your approval.' }) +
        sse('done', { finishReason: 'stop', durationMs: 900 });
    } else if (lastMessage.startsWith('Approved')) {
      // Batch approval → two executions.
      frames =
        executionEvent(1) +
        executionEvent(2) +
        sse('chunk', { text: 'Both actions executed. Audit rows #101 and #102.' }) +
        sse('done', { finishReason: 'stop', durationMs: 800 });
    } else {
      frames = sse('chunk', { text: `Unexpected turn ${chatCalls}.` }) + sse('done', {});
    }
    await route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
      body: frames,
    });
  });
}

test.describe('Agents v2 (mocked backend)', () => {
  test.setTimeout(90_000);

  test('checklist → handoff → plan cards → batch approve → executions', async ({ page }) => {
    await mockAgentsBackend(page);
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    // Preview builds land on the host picker — enter via the Local DSS card.
    await page.getByText('Pick a host to scan').waitFor({ timeout: 30_000 });
    const hostCard = page.getByRole('button', { name: /Local DSS/ });
    await expect(hostCard).toContainText('Ready', { timeout: 15_000 });
    await hostCard.click();
    await page.waitForSelector('aside', { timeout: 60_000 });

    // AGENTS nav sits right after OVERVIEW.
    await page.locator('aside button').filter({ hasText: /^Agents$/ }).first().click();

    // Agent picker present; switch to Health Triage.
    const triageBtn = page.getByRole('button', { name: 'Health Triage', exact: true });
    await expect(triageBtn).toBeVisible({ timeout: 15_000 });
    await triageBtn.click();

    // Prompt library opens from the composer.
    await page.getByRole('button', { name: /Prompts/ }).click();
    await expect(page.getByText('Prompt library')).toBeVisible();
    await expect(page.getByText('Full fleet audit').first()).toBeVisible();
    await page.keyboard.press('Escape');

    // Send a message → checklist card arrives.
    const composer = page.getByPlaceholder(/Message the agent/);
    await composer.fill('Sweep the fleet.');
    await composer.press('Enter');

    await expect(page.getByText('Action items')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('ANALYZE story.events (stale stats)')).toBeVisible();
    await expect(page.getByText('advisory', { exact: true })).toBeVisible();

    // Advisory item's checkbox is disabled; select the two actionable ones.
    const checkboxes = page.locator('input[type=checkbox]');
    await expect(checkboxes).toHaveCount(3);
    await expect(checkboxes.nth(2)).toBeDisabled();
    await checkboxes.nth(0).check();
    await checkboxes.nth(1).check();

    // Handoff: switches to the actuator and produces two plan cards.
    await page.getByRole('button', { name: /Send 2 to Ops Actuator/ }).click();
    await expect(page.getByText('Action-item batch handoff')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('db-analyze', { exact: true }).first()).toBeVisible();
    await expect(page.locator('text=ai-00000001').first()).toBeVisible();

    // Batch approvals bar appears for ≥2 pending plans.
    await expect(page.getByText('2 plans', { exact: false }).first()).toBeVisible();
    await page.getByRole('button', { name: /Approve all \(2\)/ }).click();

    // Confirm dialog lists both plans, then confirms.
    await expect(page.getByText('Approve 2 plans?')).toBeVisible();
    await page.getByRole('button', { name: /Approve all 2/ }).click();

    // Executions arrive with audit ids.
    await expect(page.getByRole('button', { name: 'audit #101' })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole('button', { name: 'audit #102' })).toBeVisible();

    // Audit deep-link: clicking audit #101 opens the trail with a DSS link.
    await page.getByRole('button', { name: 'audit #101' }).click();
    await expect(page.getByText('Action audit trail')).toBeVisible();
    const auditLink = page.locator('a').filter({ hasText: 'db-analyze' }).first();
    await expect(auditLink).toBeVisible();
    await expect(auditLink).toHaveAttribute('href', /\/admin\/connections\/runtimedb\/$/);
  });

  test('chat + action items survive a hard refresh', async ({ page }) => {
    await mockAgentsBackend(page);
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    await page.getByText('Pick a host to scan').waitFor({ timeout: 30_000 });
    const hostCard = page.getByRole('button', { name: /Local DSS/ });
    await expect(hostCard).toContainText('Ready', { timeout: 15_000 });
    await hostCard.click();
    await page.waitForSelector('aside', { timeout: 60_000 });
    await page.locator('aside button').filter({ hasText: /^Agents$/ }).first().click();

    const triageBtn = page.getByRole('button', { name: 'Health Triage', exact: true });
    await expect(triageBtn).toBeVisible({ timeout: 15_000 });
    await triageBtn.click();
    const composer = page.getByPlaceholder(/Message the agent/);
    await composer.fill('Sweep the fleet.');
    await composer.press('Enter');
    await expect(page.getByText('Action items')).toBeVisible({ timeout: 15_000 });

    // Hard refresh: conversation + checklist must rehydrate from localStorage.
    await page.reload({ waitUntil: 'domcontentloaded' });
    const gate = page.getByText('Pick a host to scan');
    await expect(page.locator('aside').or(gate).first()).toBeVisible({ timeout: 30_000 });
    if (await gate.isVisible().catch(() => false)) {
      const card = page.getByRole('button', { name: /Local DSS/ });
      await expect(card).toContainText('Ready', { timeout: 15_000 });
      await card.click();
    }
    await page.waitForSelector('aside', { timeout: 60_000 });
    await page.locator('aside button').filter({ hasText: /^Agents$/ }).first().click();

    await expect(page.getByText('Sweep the fleet.')).toBeVisible({ timeout: 15_000 });
    await expect(
      page.getByText('Sweep done — two DB maintenance items and one advisory.'),
    ).toBeVisible();
    await expect(page.getByText('ANALYZE story.events (stale stats)')).toBeVisible();
    // No stuck stream after rehydration: the composer accepts input again.
    await expect(page.getByPlaceholder(/Message the agent/)).toBeEnabled();
  });
});
