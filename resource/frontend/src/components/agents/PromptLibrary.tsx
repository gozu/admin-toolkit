import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion } from 'framer-motion';
import { InfoDot } from '../common/InfoDot';
import {
  PROMPT_GROUPS,
  type CatalogGroup,
  type CatalogSection,
} from '../../utils/agentPromptCatalog';
import type { PresetMeta } from '../../state/agentsChatStore';

const EASE_OUT: [number, number, number, number] = [0.16, 1, 0.3, 1];

function SendIcon() {
  return (
    <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}

function PromptRow({
  label,
  prompt,
  onInsert,
  onSend,
}: {
  label: string;
  prompt: string;
  onInsert: (prompt: string) => void;
  onSend: (prompt: string) => void;
}) {
  return (
    <div className="group flex items-center gap-1">
      <button
        onClick={() => onInsert(prompt)}
        title={prompt}
        className="flex-1 min-w-0 truncate rounded-md border border-[var(--border-default)] bg-[var(--bg-surface)] px-2.5 py-1.5 text-left text-xs text-[var(--text-secondary)] transition-colors hover:border-[var(--accent)]/40 hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
      >
        {label}
      </button>
      <button
        onClick={() => onSend(prompt)}
        title="Send now"
        className="shrink-0 rounded-md border border-transparent p-1.5 text-[var(--text-muted)] opacity-0 transition-all group-hover:opacity-100 hover:border-[var(--accent)]/40 hover:text-[var(--accent)] focus:opacity-100"
      >
        <SendIcon />
      </button>
    </div>
  );
}

function Section({
  section,
  groupTitle,
  filter,
  onInsert,
  onSend,
}: {
  section: CatalogSection;
  groupTitle: string;
  filter: string;
  onInsert: (prompt: string) => void;
  onSend: (prompt: string, meta: PresetMeta) => void;
}) {
  const [open, setOpen] = useState(true);
  const prompts = useMemo(() => {
    if (!filter) return section.prompts;
    const needle = filter.toLowerCase();
    return section.prompts.filter(
      (p) => p.label.toLowerCase().includes(needle) || p.prompt.toLowerCase().includes(needle),
    );
  }, [section, filter]);
  if (prompts.length === 0) return null;
  const expanded = open || Boolean(filter);
  return (
    <div>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 py-1 text-left"
      >
        <span className="text-[10px] text-[var(--accent)]">{expanded ? '▾' : '▸'}</span>
        <span className="text-[11px] font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
          {section.title}
        </span>
        {section.eduId && <InfoDot eduId={section.eduId} />}
        <span className="ml-auto text-[10px] text-[var(--text-muted)]">{prompts.length}</span>
      </button>
      {expanded && (
        <div className="space-y-1 pb-2">
          <p className="px-0.5 pb-0.5 text-[10px] text-[var(--text-muted)]">{section.blurb}</p>
          {prompts.map((p) => (
            <PromptRow
              key={p.id}
              label={p.label}
              prompt={p.prompt}
              onInsert={onInsert}
              onSend={(prompt) =>
                onSend(prompt, {
                  kind: 'prompt',
                  title: p.label,
                  group: `${groupTitle} · ${section.title}`,
                  icon: 'prompt',
                })
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}

function Group({
  group,
  filter,
  onInsert,
  onSend,
}: {
  group: CatalogGroup;
  filter: string;
  onInsert: (prompt: string) => void;
  onSend: (prompt: string, meta: PresetMeta) => void;
}) {
  const matches = useMemo(() => {
    if (!filter) return true;
    const needle = filter.toLowerCase();
    return group.sections.some((section) =>
      section.prompts.some(
        (p) => p.label.toLowerCase().includes(needle) || p.prompt.toLowerCase().includes(needle),
      ),
    );
  }, [group, filter]);
  if (!matches) return null;
  return (
    <div className="space-y-2">
      <div className="sticky top-0 z-10 -mx-3.5 border-y border-[var(--border-default)] bg-[var(--bg-elevated)] px-3.5 py-1.5">
        <div className="text-xs font-bold uppercase tracking-widest text-[var(--accent)]">
          {group.title}
        </div>
        <p className="text-[10px] leading-relaxed text-[var(--text-muted)]">{group.blurb}</p>
      </div>
      {!filter && (
        <div className="rounded-lg border border-[var(--accent)]/30 bg-[var(--accent-muted)] p-2.5 space-y-1">
          <span className="text-[11px] font-semibold text-[var(--accent)]">
            ★ {group.megapromptTitle}
          </span>
          <p className="text-[10px] leading-relaxed text-[var(--text-secondary)]">
            {group.megapromptBlurb}
          </p>
          <div className="flex items-center gap-2 pt-0.5">
            <button
              onClick={() =>
                onSend(group.megaprompt, {
                  kind: 'megaprompt',
                  title: group.megapromptTitle,
                  group: group.title,
                  gist: group.megapromptBlurb,
                  icon: 'star',
                })
              }
              className="rounded-md bg-[var(--accent)] px-2.5 py-1 text-[11px] font-semibold text-white transition-opacity hover:opacity-90"
            >
              Run it
            </button>
            <button
              onClick={() => onInsert(group.megaprompt)}
              className="rounded-md border border-[var(--border-default)] px-2.5 py-1 text-[11px] text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-hover)]"
            >
              Edit first
            </button>
          </div>
        </div>
      )}
      {group.sections.map((section) => (
        <Section
          key={`${group.role}-${section.id}`}
          section={section}
          groupTitle={group.title}
          filter={filter}
          onInsert={onInsert}
          onSend={onSend}
        />
      ))}
    </div>
  );
}

/**
 * Right slide-in drawer with the full prompt catalog: search, then the two
 * headline groups (Health & Triage, Scoping & Architecture) plus Admin
 * Actions — each with its megaprompt hero and collapsible themed sections.
 * Clicking a prompt inserts it into the composer (edit before sending); the
 * arrow affordance sends it immediately, carrying PresetMeta so the
 * transcript shows a compact card instead of the raw prompt text.
 */
export function PromptLibrary({
  open,
  onClose,
  onInsert,
  onSend,
}: {
  open: boolean;
  onClose: () => void;
  onInsert: (prompt: string) => void;
  onSend: (prompt: string, meta: PresetMeta) => void;
}) {
  const [filter, setFilter] = useState('');

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const insert = (prompt: string) => {
    onInsert(prompt);
    onClose();
  };
  const send = (prompt: string, meta: PresetMeta) => {
    onSend(prompt, meta);
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
            transition={{ duration: 0.15 }}
            onClick={onClose}
          />
          <motion.aside
            className="fixed inset-y-0 right-0 z-50 flex w-[26rem] max-w-[92vw] flex-col border-l border-[var(--border-default)] bg-[var(--bg-elevated)] shadow-2xl"
            initial={{ x: 420 }}
            animate={{ x: 0 }}
            exit={{ x: 420 }}
            transition={{ duration: 0.22, ease: EASE_OUT }}
          >
            <div className="flex items-center gap-2 border-b border-[var(--border-default)] px-3.5 py-2.5">
              <span className="text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
                Prompt library
              </span>
              <button
                onClick={onClose}
                className="ml-auto rounded p-1 text-[var(--text-muted)] transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]"
                aria-label="Close prompt library"
              >
                <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </div>

            <div className="px-3.5 py-2">
              <input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Search prompts…"
                className="w-full rounded-md border border-[var(--border-default)] bg-[var(--bg-surface)] px-2.5 py-1.5 text-xs text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:border-[var(--accent)] focus:outline-none"
              />
            </div>

            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3.5 pb-4">
              {PROMPT_GROUPS.map((group) => (
                <Group key={group.role} group={group} filter={filter} onInsert={insert} onSend={send} />
              ))}
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>,
    document.body,
  );
}
