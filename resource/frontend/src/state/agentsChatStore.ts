// Agents chat store — module-scoped singleton so a running agent turn
// survives page navigation. One conversation per agent id, on the active
// host. Streaming consumes the /api/agents/chat SSE proxy: token deltas plus
// the typed agent event protocol (tool_call / tool_result / plan / execution).
import { fetchRaw } from '../utils/api';
import { parseSseStream } from '../utils/sseStream';
import { createSyncStore } from './createSyncStore';

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

export interface PlanCardData {
  action: string;
  host: string;
  canonicalTarget: unknown;
  plan: Record<string, unknown>;
  confirmToken: string;
  expiresAt: number; // epoch ms
  decision?: 'approved' | 'rejected';
}

export interface ExecutionCardData {
  action: string;
  host: string;
  status: string;
  auditId?: number | null;
  auditWarning?: string;
  result?: unknown;
}

export type Segment =
  | { type: 'text'; text: string }
  | { type: 'activity'; items: ActivityItem[] }
  | { type: 'plan'; plan: PlanCardData }
  | { type: 'execution'; execution: ExecutionCardData };

export interface ChatMessage {
  role: 'user' | 'assistant';
  // Plain text sent back as history (assistant = concatenated text segments).
  content: string;
  segments: Segment[];
}

export interface Conversation {
  agentId: string;
  messages: ChatMessage[];
  streaming: boolean;
  error: string | null;
  lastDurationMs?: number;
}

interface AgentsChatState {
  conversations: Record<string, Conversation>;
}

export const agentsChatStore = createSyncStore<AgentsChatState>(
  { conversations: {} },
  { sessionScoped: true },
);

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
      },
    });
  }
  return segments;
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

export function abortAgentTurn(agentId: string): void {
  abortControllers.get(agentId)?.abort();
}

export function clearConversation(agentId: string): void {
  abortAgentTurn(agentId);
  const conversations = { ...agentsChatStore.get().conversations };
  delete conversations[agentId];
  agentsChatStore.patch({ conversations });
}

/** Send one user message and stream the agent's reply into the store. */
export async function sendAgentMessage(agentId: string, text: string): Promise<void> {
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
      { role: 'user', content: text, segments: [{ type: 'text', text }] },
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
        conv = { ...conv, lastDurationMs: Number(payload.durationMs) || undefined };
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
