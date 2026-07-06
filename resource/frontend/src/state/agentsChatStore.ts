// Agents chat store — module-scoped singleton so a running agent turn
// survives page navigation. One conversation per agent id, on the active
// host. Streaming consumes the /api/agents/chat SSE proxy: token deltas plus
// the typed agent event protocol (tool_call / tool_result / plan / execution).
import { fetchRaw } from '../utils/api';
import { parseSseStream } from '../utils/sseStream';
import { createSyncStore } from './createSyncStore';
import { subscribeSessionEpoch } from './sessionCache';

export interface AgentInfo {
  id: string;
  name: string;
}

export interface ActivityItem {
  name: string;
  args?: unknown;
  running: boolean;
  durationMs?: number;
  ok?: boolean;
  error?: string;
}

/** Provenance ref linking a plan/execution back to a proposed action item. */
export interface ItemRef {
  batchId?: string;
  itemId?: string;
}

export interface PlanCardData {
  action: string;
  host: string;
  canonicalTarget: unknown;
  plan: Record<string, unknown>;
  confirmToken: string;
  expiresAt: number; // epoch ms
  decision?: 'approved' | 'rejected';
  itemRef?: ItemRef;
}

export interface ExecutionCardData {
  action: string;
  host: string;
  status: string;
  auditId?: number | null;
  auditWarning?: string;
  result?: unknown;
  target?: unknown;
  itemRef?: ItemRef;
}

export type ActionItemRisk = 'red' | 'amber' | 'green';

export interface ActionItemData {
  id: string;
  title: string;
  why: string;
  host: string;
  risk: ActionItemRisk;
  action: string | null;
  target: Record<string, unknown> | null;
  evidence: string[];
  actionable: boolean;
  validation: string | null;
}

export interface ActionItemsCardData {
  batchId: string;
  items: ActionItemData[];
  /** Item ids already handed to the actuator (checkboxes lock afterwards). */
  submittedIds: string[];
  droppedCount?: number;
}

export type Segment =
  | { type: 'text'; text: string }
  | { type: 'activity'; items: ActivityItem[] }
  | { type: 'plan'; plan: PlanCardData }
  | { type: 'execution'; execution: ExecutionCardData }
  | { type: 'action_items'; batch: ActionItemsCardData };

export interface ChatMessage {
  role: 'user' | 'assistant';
  // Plain text sent back as history (assistant = concatenated text segments).
  content: string;
  /** Human-facing text when it differs from the model-facing `content` — used
   * for synthetic approval/handoff messages whose content must carry
   * confirm_token/item_ref for the agent but shouldn't show them in the UI. */
  display?: string;
  segments: Segment[];
  /** Native dku-trace id for this turn — fetchable from /api/agents/last-trace
   * while it's still in the backend's short ring buffer. */
  traceId?: string;
}

export interface Conversation {
  agentId: string;
  messages: ChatMessage[];
  streaming: boolean;
  error: string | null;
  lastDurationMs?: number;
  /** DSS-relative path of the Trace Explorer webapp on this host, if one exists. */
  traceExplorerPath?: string;
}

interface AgentsChatState {
  conversations: Record<string, Conversation>;
  /** Store-owned so the action-item handoff can switch the visible agent. */
  selectedAgentId: string;
}

export const agentsChatStore = createSyncStore<AgentsChatState>(
  { conversations: {}, selectedAgentId: '' },
  { sessionScoped: true },
);

// Chats survive a hard refresh via localStorage; the in-app Refresh (session
// epoch) keeps its clear-everything semantics, so the epoch bump also drops
// the snapshot.
const STORAGE_KEY = 'admin-toolkit:agentsChat';
const STORAGE_VERSION = 1;

/** A hard refresh kills any in-flight stream: stop spinners, drop a dangling
 * empty assistant turn, and never rehydrate `streaming: true` (the abort
 * controllers don't survive a reload). */
function sanitizeStored(state: AgentsChatState): AgentsChatState {
  const conversations: Record<string, Conversation> = {};
  for (const [id, conv] of Object.entries(state.conversations || {})) {
    let messages = conv.messages || [];
    const last = messages[messages.length - 1];
    if (conv.streaming && last?.role === 'assistant' && !last.segments?.length) {
      messages = messages.slice(0, -1);
    }
    conversations[id] = {
      ...conv,
      messages: messages.map((m) => ({
        ...m,
        segments: (m.segments || []).map((s) =>
          s.type === 'activity'
            ? { ...s, items: s.items.map((it) => ({ ...it, running: false })) }
            : s,
        ),
      })),
      streaming: false,
    };
  }
  return { conversations, selectedAgentId: state.selectedAgentId || '' };
}

function readStored(): AgentsChatState | null {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { v?: number; state?: AgentsChatState };
    if (parsed?.v !== STORAGE_VERSION || typeof parsed.state?.conversations !== 'object') return null;
    return sanitizeStored(parsed.state);
  } catch {
    return null;
  }
}

function persistStored(state: AgentsChatState): void {
  // Skip mid-stream frames (per-token writes would jank); the final state
  // lands when the stream flips `streaming` back to false.
  if (Object.values(state.conversations).some((c) => c.streaming)) return;
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify({ v: STORAGE_VERSION, state }));
  } catch {
    // best effort — quota exceeded or storage unavailable
  }
}

const storedState = readStored();
if (storedState) agentsChatStore.set(storedState);
agentsChatStore.subscribe(() => persistStored(agentsChatStore.get()));
subscribeSessionEpoch(() => {
  try {
    globalThis.localStorage?.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
});

export function selectAgent(agentId: string): void {
  agentsChatStore.patch({ selectedAgentId: agentId });
}

const abortControllers = new Map<string, AbortController>();

function getConversation(agentId: string): Conversation {
  return (
    agentsChatStore.get().conversations[agentId] || {
      agentId,
      messages: [],
      streaming: false,
      error: null,
    }
  );
}

function putConversation(conv: Conversation): void {
  agentsChatStore.patch({
    conversations: { ...agentsChatStore.get().conversations, [conv.agentId]: conv },
  });
}

/** Immutable update of the last (assistant) message's segments. */
function updateAssistant(
  conv: Conversation,
  update: (segments: Segment[]) => Segment[],
): Conversation {
  const messages = conv.messages.slice();
  const last = messages[messages.length - 1];
  if (!last || last.role !== 'assistant') return conv;
  const segments = update(last.segments.slice());
  const content = segments
    .filter((s): s is Extract<Segment, { type: 'text' }> => s.type === 'text')
    .map((s) => s.text)
    .join('');
  messages[messages.length - 1] = { ...last, segments, content };
  return { ...conv, messages };
}

function appendText(segments: Segment[], text: string): Segment[] {
  const last = segments[segments.length - 1];
  if (last && last.type === 'text') {
    segments[segments.length - 1] = { type: 'text', text: last.text + text };
  } else {
    segments.push({ type: 'text', text });
  }
  return segments;
}

function applyAgentEvent(segments: Segment[], kind: string, data: Record<string, unknown>): Segment[] {
  if (kind === 'tool_call') {
    const item: ActivityItem = { name: String(data.name || '?'), args: data.args, running: true };
    const last = segments[segments.length - 1];
    if (last && last.type === 'activity') {
      segments[segments.length - 1] = { type: 'activity', items: [...last.items, item] };
    } else {
      segments.push({ type: 'activity', items: [item] });
    }
  } else if (kind === 'tool_result') {
    for (let i = segments.length - 1; i >= 0; i--) {
      const seg = segments[i];
      if (seg.type !== 'activity') continue;
      const idx = seg.items.findIndex((it) => it.running && it.name === data.name);
      if (idx === -1) continue;
      const items = seg.items.slice();
      const err = data.error as { message?: string } | null | undefined;
      items[idx] = {
        ...items[idx],
        running: false,
        durationMs: Number(data.durationMs) || 0,
        ok: Boolean(data.ok),
        error: err ? String(err.message || 'error') : undefined,
      };
      segments[i] = { type: 'activity', items };
      break;
    }
  } else if (kind === 'plan') {
    const expiresIn = Number(data.expiresInSeconds) || 900;
    segments.push({
      type: 'plan',
      plan: {
        action: String(data.action || ''),
        host: String(data.host || 'local'),
        canonicalTarget: data.canonicalTarget,
        plan: (data.plan as Record<string, unknown>) || {},
        confirmToken: String(data.confirm_token || ''),
        expiresAt: Date.now() + expiresIn * 1000,
        itemRef: normalizeItemRef(data.itemRef),
      },
    });
  } else if (kind === 'execution') {
    segments.push({
      type: 'execution',
      execution: {
        action: String(data.action || ''),
        host: String(data.host || 'local'),
        status: String(data.status || 'unknown'),
        auditId: data.auditId as number | null,
        auditWarning: data.auditWarning ? String(data.auditWarning) : undefined,
        result: data.result,
        target: data.target,
        itemRef: normalizeItemRef(data.itemRef),
      },
    });
  } else if (kind === 'action_items') {
    const items = normalizeActionItems(data.items);
    if (items.length > 0) {
      segments.push({
        type: 'action_items',
        batch: {
          batchId: String(data.batchId || `aib-${Date.now().toString(16)}`),
          items,
          submittedIds: [],
          droppedCount: Number(data.droppedCount) || undefined,
        },
      });
    }
  }
  return segments;
}

function normalizeItemRef(raw: unknown): ItemRef | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const ref = raw as Record<string, unknown>;
  if (!ref.batchId && !ref.itemId) return undefined;
  return {
    batchId: ref.batchId ? String(ref.batchId) : undefined,
    itemId: ref.itemId ? String(ref.itemId) : undefined,
  };
}

/** Defensive normalization — the event payload is model-adjacent data. */
function normalizeActionItems(raw: unknown): ActionItemData[] {
  if (!Array.isArray(raw)) return [];
  const out: ActionItemData[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object') continue;
    const item = entry as Record<string, unknown>;
    const title = String(item.title || '').trim();
    if (!title) continue;
    const risk = String(item.risk || 'amber');
    const action = item.action ? String(item.action) : null;
    out.push({
      id: String(item.id || `ai-${out.length}-${Date.now().toString(16)}`),
      title,
      why: String(item.why || ''),
      host: String(item.host || 'local'),
      risk: risk === 'red' || risk === 'green' ? risk : 'amber',
      action,
      target:
        item.target && typeof item.target === 'object' && !Array.isArray(item.target)
          ? (item.target as Record<string, unknown>)
          : null,
      evidence: Array.isArray(item.evidence) ? item.evidence.map(String).slice(0, 6) : [],
      actionable: Boolean(item.actionable) && action !== null,
      validation: item.validation ? String(item.validation) : null,
    });
  }
  return out;
}

/** Mark a plan card approved/rejected (by confirm token) across the conversation. */
export function decidePlan(
  agentId: string,
  confirmToken: string,
  decision: 'approved' | 'rejected',
): void {
  const conv = getConversation(agentId);
  const messages = conv.messages.map((msg) => ({
    ...msg,
    segments: msg.segments.map((seg) =>
      seg.type === 'plan' && seg.plan.confirmToken === confirmToken
        ? { ...seg, plan: { ...seg.plan, decision } }
        : seg,
    ),
  }));
  putConversation({ ...conv, messages });
}

/** One line of the batch handoff message the actuator receives per item.
 * `forModel` includes the machine refs ([id], item_ref) the actuator must echo
 * into plan_admin_action; the display variant omits them. */
function handoffLine(
  batchId: string,
  item: ActionItemData,
  index: number,
  forModel: boolean,
): string {
  const ref = forModel
    ? ` [${item.id}]`
    : '';
  const itemRef = forModel ? ` item_ref=${JSON.stringify({ batchId, itemId: item.id })}` : '';
  const head = `${index + 1}.${ref} ${item.title} — action=${item.action} host=${item.host} target=${JSON.stringify(item.target)}${itemRef}`;
  const why = item.why ? `\n   why: ${item.why}` : '';
  const evidence = item.evidence.length > 0 ? `\n   evidence: ${item.evidence.join(' | ')}` : '';
  return head + why + evidence;
}

/**
 * Hand checked action items to the ops-actuator: marks them submitted on the
 * source conversation, switches the visible agent, and sends ONE synthetic
 * message that asks the actuator to plan each item (fresh tokens + fresh blast
 * radius — approval still happens per plan, in the actuator conversation).
 */
export function submitActionItemsToActuator(
  sourceAgentId: string,
  actuatorAgentId: string,
  batchId: string,
  items: ActionItemData[],
): void {
  const actionable = items.filter((item) => item.actionable && item.action);
  if (actionable.length === 0) return;
  // The actuator can only take the batch when idle — otherwise the items
  // would be marked submitted while the message silently dropped.
  if (getConversation(actuatorAgentId).streaming) return;

  const source = getConversation(sourceAgentId);
  const submitted = new Set(actionable.map((item) => item.id));
  putConversation({
    ...source,
    messages: source.messages.map((msg) => ({
      ...msg,
      segments: msg.segments.map((seg) =>
        seg.type === 'action_items' && seg.batch.batchId === batchId
          ? {
              ...seg,
              batch: {
                ...seg.batch,
                submittedIds: [...new Set([...seg.batch.submittedIds, ...submitted])],
              },
            }
          : seg,
      ),
    })),
  });

  const text =
    `Action-item batch handoff (batch ${batchId}, ${actionable.length} item(s) selected by the user from another agent's findings).\n` +
    `Plan EVERY item below — one plan_admin_action call per item, passing its item_ref verbatim. ` +
    `Present each plan and WAIT for my approval. Do NOT execute anything yet.\n\n` +
    actionable.map((item, i) => handoffLine(batchId, item, i, true)).join('\n');
  const display =
    `Action-item handoff — ${actionable.length} item(s) selected from another agent's findings. ` +
    `Plan each item and wait for my approval before executing.\n\n` +
    actionable.map((item, i) => handoffLine(batchId, item, i, false)).join('\n');

  selectAgent(actuatorAgentId);
  void sendAgentMessage(actuatorAgentId, text, display);
}

/**
 * Approve one or more pending plans in a single message: each plan keeps its
 * own token, the actuator executes each independently (one audit row each).
 */
export function approvePlans(agentId: string, plans: PlanCardData[]): void {
  if (plans.length === 0) return;
  for (const plan of plans) decidePlan(agentId, plan.confirmToken, 'approved');
  const text =
    plans.length === 1
      ? `Approved — I confirm. Execute the planned ${plans[0].action} on host ${plans[0].host} with the exact planned target, confirm=true and confirm_token ${plans[0].confirmToken}${plans[0].itemRef ? ` and item_ref ${JSON.stringify(plans[0].itemRef)}` : ''}. Report the outcome and the auditId.`
      : `Approved — I confirm ALL ${plans.length} plans below. Execute each independently with its exact planned target, confirm=true, its own confirm_token (and its item_ref where given); report each outcome and auditId separately:\n` +
        plans
          .map(
            (plan, i) =>
              `${i + 1}. ${plan.action} on host ${plan.host} — confirm_token ${plan.confirmToken}${plan.itemRef ? ` item_ref=${JSON.stringify(plan.itemRef)}` : ''}`,
          )
          .join('\n');
  const display =
    plans.length === 1
      ? `Approved — execute the planned ${plans[0].action} on host ${plans[0].host}.`
      : `Approved — execute all ${plans.length} plans:\n` +
        plans.map((plan, i) => `${i + 1}. ${plan.action} on host ${plan.host}`).join('\n');
  void sendAgentMessage(agentId, text, display);
}

/** Reject one or more pending plans in a single message. */
export function rejectPlans(agentId: string, plans: PlanCardData[]): void {
  if (plans.length === 0) return;
  for (const plan of plans) decidePlan(agentId, plan.confirmToken, 'rejected');
  const text =
    plans.length === 1
      ? `Rejected — do NOT execute the planned ${plans[0].action}. Stand down and await further instructions.`
      : `Rejected — do NOT execute ANY of the following ${plans.length} planned actions: ` +
        plans.map((plan) => `${plan.action} on ${plan.host}`).join('; ') +
        `. Stand down and await further instructions.`;
  void sendAgentMessage(agentId, text);
}

export function abortAgentTurn(agentId: string): void {
  abortControllers.get(agentId)?.abort();
}

export function clearConversation(agentId: string): void {
  abortAgentTurn(agentId);
  const conversations = { ...agentsChatStore.get().conversations };
  delete conversations[agentId];
  agentsChatStore.patch({ conversations });
}

/** Send one user message and stream the agent's reply into the store.
 * `display` overrides what the user bubble shows; `text` (which may carry
 * confirm tokens / item refs) is always what goes into the model history. */
export async function sendAgentMessage(
  agentId: string,
  text: string,
  display?: string,
): Promise<void> {
  const base = getConversation(agentId);
  if (base.streaming) return;

  abortControllers.get(agentId)?.abort();
  const controller = new AbortController();
  abortControllers.set(agentId, controller);

  let conv: Conversation = {
    ...base,
    error: null,
    streaming: true,
    messages: [
      ...base.messages,
      { role: 'user', content: text, display, segments: [{ type: 'text', text: display ?? text }] },
      { role: 'assistant', content: '', segments: [] },
    ],
  };
  putConversation(conv);

  const history = conv.messages
    .filter((m) => m.content)
    .map((m) => ({ role: m.role, content: m.content }));

  try {
    const response = await fetchRaw('/api/agents/chat', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agentId, messages: history }),
      signal: controller.signal,
    });
    if (!response.ok || !response.body) {
      const body = await response.text();
      throw new Error(`${response.status} ${response.statusText} — ${body.slice(0, 240)}`);
    }

    for await (const frame of parseSseStream(response.body)) {
      const payload = (frame.payload || {}) as Record<string, unknown>;
      if (frame.event === 'chunk') {
        conv = updateAssistant(conv, (segs) => appendText(segs, String(payload.text || '')));
      } else if (frame.event === 'agent_event') {
        const kind = String(payload.eventKind || '');
        const data = (payload.eventData || {}) as Record<string, unknown>;
        conv = updateAssistant(conv, (segs) => applyAgentEvent(segs, kind, data));
      } else if (frame.event === 'done') {
        conv = {
          ...conv,
          lastDurationMs: Number(payload.durationMs) || undefined,
          traceExplorerPath: payload.traceExplorerPath
            ? String(payload.traceExplorerPath)
            : conv.traceExplorerPath,
        };
        if (payload.traceId) {
          const messages = conv.messages.slice();
          const last = messages[messages.length - 1];
          if (last?.role === 'assistant') {
            messages[messages.length - 1] = { ...last, traceId: String(payload.traceId) };
            conv = { ...conv, messages };
          }
        }
      } else if (frame.event === 'error') {
        conv = { ...conv, error: String(payload.message || 'Agent stream failed') };
      }
      putConversation(conv);
    }
  } catch (err) {
    const aborted = (err as Error).name === 'AbortError';
    conv = { ...conv, error: aborted ? null : String(err) };
  } finally {
    abortControllers.delete(agentId);
    // Drop an empty trailing assistant message (abort before first token).
    const messages = conv.messages.slice();
    const last = messages[messages.length - 1];
    if (last && last.role === 'assistant' && last.segments.length === 0) messages.pop();
    putConversation({ ...conv, messages, streaming: false });
  }
}
