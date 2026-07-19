import { test, expect, type Page, type Route } from '@playwright/test';

/**
 * Agents v2 flow against a fully mocked backend:
 * sensor checklist → handoff to actuator → plan cards (with item_ref) →
 * batch approve → execution cards. No live DSS needed.
 */

const AGENTS = [{ id: 'agent1', name: 'ATK Admin Agent' }];

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
  // Host picker gate: a healthy local host lets Enter reach the app shell.
  await page.route('**/api/hosts', (route: Route) =>
    route.fulfill({ json: [{ id: 'local', label: 'Local DSS', url: '' }] }),
  );
  await page.route('**/api/hosts/check', (route: Route) =>
    route.fulfill({ json: { ok: true, pluginInstalled: true, adminToolkitProjectExists: true } }),
  );
  // Chat persistence OFF by default (the plugin default) — existing flows must
  // behave exactly as before; the persistence suite overrides these routes.
  await page.route('**/api/chat/config', (route: Route) =>
    route.fulfill({ json: { enabled: false } }),
  );
  await page.route('**/api/agents/trace-explorer/status', (route: Route) =>
    route.fulfill({
      json: { installed: false, provisioned: false, projectKey: 'ADMINTOOLKIT', sameOrigin: true },
    }),
  );
  await page.route('**/api/agents', (route: Route) =>
    route.fulfill({ json: { available: true, projectKey: 'ADMINTOOLKIT', agents: AGENTS } }),
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
    const body = JSON.parse(route.request().postData() || '{}') as {
      agentId: string;
      messages: { content: string }[];
    };
    const lastMessage = body.messages[body.messages.length - 1]?.content || '';
    let frames: string;
    // Single agent since 4c — the turn's INTENT (sweep / handoff / approval)
    // is read from the message, not from which agent id it went to.
    if (lastMessage.startsWith('Action-item batch handoff')) {
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
      // Sensor turn → text + checklist.
      frames =
        sse('chunk', { text: 'Sweep done — two DB maintenance items and one advisory.' }) +
        ACTION_ITEMS_EVENT +
        sse('done', { finishReason: 'stop', durationMs: 1200 });
    }
    await route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
      body: frames,
    });
  });
}

interface StoredTurnMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  display?: string;
  segments?: unknown[];
  position: number;
}

/** In-memory chat-persistence server: config enabled + conversation CRUD +
 * turn upserts, mirroring routes/chat.py just enough for the UI flows. */
function mockChatPersistence(page: Page) {
  const conversations = new Map<
    string,
    { id: string; agentId: string; title: string; messages: Map<string, StoredTurnMessage> }
  >();
  const turnBodies: unknown[] = [];

  const register = async () => {
    await page.route('**/api/chat/config', (route: Route) =>
      route.fulfill({ json: { enabled: true, mode: 'LOCAL' } }),
    );
    await page.route('**/api/chat/conversations', (route: Route) =>
      route.fulfill({
        json: {
          enabled: true,
          conversations: [...conversations.values()].map((c) => ({
            id: c.id,
            agentId: c.agentId,
            title: c.title,
            lastModified: new Date().toISOString(),
          })),
        },
      }),
    );
    await page.route('**/api/chat/conversations/*', (route: Route) => {
      const id = decodeURIComponent(route.request().url().split('/').pop() || '');
      const conv = conversations.get(id);
      if (route.request().method() === 'DELETE') {
        conversations.delete(id);
        return route.fulfill({ json: { enabled: true, ok: true } });
      }
      if (route.request().method() === 'PUT') {
        const body = JSON.parse(route.request().postData() || '{}') as { title?: string };
        if (conv && body.title) conv.title = body.title;
        return route.fulfill({ json: { enabled: true, ok: true } });
      }
      if (!conv) return route.fulfill({ status: 404, json: { error: 'conversation-not-found' } });
      return route.fulfill({
        json: {
          enabled: true,
          conversation: {
            id: conv.id,
            agentId: conv.agentId,
            title: conv.title,
            messages: [...conv.messages.values()].sort((a, b) => a.position - b.position),
          },
        },
      });
    });
    await page.route('**/api/chat/conversations/*/turn', (route: Route) => {
      const parts = route.request().url().split('/');
      const id = decodeURIComponent(parts[parts.length - 2] || '');
      const body = JSON.parse(route.request().postData() || '{}') as {
        agentId: string;
        title?: string;
        messages: StoredTurnMessage[];
      };
      turnBodies.push(body);
      let conv = conversations.get(id);
      if (!conv) {
        conv = { id, agentId: body.agentId, title: body.title || '', messages: new Map() };
        conversations.set(id, conv);
      }
      if (body.title && !conv.title) conv.title = body.title;
      for (const msg of body.messages || []) conv.messages.set(msg.id, msg);
      return route.fulfill({
        json: { enabled: true, conversation: { id, title: conv.title } },
      });
    });
  };
  return { register, conversations, turnBodies };
}

async function enterAgentsPage(page: Page) {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.getByText('Pick a host to scan').waitFor({ timeout: 30_000 });
  const hostCard = page.getByRole('button', { name: /Local DSS/ });
  await expect(hostCard).toContainText('Ready', { timeout: 15_000 });
  await hostCard.click();
  await page.waitForSelector('aside', { timeout: 60_000 });
  await page.locator('aside button').filter({ hasText: /^Agents$/ }).first().click();
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

    // Single-agent presentation: one identity, no specialist picker.
    await expect(page.getByText('Admin Toolkit Agent')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole('button', { name: 'Health Triage', exact: true })).toHaveCount(0);

    // Prompt library opens from the composer.
    await page.getByRole('button', { name: /Prompts/ }).click();
    await expect(page.getByText('Prompt library')).toBeVisible();
    // Both headline groups render with their megaprompts (the titles also
    // appear on the empty-state hero cards behind the drawer → .first()).
    await expect(page.getByText('Health & Triage').first()).toBeVisible();
    await expect(page.getByText('Scoping & Architecture').first()).toBeVisible();
    await expect(page.getByText('Full fleet audit').first()).toBeVisible();
    await expect(page.getByText('Full scoping dossier').first()).toBeVisible();
    await page.keyboard.press('Escape');

    // Free-form message routes to the triage generalist → checklist card.
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

    // Handoff: internally routes to the actuator specialist and produces two
    // plan cards. The bubble shows the display variant (machine refs hidden
    // since 0.4.647; specialist names never surface in the single-agent UI).
    await page.getByRole('button', { name: /Plan 2 selected actions/ }).click();
    await expect(page.getByText(/Plan the 2 action item/)).toBeVisible({ timeout: 15_000 });
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

    await expect(page.getByText('Admin Toolkit Agent')).toBeVisible({ timeout: 15_000 });
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

test.describe('Agents chat persistence (mocked backend)', () => {
  test.setTimeout(90_000);

  test('settled turn is POSTed; history drawer restores it server-side', async ({ page }) => {
    await mockAgentsBackend(page);
    const server = mockChatPersistence(page);
    await server.register(); // registered after → overrides the enabled:false default
    await enterAgentsPage(page);

    // Persistence enabled → the History affordance appears.
    await expect(page.getByRole('button', { name: 'History', exact: true })).toBeVisible({
      timeout: 15_000,
    });

    // Send a message; on settle the turn lands on the server store.
    await expect(page.getByText('Admin Toolkit Agent')).toBeVisible({ timeout: 15_000 });
    const composer = page.getByPlaceholder(/Message the agent/);
    await composer.fill('Sweep the fleet.');
    const turnRequest = page.waitForRequest(
      (req) => req.url().includes('/api/chat/conversations/') && req.url().endsWith('/turn'),
      { timeout: 15_000 },
    );
    await composer.press('Enter');
    await expect(page.getByText('Action items')).toBeVisible({ timeout: 15_000 });
    await turnRequest;

    const turn = server.turnBodies[server.turnBodies.length - 1] as {
      agentId: string;
      title: string;
      messages: StoredTurnMessage[];
    };
    expect(turn.agentId).toBe('agent1');
    expect(turn.title).toBe('Sweep the fleet.');
    expect(turn.messages.map((m) => m.role)).toEqual(['user', 'assistant']);
    expect(turn.messages[1].segments?.length).toBeGreaterThan(0);

    // Wipe the browser cache entirely — restore must come from the server.
    await page.evaluate(() => localStorage.clear());
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

    // Transcript starts empty (localStorage gone), history has the conversation.
    await expect(page.getByText('Sweep the fleet.')).toHaveCount(0);
    await page.getByRole('button', { name: 'History', exact: true }).click();
    await expect(page.getByText('Chat history')).toBeVisible();
    await page.getByRole('button', { name: /Sweep the fleet\./ }).click();

    // Reopened from the server: user bubble + assistant reply + checklist card.
    await expect(page.getByText('Sweep the fleet.')).toBeVisible({ timeout: 15_000 });
    await expect(
      page.getByText('Sweep done — two DB maintenance items and one advisory.'),
    ).toBeVisible();
    await expect(page.getByText('ANALYZE story.events (stale stats)')).toBeVisible();
    await expect(page.getByPlaceholder(/Message the agent/)).toBeEnabled();
  });

  test('delete removes the conversation from the drawer', async ({ page }) => {
    await mockAgentsBackend(page);
    const server = mockChatPersistence(page);
    await server.register();
    await enterAgentsPage(page);

    await expect(page.getByText('Admin Toolkit Agent')).toBeVisible({ timeout: 15_000 });
    const composer = page.getByPlaceholder(/Message the agent/);
    await composer.fill('Quick check please.');
    await composer.press('Enter');
    await expect(page.getByText('Action items')).toBeVisible({ timeout: 15_000 });

    await page.getByRole('button', { name: 'History', exact: true }).click();
    const row = page.getByRole('button', { name: /Quick check please\./ });
    await expect(row).toBeVisible();
    await row.hover();
    await page.getByRole('button', { name: 'Delete conversation' }).click();
    await expect(row).toHaveCount(0);
    expect(server.conversations.size).toBe(0);
  });
});

test.describe('Agent Tuning (mocked backend)', () => {
  test.setTimeout(90_000);

  test('edit a prompt → save appends a version, restore loads it back', async ({ page }) => {
    await mockAgentsBackend(page);
    const TUNING_STATE = {
      available: true,
      datasetName: 'agent_prompt_versions',
      project: 'TOOLKIT',
      connection: 'filesystem_managed',
      promptTypes: [
        {
          key: 'triage_system_prompt', label: 'Health Triage — system prompt',
          description: 'Persona of the triage specialist.',
          placeholders: ['{severity_rubric}'],
          default: 'DEFAULT TRIAGE PROMPT {severity_rubric}', override: null,
        },
        {
          key: 'severity_rubric', label: 'Severity rubric',
          description: 'Shared severity calibration.', placeholders: [],
          default: 'DEFAULT RUBRIC', override: null,
        },
      ],
      versions: [] as unknown[],
    };
    const saves: { note: string; values: Record<string, string> }[] = [];
    await page.route('**/api/agents/tuning', (route: Route) =>
      route.fulfill({ json: TUNING_STATE }),
    );
    await page.route('**/api/agents/tuning/save', (route: Route) => {
      const body = JSON.parse(route.request().postData() || '{}') as {
        note: string;
        values: Record<string, string>;
      };
      saves.push(body);
      route.fulfill({
        json: {
          ...TUNING_STATE,
          promptTypes: TUNING_STATE.promptTypes.map((pt) =>
            pt.key === 'severity_rubric' ? { ...pt, override: body.values.severity_rubric } : pt,
          ),
          versions: [
            {
              savedAt: '2026-07-06T10:00:00Z', author: 'alex', note: body.note,
              customized: ['severity_rubric'],
              values: { triage_system_prompt: '', severity_rubric: body.values.severity_rubric },
            },
          ],
        },
      });
    });
    await enterAgentsPage(page);
    await page.locator('aside button').filter({ hasText: /^Tuning$/ }).first().click();

    await expect(page.getByText('Version history')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/no saved versions/)).toBeVisible();

    // Expand the rubric card and customize it.
    await page.getByRole('button', { name: /Severity rubric/ }).click();
    const editor = page.locator('textarea');
    await expect(editor).toHaveValue('DEFAULT RUBRIC');
    await editor.fill('CUSTOM RUBRIC v2');
    await expect(page.getByText(/Unsaved changes: 1 prompt/)).toBeVisible();

    // Save with a note → new active version appears.
    await page.getByPlaceholder(/version note/).fill('tighter rubric');
    await page.getByRole('button', { name: 'Save new version' }).click();
    await expect(page.getByText(/Version saved/)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('active', { exact: true })).toBeVisible();
    await expect(page.getByText(/customized: severity_rubric/)).toBeVisible();
    expect(saves[0].note).toBe('tighter rubric');
    expect(saves[0].values.severity_rubric).toBe('CUSTOM RUBRIC v2');
    // Cells equal to the default are posted verbatim; the backend stores them
    // as empty ("default") — here the triage prompt was untouched.
    expect(saves[0].values.triage_system_prompt).toBe('DEFAULT TRIAGE PROMPT {severity_rubric}');

    // Load restores a version's values into the editors, replacing edits.
    await editor.fill('SCRATCH EDIT');
    await expect(page.getByText(/Unsaved changes: 1 prompt/)).toBeVisible();
    await page.getByRole('button', { name: 'Load', exact: true }).click();
    await expect(editor).toHaveValue('CUSTOM RUBRIC v2');
  });

  test('python-run plan card requires the code ack before Approve arms', async ({ page }) => {
    await mockAgentsBackend(page);
    await page.route('**/api/agents/chat', async (route: Route) => {
      const frames =
        sse('chunk', { text: 'Power-Up plan ready.' }) +
        sse('agent_event', {
          eventKind: 'plan',
          eventData: {
            action: 'python-run',
            host: 'local',
            canonicalTarget: { codeSha256: 'a'.repeat(64), purpose: 'List 5 largest datasets' },
            plan: {
              summary: 'POWER-UP: run an agent-authored Python script.',
              purpose: 'List 5 largest datasets',
              code: "import dataiku\nprint('largest datasets…')\n",
              warnings: ['This script runs with ADMIN credentials.'],
            },
            confirm_token: 'tok-python.sig9',
            expiresInSeconds: 900,
          },
        }) +
        sse('done', { finishReason: 'stop', durationMs: 500 });
      await route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: frames,
      });
    });
    await enterAgentsPage(page);
    const composer = page.getByPlaceholder(/Message the agent/);
    await composer.fill('List the 5 largest datasets with owners.');
    await composer.press('Enter');

    // The card shows the exact code and a red ack row; Approve starts disabled.
    await expect(page.getByText('Power-Up script — runs with admin credentials')).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("print('largest datasets…')")).toBeVisible();
    const approve = page.getByRole('button', { name: /Approve & execute/ });
    await expect(approve).toBeDisabled();
    // Ticking "I have read this code" arms it.
    await page.getByText(/I have read this code/).click();
    await expect(approve).toBeEnabled();
    // And a lone python-run plan never raises the batch approvals bar.
    await expect(page.getByText(/awaiting your decision/)).toHaveCount(0);
  });

  test('gate refusal card deep-links to Agent Permissions', async ({ page }) => {
    await mockAgentsBackend(page);
    // The deep-link target page loads its catalog on mount.
    await page.route('**/api/agents/action-settings', (route: Route) =>
      route.fulfill({ json: { ok: true, sensors: [], actions: [], gates: {} } }),
    );
    // Override the chat mock (last-registered route wins): this turn hits the
    // action-disabled gate and the tool_result error carries the deep link.
    await page.route('**/api/agents/chat', async (route: Route) => {
      const frames =
        sse('chunk', { text: 'Trying to plan the cleanup.' }) +
        sse('agent_event', {
          eventKind: 'tool_call',
          eventData: { name: 'plan_admin_action', args: { action: 'log-cleanup' } },
        }) +
        sse('agent_event', {
          eventKind: 'tool_result',
          eventData: {
            name: 'plan_admin_action',
            durationMs: 40,
            ok: false,
            error: {
              code: 'action-disabled',
              message:
                "Action 'log-cleanup' is disabled in Agent Settings — every non-read action " +
                'is off until an administrator enables it.',
              link: { page: 'agent-settings', label: 'Enable in Agents → Permissions' },
            },
          },
        }) +
        sse('chunk', { text: 'That action is disabled; an admin can enable it.' }) +
        sse('done', { finishReason: 'stop', durationMs: 300 });
      await route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: frames,
      });
    });
    await enterAgentsPage(page);
    const composer = page.getByPlaceholder(/Message the agent/);
    await composer.fill('Clean up rotated logs.');
    await composer.press('Enter');

    // The refusal renders as a card with the backend's deep link…
    await expect(page.getByText('Action disabled in Agent Permissions')).toBeVisible({
      timeout: 15_000,
    });
    const linkBtn = page.getByRole('button', { name: /Enable in Agents → Permissions/ });
    await expect(linkBtn).toBeVisible();
    // …and clicking it navigates to the Agent Permissions page in-app.
    await linkBtn.click();
    await expect(page.getByRole('heading', { name: 'Agent Permissions' })).toBeVisible({
      timeout: 15_000,
    });
  });
});
