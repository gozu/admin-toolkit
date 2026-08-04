import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { LlmOption } from '../types';

interface ModelPickerProps {
  llms: LlmOption[];
  selectedId: string;
  onChange: (id: string) => void;
  placeholder?: string;
  className?: string;
  onEnterWithClosed?: () => void;
}

interface ConnectionGroup {
  connection: string;
  models: LlmOption[];
}

function groupKey(llm: LlmOption): string {
  return llm.connection || 'Other';
}

export function ModelPicker({
  llms,
  selectedId,
  onChange,
  placeholder = 'Select a model...',
  className,
  onEnterWithClosed,
}: ModelPickerProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [rawActiveId, setActiveId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const selected = useMemo(() => llms.find((l) => l.id === selectedId) ?? null, [llms, selectedId]);

  const groups = useMemo<ConnectionGroup[]>(() => {
    const map = new Map<string, LlmOption[]>();
    for (const llm of llms) {
      const key = groupKey(llm);
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(llm);
    }
    return Array.from(map.entries())
      .map(([connection, models]) => ({ connection, models }))
      .sort((a, b) => a.connection.localeCompare(b.connection));
  }, [llms]);

  const q = query.trim().toLowerCase();
  const isFiltering = q.length > 0;

  const filteredGroups = useMemo<ConnectionGroup[]>(() => {
    if (!isFiltering) return groups;
    return groups
      .map((g) => {
        const connMatches = g.connection.toLowerCase().includes(q);
        const models = connMatches
          ? g.models
          : g.models.filter((m) => (m.model || m.label).toLowerCase().includes(q));
        return { connection: g.connection, models };
      })
      .filter((g) => g.models.length > 0);
  }, [groups, q, isFiltering]);

  // Flattened, currently-visible (expanded) models — drives keyboard nav
  const visibleModels = useMemo(() => {
    return filteredGroups
      .filter((g) => isFiltering || expanded.has(g.connection))
      .flatMap((g) => g.models);
  }, [filteredGroups, expanded, isFiltering]);

  // Fall back to the first visible row when the raw active id drops out of view
  // (group collapsed/filtered away) — derived at render time, not via effect.
  const activeId =
    (rawActiveId && visibleModels.some((m) => m.id === rawActiveId) ? rawActiveId : visibleModels[0]?.id) ??
    null;

  const open = useCallback(() => {
    setIsOpen(true);
    setQuery('');
    // Open on the selected model's group; with nothing selected yet, a lone
    // connection expands so the list never opens on headers alone.
    setExpanded(
      selected
        ? new Set([groupKey(selected)])
        : groups.length === 1
          ? new Set([groups[0].connection])
          : new Set(),
    );
    setActiveId(selected?.id ?? null);
    requestAnimationFrame(() => inputRef.current?.focus());
  }, [selected, groups]);

  const close = useCallback(() => {
    setIsOpen(false);
    setQuery('');
  }, []);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        close();
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [close]);

  useEffect(() => {
    if (!listRef.current || !isOpen || !activeId) return;
    const el = listRef.current.querySelector(`[data-model-id="${CSS.escape(activeId)}"]`);
    el?.scrollIntoView({ block: 'nearest' });
  }, [activeId, isOpen]);

  const handleSelect = useCallback(
    (id: string) => {
      onChange(id);
      close();
      triggerRef.current?.focus();
    },
    [onChange, close],
  );

  const toggleGroup = useCallback((connection: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(connection)) next.delete(connection);
      else next.add(connection);
      return next;
    });
  }, []);

  const handleInputKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      switch (e.key) {
        case 'ArrowDown': {
          e.preventDefault();
          if (visibleModels.length === 0) break;
          const idx = visibleModels.findIndex((m) => m.id === activeId);
          setActiveId(visibleModels[(idx + 1) % visibleModels.length].id);
          break;
        }
        case 'ArrowUp': {
          e.preventDefault();
          if (visibleModels.length === 0) break;
          const idx = visibleModels.findIndex((m) => m.id === activeId);
          setActiveId(visibleModels[(idx - 1 + visibleModels.length) % visibleModels.length].id);
          break;
        }
        case 'Enter': {
          e.preventDefault();
          if (activeId) handleSelect(activeId);
          break;
        }
        case 'Escape': {
          e.preventDefault();
          e.stopPropagation();
          close();
          triggerRef.current?.focus();
          break;
        }
      }
    },
    [visibleModels, activeId, handleSelect, close],
  );

  const triggerKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (isOpen) return;
      if (e.key === 'Enter') {
        e.preventDefault();
        onEnterWithClosed?.();
      } else if (e.key === 'ArrowDown' || e.key === ' ') {
        e.preventDefault();
        open();
      }
    },
    [isOpen, open, onEnterWithClosed],
  );

  return (
    <div ref={containerRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => (isOpen ? close() : open())}
        onKeyDown={triggerKeyDown}
        className={`flex items-center justify-between gap-2 ${className ?? ''}`}
      >
        {selected ? (
          <span className="flex items-baseline gap-2 min-w-0">
            <span className="truncate">{selected.model || selected.label}</span>
            <span className="shrink-0 text-[10px] uppercase tracking-wide text-[var(--text-tertiary)]">
              {selected.connection || 'Other'}
            </span>
          </span>
        ) : (
          <span className="text-[var(--text-tertiary)]">{placeholder}</span>
        )}
        <svg
          className={`pointer-events-none shrink-0 w-4 h-4 text-[var(--text-tertiary)] transition-transform ${isOpen ? 'rotate-180' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {isOpen && (
        <div className="absolute z-50 left-0 right-0 top-full mt-1 rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] shadow-lg dropdown-enter overflow-hidden">
          <div className="p-2 border-b border-[var(--border-default)]">
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleInputKeyDown}
              placeholder="Filter by model or connection..."
              className="w-full rounded-md border border-[var(--border-default)] bg-[var(--bg-base)] text-[var(--text-primary)] px-2.5 py-1.5 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-[var(--accent)]"
              autoComplete="off"
              spellCheck={false}
            />
          </div>
          <div ref={listRef} className="max-h-[280px] overflow-y-auto py-1">
            {filteredGroups.length === 0 ? (
              <div className="px-3 py-2 text-sm text-[var(--text-tertiary)]">No matches</div>
            ) : (
              filteredGroups.map((g) => {
                const isExpanded = isFiltering || expanded.has(g.connection);
                return (
                  <div key={g.connection}>
                    <button
                      type="button"
                      onClick={() => !isFiltering && toggleGroup(g.connection)}
                      disabled={isFiltering}
                      className="w-full flex items-center justify-between gap-2 px-3 py-1.5 text-left disabled:cursor-default"
                    >
                      <span className="text-xs font-semibold uppercase tracking-wider text-[var(--text-tertiary)] truncate">
                        {g.connection}
                      </span>
                      <span className="flex items-center gap-1.5 shrink-0">
                        <span className="text-[10px] text-[var(--text-tertiary)]">{g.models.length}</span>
                        {!isFiltering && (
                          <svg
                            className={`w-3 h-3 text-[var(--text-tertiary)] transition-transform ${isExpanded ? 'rotate-180' : ''}`}
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                          >
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                          </svg>
                        )}
                      </span>
                    </button>
                    {isExpanded &&
                      g.models.map((m) => {
                        const isActive = m.id === activeId;
                        const isSelected = m.id === selectedId;
                        return (
                          <button
                            key={m.id}
                            data-model-id={m.id}
                            type="button"
                            onClick={() => handleSelect(m.id)}
                            onMouseEnter={() => setActiveId(m.id)}
                            className={`w-full flex items-center justify-between gap-2 pl-5 pr-3 py-1.5 text-sm font-mono truncate transition-colors ${
                              isActive
                                ? 'bg-[var(--accent-muted)] text-[var(--text-primary)]'
                                : isSelected
                                  ? 'text-[var(--text-primary)]'
                                  : 'text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]'
                            }`}
                          >
                            <span className="truncate">{m.model || m.label}</span>
                            {isSelected && (
                              <svg
                                className="shrink-0 w-3.5 h-3.5 text-[var(--accent)]"
                                fill="none"
                                stroke="currentColor"
                                viewBox="0 0 24 24"
                              >
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                              </svg>
                            )}
                          </button>
                        );
                      })}
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
