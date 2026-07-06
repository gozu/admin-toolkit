import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import {
  deleteConversation,
  loadConversationList,
  openConversation,
  renameConversation,
  type AgentInfo,
  type ConversationMeta,
} from '../../state/agentsChatStore';

const EASE_OUT: [number, number, number, number] = [0.16, 1, 0.3, 1];

/** "3m ago"-style relative time; falls back to the raw date for old items. */
function relativeTime(iso: string | undefined, now: number): string {
  if (!iso) return '';
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return '';
  const s = Math.max(0, Math.floor((now - then) / 1000));
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)}d ago`;
  return new Date(then).toLocaleDateString();
}

function ConversationRow({
  conv,
  agentName,
  active,
  onOpen,
}: {
  conv: ConversationMeta;
  agentName: string;
  active: boolean;
  onOpen: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(conv.title);
  // Fresh per drawer open — the row subtree remounts with the drawer.
  const [now] = useState(() => Date.now());

  const commitRename = () => {
    setEditing(false);
    if (title.trim() && title.trim() !== conv.title) {
      void renameConversation(conv.id, title.trim());
    } else {
      setTitle(conv.title);
    }
  };

  return (
    <div
      className={`group flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 transition-colors ${
        active
          ? 'border-[var(--accent)]/40 bg-[var(--accent-muted)]'
          : 'border-[var(--border-default)] bg-[var(--bg-surface)] hover:bg-[var(--bg-hover)]'
      }`}
    >
      {editing ? (
        <input
          autoFocus
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commitRename();
            if (e.key === 'Escape') {
              setTitle(conv.title);
              setEditing(false);
            }
          }}
          className="min-w-0 flex-1 rounded border border-[var(--accent)]/40 bg-[var(--bg-elevated)] px-1.5 py-0.5 text-xs text-[var(--text-primary)] focus:outline-none"
        />
      ) : (
        <button onClick={onOpen} className="min-w-0 flex-1 text-left" title={conv.title}>
          <span className="block truncate text-xs text-[var(--text-primary)]">
            {conv.title || 'Untitled conversation'}
          </span>
          <span className="block truncate text-[10px] text-[var(--text-muted)]">
            {agentName}
            {conv.lastModified ? ` · ${relativeTime(conv.lastModified, now)}` : ''}
          </span>
        </button>
      )}
      {!editing && (
        <>
          <button
            onClick={() => setEditing(true)}
            title="Rename"
            aria-label="Rename conversation"
            className="shrink-0 rounded p-1 text-[var(--text-muted)] opacity-0 transition-all group-hover:opacity-100 hover:text-[var(--text-primary)] focus:opacity-100"
          >
            <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 4.5l3 3L8 19H5v-3L16.5 4.5z" />
            </svg>
          </button>
          <button
            onClick={() => void deleteConversation(conv.id)}
            title="Delete conversation"
            aria-label="Delete conversation"
            className="shrink-0 rounded p-1 text-[var(--text-muted)] opacity-0 transition-all group-hover:opacity-100 hover:text-[var(--danger)] focus:opacity-100"
          >
            <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </>
      )}
    </div>
  );
}

/**
 * Compact history slide-over (no permanent second column, per UI soul):
 * server-persisted conversations for the current user + host, most recent
 * first. Only reachable when chat persistence is enabled in plugin settings.
 */
export function ChatHistoryDrawer({
  open,
  onClose,
  conversations,
  agents,
  activeConvIds,
}: {
  open: boolean;
  onClose: () => void;
  conversations: ConversationMeta[];
  agents: AgentInfo[];
  activeConvIds: string[];
}) {
  const reduced = useReducedMotion();

  useEffect(() => {
    if (!open) return;
    void loadConversationList();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const agentName = (agentId: string) =>
    agents.find((a) => a.id === agentId)?.name.replace(/^ATK /, '') || agentId;

  const openConv = (id: string) => {
    void openConversation(id).catch(() => loadConversationList());
    onClose();
  };

  return createPortal(
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            className="fixed inset-0 z-40 bg-black/30"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: reduced ? 0 : 0.15 }}
            onClick={onClose}
          />
          <motion.aside
            className="fixed inset-y-0 right-0 z-50 flex w-[22rem] max-w-[92vw] flex-col border-l border-[var(--border-default)] bg-[var(--bg-elevated)] shadow-2xl"
            initial={{ x: reduced ? 0 : 360, opacity: reduced ? 0 : 1 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: reduced ? 0 : 360, opacity: reduced ? 0 : 1 }}
            transition={reduced ? { duration: 0.1 } : { duration: 0.22, ease: EASE_OUT }}
          >
            <div className="flex items-center gap-2 border-b border-[var(--border-default)] px-3.5 py-2.5">
              <span className="text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
                Chat history
              </span>
              <span className="text-[10px] text-[var(--text-muted)]">{conversations.length}</span>
              <button
                onClick={onClose}
                className="ml-auto rounded p-1 text-[var(--text-muted)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
                aria-label="Close chat history"
              >
                <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </div>

            <div className="flex-1 space-y-1.5 overflow-y-auto px-3.5 py-2.5">
              {conversations.length === 0 ? (
                <p className="pt-6 text-center text-xs text-[var(--text-muted)]">
                  No saved conversations on this host yet — settled chat turns are
                  saved automatically.
                </p>
              ) : (
                conversations.map((conv) => (
                  <ConversationRow
                    key={conv.id}
                    conv={conv}
                    agentName={agentName(conv.agentId)}
                    active={activeConvIds.includes(conv.id)}
                    onOpen={() => openConv(conv.id)}
                  />
                ))
              )}
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>,
    document.body,
  );
}
