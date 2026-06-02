import { useState, useCallback } from 'react';

type SortDir = 'asc' | 'desc';

interface UseTableSortOptions<K extends string> {
  defaultKey: K;
  defaultDir?: SortDir;
  ascDefaultKeys?: K[];
}

interface UseTableSortReturn<K extends string> {
  sortKey: K;
  sortDir: SortDir;
  handleSort: (key: K) => void;
  sortIndicator: (key: K) => string;
}

export function useTableSort<K extends string>(
  opts: UseTableSortOptions<K>,
): UseTableSortReturn<K> {
  const { defaultKey, defaultDir = 'desc', ascDefaultKeys = [] } = opts;
  const [sortKey, setSortKey] = useState<K>(defaultKey);
  const [sortDir, setSortDir] = useState<SortDir>(defaultDir);

  const handleSort = useCallback(
    (key: K) => {
      if (sortKey === key) {
        setSortDir((prev) => (prev === 'asc' ? 'desc' : 'asc'));
      } else {
        setSortKey(key);
        setSortDir(ascDefaultKeys.includes(key) ? 'asc' : 'desc');
      }
    },
    [sortKey, ascDefaultKeys],
  );

  const sortIndicator = useCallback(
    (key: K): string => {
      if (sortKey !== key) return '';
      return sortDir === 'asc' ? ' \u25B2' : ' \u25BC';
    },
    [sortKey, sortDir],
  );

  return { sortKey, sortDir, handleSort, sortIndicator };
}
