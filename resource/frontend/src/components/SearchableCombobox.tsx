import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

interface SearchableComboboxProps {
  value: string;
  onChange: (value: string) => void;
  options: string[];
  placeholder?: string;
  className?: string;
  onEnterWithClosed?: () => void;
}

export function SearchableCombobox({
  value,
  onChange,
  options,
  placeholder,
  className,
  onEnterWithClosed,
}: SearchableComboboxProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [rawSelectedIndex, setSelectedIndex] = useState(0);
  const [isTyping, setIsTyping] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => {
    if (!isTyping || !value.trim()) return options;
    const q = value.toLowerCase();
    return options.filter((opt) => opt.toLowerCase().includes(q));
  }, [value, options, isTyping]);

  // Derive clamped index from raw index + results length (resets when list shrinks)
  const selectedIndex = Math.min(rawSelectedIndex, Math.max(0, filtered.length - 1));

  // Scroll selected item into view
  useEffect(() => {
    if (!listRef.current || !isOpen) return;
    const items = listRef.current.querySelectorAll('[data-combobox-item]');
    const target = items[selectedIndex];
    if (target) {
      target.scrollIntoView({ block: 'nearest' });
    }
  }, [selectedIndex, isOpen]);

  // Close on click outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
        setIsTyping(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const handleSelect = useCallback(
    (opt: string) => {
      onChange(opt);
      setIsOpen(false);
      setIsTyping(false);
      inputRef.current?.focus();
    },
    [onChange],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      switch (e.key) {
        case 'ArrowDown': {
          e.preventDefault();
          if (!isOpen) {
            setIsOpen(true);
          } else {
            setSelectedIndex((prev) => (prev + 1) % Math.max(1, filtered.length));
          }
          break;
        }
        case 'ArrowUp': {
          e.preventDefault();
          if (isOpen) {
            setSelectedIndex(
              (prev) => (prev - 1 + filtered.length) % Math.max(1, filtered.length),
            );
          }
          break;
        }
        case 'Enter': {
          e.preventDefault();
          if (isOpen && filtered[selectedIndex]) {
            handleSelect(filtered[selectedIndex]);
          } else if (!isOpen) {
            onEnterWithClosed?.();
          }
          break;
        }
        case 'Escape': {
          if (isOpen) {
            e.preventDefault();
            e.stopPropagation();
            setIsOpen(false);
            setIsTyping(false);
          }
          break;
        }
      }
    },
    [isOpen, filtered, selectedIndex, handleSelect, onEnterWithClosed],
  );

  return (
    <div ref={containerRef} className="relative">
      <input
        ref={inputRef}
        type="text"
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setSelectedIndex(0);
          setIsTyping(true);
          if (!isOpen) setIsOpen(true);
        }}
        onFocus={() => {
          setIsTyping(false);
          if (options.length > 0) setIsOpen(true);
        }}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        className={className}
        style={{ paddingRight: '2rem' }}
        autoComplete="off"
        spellCheck={false}
      />
      <svg
        className={`pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--text-tertiary)] transition-transform ${isOpen ? 'rotate-180' : ''}`}
        fill="none" stroke="currentColor" viewBox="0 0 24 24"
      >
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
      </svg>
      {isOpen && filtered.length > 0 && (
        <div
          ref={listRef}
          className="absolute z-50 left-0 right-0 top-full mt-1 max-h-[200px] overflow-y-auto rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] shadow-lg dropdown-enter"
        >
          {filtered.map((opt, index) => {
            const isSelected = index === selectedIndex;
            return (
              <button
                key={opt}
                data-combobox-item
                type="button"
                onClick={() => handleSelect(opt)}
                onMouseEnter={() => setSelectedIndex(index)}
                className={`w-full text-left px-3 py-1.5 text-sm font-mono truncate transition-colors ${
                  isSelected
                    ? 'bg-[var(--accent-muted)] text-[var(--text-primary)]'
                    : 'text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]'
                }`}
              >
                {opt}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
