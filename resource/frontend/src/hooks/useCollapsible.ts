import { useState, useCallback, useEffect } from 'react';

const STORAGE_KEY_PREFIX = 'admin-toolkit-collapsed-';

interface UseCollapsibleOptions {
  id: string;
  defaultOpen?: boolean;
  persist?: boolean;
}

interface UseCollapsibleReturn {
  isOpen: boolean;
  toggle: () => void;
  open: () => void;
  close: () => void;
}

/**
 * Hook to manage collapsible state with optional localStorage persistence
 */
export function useCollapsible({
  id,
  defaultOpen = true,
  persist = true,
}: UseCollapsibleOptions): UseCollapsibleReturn {
  const storageKey = `${STORAGE_KEY_PREFIX}${id}`;

  const [isOpen, setIsOpen] = useState(() => {
    if (persist && typeof window !== 'undefined') {
      const stored = localStorage.getItem(storageKey);
      if (stored !== null) {
        return stored === 'true';
      }
    }
    return defaultOpen;
  });

  // Persist state to localStorage
  useEffect(() => {
    if (persist && typeof window !== 'undefined') {
      localStorage.setItem(storageKey, String(isOpen));
    }
  }, [isOpen, persist, storageKey]);

  const toggle = useCallback(() => {
    setIsOpen((prev) => !prev);
  }, []);

  const open = useCallback(() => {
    setIsOpen(true);
  }, []);

  const close = useCallback(() => {
    setIsOpen(false);
  }, []);

  return { isOpen, toggle, open, close };
}
