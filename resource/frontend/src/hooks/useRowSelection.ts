import { useCallback, useState } from 'react';

interface UseRowSelectionReturn {
  selectedKeys: Set<string>;
  setSelectedKeys: React.Dispatch<React.SetStateAction<Set<string>>>;
  toggleSelect: (key: string) => void;
  toggleSelectAll: (allKeys: string[]) => void;
  clear: () => void;
}

// Set-based row selection. toggleSelectAll clears when every key is already
// selected, otherwise selects exactly the given keys.
export function useRowSelection(): UseRowSelectionReturn {
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());

  const toggleSelect = useCallback((key: string) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback((allKeys: string[]) => {
    setSelectedKeys((prev) => {
      const allSelected = allKeys.length > 0 && allKeys.every((k) => prev.has(k));
      return allSelected ? new Set<string>() : new Set(allKeys);
    });
  }, []);

  const clear = useCallback(() => setSelectedKeys(new Set()), []);

  return { selectedKeys, setSelectedKeys, toggleSelect, toggleSelectAll, clear };
}
