import { useEffect, useRef } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  patchQueuedMessage,
  removeQueuedMessage,
  type QueuedMessage,
} from '../../state/agentsChatStore';

/** Messages typed while the agent was busy, waiting at the tail of the
 * transcript. Each sends automatically when the agent next goes idle; until
 * then ✕ drops it and ↑ edits it in place. Emptying the text while editing
 * removes the message immediately (the queue holds while an edit is open). */
export function QueuedMessageList({
  agentId,
  queue,
}: {
  agentId: string;
  queue: QueuedMessage[];
}) {
  return (
    <div className="space-y-2 pb-1">
      <AnimatePresence initial={false}>
        {queue.map((msg) => (
          <motion.div
            key={msg.id}
            initial={{ opacity: 0, scale: 0.94, y: 8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, transition: { duration: 0.12 } }}
            transition={{ type: 'spring', stiffness: 480, damping: 32 }}
          >
            <QueuedBubble agentId={agentId} msg={msg} />
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

function QueuedBubble({ agentId, msg }: { agentId: string; msg: QueuedMessage }) {
  const editing = Boolean(msg.editing);
  const textRef = useRef<HTMLTextAreaElement>(null);
  // Latest props without effect deps — the edit-start effect below snapshots
  // from here so Esc can restore text AND preset/display framing.
  const latestRef = useRef(msg);
  const originalRef = useRef(msg);
  useEffect(() => {
    latestRef.current = msg;
  });

  // Entering edit mode (via the ↑ button here, or ArrowUp in the composer):
  // snapshot for cancel, then land the caret at the end.
  useEffect(() => {
    if (!editing) return;
    originalRef.current = latestRef.current;
    const el = textRef.current;
    if (el) {
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    }
  }, [editing]);

  // Editor grows with content like the main composer (wrap-aware, capped).
  useEffect(() => {
    if (!editing) return;
    const el = textRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [editing, msg.text]);

  const onEditChange = (value: string) => {
    if (value === '') {
      // All characters deleted — the message leaves the queue immediately.
      removeQueuedMessage(agentId, msg.id);
      return;
    }
    // First keystroke divorces the message from any preset framing — a preset
    // card title would otherwise keep describing text it no longer matches.
    patchQueuedMessage(agentId, msg.id, { text: value, display: undefined, preset: undefined });
  };

  const commitEdit = () => {
    if (!msg.text.trim()) removeQueuedMessage(agentId, msg.id);
    else patchQueuedMessage(agentId, msg.id, { editing: false });
  };

  const cancelEdit = () => {
    const orig = originalRef.current;
    patchQueuedMessage(agentId, msg.id, {
      text: orig.text,
      display: orig.display,
      preset: orig.preset,
      editing: false,
    });
  };

  return (
    <div className="group flex items-end justify-end gap-2">
      <span className="pb-1.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
        queued
      </span>
      <div className={`relative ${editing ? 'w-full max-w-[40rem]' : 'max-w-[min(85%,40rem)]'}`}>
        {editing ? (
          <>
            <textarea
              ref={textRef}
              value={msg.text}
              onChange={(e) => onEditChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  commitEdit();
                } else if (e.key === 'Escape') {
                  e.preventDefault();
                  cancelEdit();
                }
              }}
              rows={1}
              aria-label="Edit queued message"
              className="chat-user-bubble w-full rounded-xl rounded-br-sm px-3.5 py-2 text-sm text-[var(--text-primary)] resize-none overflow-y-auto focus:outline-none focus:border-[var(--accent)]"
            />
            <div className="pt-0.5 text-right text-[10px] text-[var(--text-muted)]">
              Enter saves · Esc cancels · clearing all text removes it
            </div>
          </>
        ) : (
          <div className="chat-user-bubble rounded-xl rounded-br-sm px-3.5 py-2 text-sm text-[var(--text-primary)] whitespace-pre-wrap opacity-75">
            {msg.display ?? msg.preset?.title ?? msg.text}
          </div>
        )}
        <div className="absolute -top-2.5 -right-2 flex items-center gap-1">
          {!editing && (
            <button
              onClick={() => patchQueuedMessage(agentId, msg.id, { editing: true })}
              className="flex h-5 w-5 items-center justify-center rounded-full border border-[var(--border-default)] bg-[var(--bg-elevated)] text-[10px] text-[var(--text-muted)] shadow-sm transition-colors hover:text-[var(--text-primary)] hover:border-[var(--accent)]"
              title="Edit this queued message (holds the queue while you edit)"
              aria-label="Edit queued message"
            >
              ↑
            </button>
          )}
          <button
            onClick={() => removeQueuedMessage(agentId, msg.id)}
            className="flex h-5 w-5 items-center justify-center rounded-full border border-[var(--border-default)] bg-[var(--bg-elevated)] text-[10px] text-[var(--text-muted)] shadow-sm transition-colors hover:text-[var(--danger)] hover:border-[var(--danger)]"
            title="Remove from the queue — it will not be sent"
            aria-label="Remove queued message"
          >
            ✕
          </button>
        </div>
      </div>
    </div>
  );
}
