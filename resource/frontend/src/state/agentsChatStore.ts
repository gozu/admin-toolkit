// Agents chat store — module-scoped singleton so a running agent turn
// survives page navigation. Conversations are keyed by conversation id (one
// ACTIVE conversation per agent via activeConvIdByAgent), on the active host.
// Streaming consumes the /api/agents/chat SSE proxy: token deltas plus the
// typed agent event protocol (tool_call / tool_result / plan / execution).
//
// Persistence: when the admin enables chat storage in plugin settings
// (/api/chat/config → enabled), every settled turn is POSTed to the server
// (Agent Hub-style SQL store, scoped per user + fleet host) and the history
// drawer lists/reopens past conversations. localStorage stays a cache only.
import { fetchJson, fetchRaw } from '../utils/api';
import { parseSseStream } from '../utils/sseStream';
import { createSyncStore } from './createSyncStore';
import { subscribeSessionEpoch } from './sessionCache';

export interface AgentInfo {
  id: string;
  name: string;
  /** Saved-model active version id (e.g. 'v1') — feeds the DSS config deep link. */
  activeVersion?: string;
  /** Project holding the agent saved model (ADMINTOOLKIT) — deep-link segment. */
  projectKey?: string;
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
  /** Full confirm window in seconds — drives the drain bar on pending cards. */
  ttlSeconds?: number;
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
  /** First target — kept for back-compat consumers; targets is the full list. */
  target: Record<string, unknown> | null;
  /** All targets of a batched item (one action × N objects, one plan/token). */
  targets: Record<string, unknown>[] | null;
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
  | { type: 'action_items'; batch: ActionItemsCardData }
  /** A safety-gate refusal that an admin can clear — rendered as an inline
   *  callout with a deep link to the config that clears it. `code` is the
   *  structured error code from the tool result (e.g. action-disabled);
   *  `link` is the backend's machine-readable internal deep link
   *  ({page: PageId, label}) when the clearing surface is a toolkit page. */
  | { type: 'gate_hint'; code: string; message?: string; link?: GateLink };

export interface GateLink {
  page: string;
  label: string;
}

export interface ChatMessage {
  /** Client-minted uuid — the server upserts persisted messages by this id. */
  id: string;
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
  /** Wall-clock duration of the turn that produced this assistant message. */
  durationMs?: number;
}

export interface Conversation {
  /** Client-minted uuid4 — the server row is created on the first turn. */
  id: string;
  agentId: string;
  title?: string;
  messages: ChatMessage[];
  streaming: boolean;
  /** Epoch ms when the in-flight turn started — powers the elapsed ticker. */
  streamStartedAt?: number;
  error: string | null;
  lastDurationMs?: number;
  /** DSS-relative path of the Trace Explorer webapp on this host, if one exists. */
  traceExplorerPath?: string;
}

export interface ConversationMeta {
  id: string;
  agentId: string;
  title: string;
  lastModified?: string;
}

export interface TraceExplorerStatus {
  installed: boolean;
  provisioned: boolean;
  projectKey: string;
  /** True only on the local hub — the readTraceFromLS handoff needs the
   * explorer to share the browser origin (localStorage is per-origin). */
  sameOrigin: boolean;
  webAppId?: string;
  viewPath?: string;
}

export interface ProvisionStep {
  step: string;
  status: string;
  message?: string;
}

export interface ProvisionResult {
  ok: boolean;
  steps: ProvisionStep[];
  webAppId?: string;
  viewPath?: string;
}

interface ChatPersistenceState {
  /** False until /api/chat/config has answered for the current host. */
  loaded: boolean;
  enabled: boolean;
  mode?: string;
}

interface AgentsChatState {
  conversations: Record<string, Conversation>;
  /** The conversation currently shown per agent (others live server-side). */
  activeConvIdByAgent: Record<string, string>;
  /** Store-owned so the action-item handoff can switch the visible agent. */
  selectedAgentId: string;
  persistence: ChatPersistenceState;
  conversationList: ConversationMeta[];
  traceExplorer: TraceExplorerStatus | null;
}

const INITIAL_STATE: AgentsChatState = {
  conversations: {},
  activeConvIdByAgent: {},
  selectedAgentId: '',
  persistence: { loaded: false, enabled: false },
  conversationList: [],
  traceExplorer: null,
};

export const agentsChatStore = createSyncStore<AgentsChatState>(INITIAL_STATE, {
  sessionScoped: true,
});

function newId(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c?.randomUUID) return c.randomUUID();
  return `id-${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 10)}`;
}

// Chats survive a hard refresh via localStorage (cache only — the durable copy
// is server-side when persistence is enabled); the in-app Refresh (session
// epoch) keeps its clear-everything semantics, so the epoch bump also drops
// the snapshot.
const STORAGE_KEY = 'admin-toolkit:agentsChat';
// v2: conversations keyed by conversation id + activeConvIdByAgent (v1 blobs
// were keyed by agent id and never durable — discarded, no migration).
const STORAGE_VERSION = 2;

interface StoredState {
  conversations: Record<string, Conversation>;
  activeConvIdByAgent: Record<string, string>;
  selectedAgentId: string;
}

/** A hard refresh kills any in-flight stream: stop spinners, drop a dangling
 * empty assistant turn, and never rehydrate `streaming: true` (the abort
 * controllers don't survive a reload). */
function sanitizeStored(state: StoredState): StoredState {
  const conversations: Record<string, Conversation> = {};
  for (const [id, conv] of Object.entries(state.conversations || {})) {
    if (!conv?.id || !conv.agentId) continue;
    let messages = (conv.messages || []).filter((m) => m?.id);
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
  const activeConvIdByAgent: Record<string, string> = {};
  for (const [agentId, convId] of Object.entries(state.activeConvIdByAgent || {})) {
    if (conversations[convId]) activeConvIdByAgent[agentId] = convId;
  }
  return { conversations, activeConvIdByAgent, selectedAgentId: state.selectedAgentId || '' };
}

function readStored(): StoredState | null {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { v?: number; state?: StoredState };
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
    const stored: StoredState = {
      conversations: state.conversations,
      activeConvIdByAgent: state.activeConvIdByAgent,
      selectedAgentId: state.selectedAgentId,
    };
    globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify({ v: STORAGE_VERSION, state: stored }));
  } catch {
    // best effort — quota exceeded or storage unavailable
  }
}

const storedState = readStored();
if (storedState) agentsChatStore.patch(storedState);
agentsChatStore.subscribe(() => persistStored(agentsChatStore.get()));

// Guard against a bootstrap fetch from the previous host landing after an
// epoch bump (host switch) and repopulating the store with stale data.
let sessionEpochCounter = 0;
subscribeSessionEpoch(() => {
  sessionEpochCounter += 1;
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
  const state = agentsChatStore.get();
  const activeId = state.activeConvIdByAgent[agentId];
  const existing = activeId ? state.conversations[activeId] : undefined;
  return (
    existing || {
      id: newId(),
      agentId,
      messages: [],
      streaming: false,
      error: null,
    }
  );
}

function putConversation(conv: Conversation): void {
  const state = agentsChatStore.get();
  agentsChatStore.patch({
    conversations: { ...state.conversations, [conv.id]: conv },
    activeConvIdByAgent: { ...state.activeConvIdByAgent, [conv.agentId]: conv.id },
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
    const err = data.error as
      | { message?: string; code?: string; link?: { page?: string; label?: string } }
      | null
      | undefined;
    for (let i = segments.length - 1; i >= 0; i--) {
      const seg = segments[i];
      if (seg.type !== 'activity') continue;
      const idx = seg.items.findIndex((it) => it.running && it.name === data.name);
      if (idx === -1) continue;
      const items = seg.items.slice();
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
    // Admin-clearable safety-gate refusals get an inline callout with a deep
    // link to the config (dedup so retries don't stack callouts): either the
    // dedicated agent-execution-disabled card, or any error whose payload
    // carries a machine-readable internal link {page, label}.
    const link =
      err?.link && err.link.page
        ? { page: String(err.link.page), label: String(err.link.label || 'Open settings') }
        : undefined;
    if (err?.code && (err.code === 'agent-execution-disabled' || link)) {
      const last = segments[segments.length - 1];
      if (!(last && last.type === 'gate_hint' && last.code === err.code)) {
        segments.push({
          type: 'gate_hint',
          code: err.code,
          message: err.message ? String(err.message) : undefined,
          link,
        });
      }
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
        ttlSeconds: expiresIn,
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
    const singleTarget =
      item.target && typeof item.target === 'object' && !Array.isArray(item.target)
        ? (item.target as Record<string, unknown>)
        : null;
    const targets = Array.isArray(item.targets)
      ? item.targets.filter(
          (t): t is Record<string, unknown> => Boolean(t) && typeof t === 'object' && !Array.isArray(t),
        )
      : null;
    out.push({
      id: String(item.id || `ai-${out.length}-${Date.now().toString(16)}`),
      title,
      why: String(item.why || ''),
      host: String(item.host || 'local'),
      risk: risk === 'red' || risk === 'green' ? risk : 'amber',
      action,
      target: singleTarget ?? (targets?.[0] || null),
      targets: targets && targets.length > 0 ? targets : singleTarget ? [singleTarget] : null,
      evidence: Array.isArray(item.evidence) ? item.evidence.map(String).slice(0, 6) : [],
      actionable: Boolean(item.actionable) && action !== null,
      validation: item.validation ? String(item.validation) : null,
    });
  }
  return out;
}

// ── server persistence (Agent Hub-style SQL store) ─────────────────────────

interface ServerMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  display?: string | null;
  segments?: Segment[];
  traceId?: string | null;
  hasTrace?: boolean;
}

interface ServerConversation {
  id: string;
  agentId: string;
  title: string;
  traceExplorerPath?: string;
  messages: ServerMessage[];
}

let bootstrapInflight = false;

/** Load /api/chat/config + trace-explorer status once per host (the session
 * epoch resets `persistence.loaded`, so a host switch re-fetches). */
export async function ensureChatBootstrapped(): Promise<void> {
  const state = agentsChatStore.get();
  if (state.persistence.loaded || bootstrapInflight) return;
  bootstrapInflight = true;
  const epoch = sessionEpochCounter;
  try {
    const [config, explorer] = await Promise.all([
      fetchJson<{ enabled: boolean; mode?: string }>('/api/chat/config').catch(() => ({
        enabled: false as const,
      })),
      fetchJson<TraceExplorerStatus>('/api/agents/trace-explorer/status').catch(() => null),
    ]);
    if (epoch !== sessionEpochCounter) return; // host switched mid-fetch
    agentsChatStore.patch({
      persistence: { loaded: true, enabled: config.enabled, mode: (config as { mode?: string }).mode },
      traceExplorer: explorer,
    });
    if (config.enabled) void loadConversationList();
  } finally {
    bootstrapInflight = false;
  }
}

export async function loadConversationList(): Promise<void> {
  const epoch = sessionEpochCounter;
  try {
    const data = await fetchJson<{ enabled: boolean; conversations: ConversationMeta[] }>(
      '/api/chat/conversations',
    );
    if (epoch !== sessionEpochCounter) return;
    agentsChatStore.patch({ conversationList: data.conversations || [] });
  } catch {
    // history stays whatever it was — the drawer shows its empty state
  }
}

/** Open a past conversation: local copy if cached, else fetched from the
 * server (traces stay server-side; hasTrace flags the durable fallback). */
export async function openConversation(conversationId: string): Promise<void> {
  const state = agentsChatStore.get();
  const cached = state.conversations[conversationId];
  if (cached) {
    agentsChatStore.patch({
      activeConvIdByAgent: { ...state.activeConvIdByAgent, [cached.agentId]: cached.id },
      selectedAgentId: cached.agentId,
    });
    return;
  }
  const data = await fetchJson<{ conversation: ServerConversation }>(
    `/api/chat/conversations/${encodeURIComponent(conversationId)}`,
  );
  const server = data.conversation;
  const conv: Conversation = {
    id: server.id,
    agentId: server.agentId,
    title: server.title || undefined,
    streaming: false,
    error: null,
    traceExplorerPath: server.traceExplorerPath || undefined,
    messages: (server.messages || []).map((m) => ({
      id: m.id,
      role: m.role,
      content: m.content || '',
      display: m.display || undefined,
      segments: (m.segments || []).map((s) =>
        s.type === 'activity'
          ? { ...s, items: s.items.map((it) => ({ ...it, running: false })) }
          : s,
      ),
      traceId: m.traceId || undefined,
    })),
  };
  const fresh = agentsChatStore.get();
  agentsChatStore.patch({
    conversations: { ...fresh.conversations, [conv.id]: conv },
    activeConvIdByAgent: { ...fresh.activeConvIdByAgent, [conv.agentId]: conv.id },
    selectedAgentId: conv.agentId,
  });
}

export async function renameConversation(conversationId: string, title: string): Promise<void> {
  const trimmed = title.trim();
  if (!trimmed) return;
  const state = agentsChatStore.get();
  const conv = state.conversations[conversationId];
  agentsChatStore.patch({
    conversations: conv
      ? { ...state.conversations, [conversationId]: { ...conv, title: trimmed } }
      : state.conversations,
    conversationList: state.conversationList.map((c) =>
      c.id === conversationId ? { ...c, title: trimmed } : c,
    ),
  });
  try {
    await fetchJson(`/api/chat/conversations/${encodeURIComponent(conversationId)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: trimmed }),
    });
  } catch {
    void loadConversationList(); // roll back to server truth
  }
}

export async function deleteConversation(conversationId: string): Promise<void> {
  const state = agentsChatStore.get();
  const conversations = { ...state.conversations };
  const conv = conversations[conversationId];
  delete conversations[conversationId];
  const activeConvIdByAgent = { ...state.activeConvIdByAgent };
  if (conv && activeConvIdByAgent[conv.agentId] === conversationId) {
    // Deleting the agent's active conversation while it streams: abort the
    // in-flight turn first, or its next SSE frame would putConversation() the
    // row back into state and the settle would re-persist it server-side
    // (same guard startNewConversation / clearAllConversations already use).
    abortControllers.get(conv.agentId)?.abort();
    abortControllers.delete(conv.agentId);
    delete activeConvIdByAgent[conv.agentId];
  }
  agentsChatStore.patch({
    conversations,
    activeConvIdByAgent,
    conversationList: state.conversationList.filter((c) => c.id !== conversationId),
  });
  try {
    await fetchJson(`/api/chat/conversations/${encodeURIComponent(conversationId)}`, {
      method: 'DELETE',
    });
  } catch {
    void loadConversationList();
  }
}

/** Conversation title: explicit title, else the first user message clipped.
 * Exported for the history drawer's local (not-yet-persisted) rows. */
export function deriveTitle(conv: Conversation): string {
  if (conv.title) return conv.title;
  const firstUser = conv.messages.find((m) => m.role === 'user');
  const text = (firstUser?.display ?? firstUser?.content ?? '').trim().replace(/\s+/g, ' ');
  return text.length > 60 ? `${text.slice(0, 57)}…` : text || 'New conversation';
}

/** Fire-and-forget: POST the given messages (by position) of a settled
 * conversation to the server store. Called after every settled turn and
 * after post-settle segment mutations (plan decisions, handoff locks). */
function persistMessages(conv: Conversation, positions: number[]): void {
  const state = agentsChatStore.get();
  if (!state.persistence.enabled || positions.length === 0) return;
  const title = deriveTitle(conv);
  if (!conv.title) {
    const cur = agentsChatStore.get();
    const stored = cur.conversations[conv.id];
    if (stored) {
      agentsChatStore.patch({
        conversations: { ...cur.conversations, [conv.id]: { ...stored, title } },
      });
    }
  }
  const messages = positions
    .map((pos) => ({ message: conv.messages[pos], pos }))
    .filter((e) => e.message)
    .map(({ message, pos }) => ({
      id: message.id,
      role: message.role,
      content: message.content,
      display: message.display,
      segments: message.segments,
      traceId: message.traceId,
      position: pos,
    }));
  const traceId = messages.map((m) => m.traceId).filter(Boolean).pop();
  const epoch = sessionEpochCounter;
  void fetchJson<{ conversation: { id: string; title: string } }>(
    `/api/chat/conversations/${encodeURIComponent(conv.id)}/turn`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        agentId: conv.agentId,
        title,
        messages,
        traceId,
        lastDurationMs: conv.lastDurationMs,
        traceExplorerPath: conv.traceExplorerPath,
      }),
    },
  )
    .then(() => {
      if (epoch !== sessionEpochCounter) return;
      const cur = agentsChatStore.get();
      const meta: ConversationMeta = {
        id: conv.id,
        agentId: conv.agentId,
        title,
        lastModified: new Date().toISOString(),
      };
      agentsChatStore.patch({
        conversationList: [meta, ...cur.conversationList.filter((c) => c.id !== conv.id)],
      });
    })
    .catch(() => {
      // best effort — the localStorage cache still holds the conversation
    });
}

/** Persist every message whose segments the predicate matches (post-settle
 * mutations touch existing rows — same ids, updated segments). */
function persistWhere(conv: Conversation, match: (msg: ChatMessage) => boolean): void {
  const positions = conv.messages
    .map((msg, i) => (match(msg) ? i : -1))
    .filter((i) => i >= 0);
  persistMessages(conv, positions);
}

/** One-click agents provisioning (the Agents page empty-state CTA) — creates
 * the ADMINTOOLKIT tool + agent instances on the active host, no CLI. */
export async function provisionAgents(): Promise<ProvisionResult> {
  return fetchJson<ProvisionResult>('/api/agents/provision', { method: 'POST' });
}

export async function provisionTraceExplorer(): Promise<ProvisionResult> {
  const result = await fetchJson<ProvisionResult>('/api/agents/trace-explorer/provision', {
    method: 'POST',
  });
  if (result.ok) {
    const explorer = await fetchJson<TraceExplorerStatus>('/api/agents/trace-explorer/status').catch(
      () => null,
    );
    if (explorer) agentsChatStore.patch({ traceExplorer: explorer });
  }
  return result;
}

// ── conversation mutations ──────────────────────────────────────────────────

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
  const next = { ...conv, messages };
  putConversation(next);
  persistWhere(next, (msg) =>
    msg.segments.some((seg) => seg.type === 'plan' && seg.plan.confirmToken === confirmToken),
  );
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
  const batched = item.targets && item.targets.length > 1;
  // Batched items carry the full targets[] for the model (ONE plan call with
  // targets); the display variant shows a compact ×N instead of raw JSON.
  const targetPart = batched
    ? forModel
      ? `targets=${JSON.stringify(item.targets)}`
      : `×${item.targets!.length} targets`
    : `target=${JSON.stringify(item.target)}`;
  const head = `${index + 1}.${ref} ${item.title} — action=${item.action} host=${item.host} ${targetPart}${itemRef}`;
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
  const nextSource: Conversation = {
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
  };
  putConversation(nextSource);
  persistWhere(nextSource, (msg) =>
    msg.segments.some((seg) => seg.type === 'action_items' && seg.batch.batchId === batchId),
  );

  const text =
    `Action-item batch handoff (batch ${batchId}, ${actionable.length} item(s) selected by the user from another agent's findings).\n` +
    `Plan EVERY item below — one plan_admin_action call per item, passing its item_ref verbatim. ` +
    `Items carrying targets=[...] are batched: pass the targets array as-is in that ONE call (one plan, one token, N targets). ` +
    `Present each plan and WAIT for my approval. Do NOT execute anything yet.\n\n` +
    actionable.map((item, i) => handoffLine(batchId, item, i, true)).join('\n');
  const display =
    `Plan the ${actionable.length} action item(s) I selected from the checklist. ` +
    `Present each plan and wait for my approval before executing.\n\n` +
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
  // Echo the exact canonicalTarget alongside the token. Over the chat SSE
  // surface the prior plan's tool-call is not in the flattened history, so
  // supplying the target here lets the agent execute this approved plan
  // directly instead of re-planning to rebuild it (the backend still
  // re-verifies the HMAC token over action|host|target, so a mismatch is
  // refused server-side).
  const targetJson = (plan: PlanCardData) => JSON.stringify(plan.canonicalTarget);
  const text =
    plans.length === 1
      ? `Approved — I confirm. Execute the planned ${plans[0].action} on host ${plans[0].host} with target ${targetJson(plans[0])}, confirm=true and confirm_token ${plans[0].confirmToken}${plans[0].itemRef ? ` and item_ref ${JSON.stringify(plans[0].itemRef)}` : ''}. This is the plan you already presented — execute it directly with execute_admin_action; do not re-plan. Report the outcome and the auditId.`
      : `Approved — I confirm ALL ${plans.length} plans below. Execute each directly with execute_admin_action (do NOT re-plan — these are the plans you already presented), each with its exact target, confirm=true, its own confirm_token (and its item_ref where given); report each outcome and auditId separately:\n` +
        plans
          .map(
            (plan, i) =>
              `${i + 1}. ${plan.action} on host ${plan.host} — target ${targetJson(plan)} — confirm_token ${plan.confirmToken}${plan.itemRef ? ` item_ref=${JSON.stringify(plan.itemRef)}` : ''}`,
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

/** Start a fresh chat session: drop the active conversation of EVERY agent,
 * not just the visible one. Sample prompts route by hidden role (triage /
 * scoping / actuator), so clearing only the selected agent leaves the other
 * roles' conversations "active" — the next routed prompt would silently
 * resume them. Previous conversations stay server-side when persistence is
 * enabled (reopen from the history drawer); locally they are dropped. */
export function clearAllConversations(): void {
  for (const controller of abortControllers.values()) controller.abort();
  abortControllers.clear();
  agentsChatStore.patch({ conversations: {}, activeConvIdByAgent: {} });
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

  const turnIds = [newId(), newId()];
  const conv: Conversation = {
    ...base,
    error: null,
    streaming: true,
    streamStartedAt: Date.now(),
    messages: [
      ...base.messages,
      { id: turnIds[0], role: 'user', content: text, display, segments: [{ type: 'text', text: display ?? text }] },
      { id: turnIds[1], role: 'assistant', content: '', segments: [] },
    ],
  };
  return streamTurn(agentId, conv, turnIds);
}

/** Re-run the trailing turn after a failure: keeps the user message in place
 * and reuses BOTH message ids, so the server-side upsert (keyed by message
 * id + position) overwrites the failed rows instead of duplicating the turn. */
export function retryLastTurn(agentId: string): void {
  const base = getConversation(agentId);
  if (base.streaming) return;
  const messages = base.messages;
  let userIdx = messages.length - 1;
  while (userIdx >= 0 && messages[userIdx].role !== 'user') userIdx--;
  if (userIdx < 0) return;
  const prevAssistant = messages[userIdx + 1];
  const assistantId = prevAssistant?.role === 'assistant' ? prevAssistant.id : newId();
  const conv: Conversation = {
    ...base,
    error: null,
    streaming: true,
    streamStartedAt: Date.now(),
    messages: [
      ...messages.slice(0, userIdx + 1),
      { id: assistantId, role: 'assistant', content: '', segments: [] },
    ],
  };
  void streamTurn(agentId, conv, [messages[userIdx].id, assistantId]);
}

/** Stream one turn (the trailing user+assistant pair of `conv`, identified by
 * `turnIds`) over the chat SSE proxy, settling and persisting at the end. */
async function streamTurn(
  agentId: string,
  initial: Conversation,
  turnIds: string[],
): Promise<void> {
  abortControllers.get(agentId)?.abort();
  const controller = new AbortController();
  abortControllers.set(agentId, controller);

  let conv = initial;
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
        const doneDuration = Number(payload.durationMs) || undefined;
        if (payload.traceId || doneDuration) {
          const messages = conv.messages.slice();
          const last = messages[messages.length - 1];
          if (last?.role === 'assistant') {
            messages[messages.length - 1] = {
              ...last,
              traceId: payload.traceId ? String(payload.traceId) : last.traceId,
              durationMs: doneDuration ?? last.durationMs,
            };
            conv = { ...conv, messages };
          }
        }
      } else if (frame.event === 'error') {
        conv = { ...conv, error: String(payload.message || 'Agent stream failed') };
      }
      // A frame already yielded before an abort ("New conversation" mid-turn)
      // must not re-insert the deleted conversation.
      if (!controller.signal.aborted) putConversation(conv);
    }
  } catch (err) {
    const aborted = (err as Error).name === 'AbortError';
    conv = { ...conv, error: aborted ? null : String(err) };
  } finally {
    abortControllers.delete(agentId);
    // Skip the settle entirely when the conversation was deleted mid-stream
    // ("New conversation") — putConversation would resurrect it otherwise.
    if (agentsChatStore.get().conversations[conv.id]) {
      // Drop an empty trailing assistant message (abort before first token).
      const messages = conv.messages.slice();
      const last = messages[messages.length - 1];
      if (last && last.role === 'assistant' && last.segments.length === 0) messages.pop();
      const settled = { ...conv, messages, streaming: false, streamStartedAt: undefined };
      putConversation(settled);
      // Auto-persist the settled turn: the user message + the assistant reply
      // (or just the user message when the reply was aborted pre-token).
      persistWhere(settled, (msg) => turnIds.includes(msg.id));
    }
  }
}
