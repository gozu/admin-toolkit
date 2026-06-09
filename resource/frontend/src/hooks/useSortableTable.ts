import { useCallback, useState } from 'react';

type SortDir = 'asc' | 'desc';

interface UseSortableTableReturn<F extends string> {
  sortField: F | null;
  sortDir: SortDir;
  toggleSort: (field: F) => void;
  sortIndicator: (field: F) => string;
}

// Sortable-table state for tables whose default order is domain-specific
// (sortField starts null; comparators stay in the component). Clicking a new
// column sorts ascending; clicking the active column flips direction.
export function useSortableTable<F extends string>(): UseSortableTableReturn<F> {
  const [sortField, setSortField] = useState<F | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('asc');

  const toggleSort = useCallback(
    (field: F) => {
      if (sortField === field) {
        setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
      } else {
        setSortField(field);
        setSortDir('asc');
      }
    },
    [sortField],
  );

  const sortIndicator = useCallback(
    (field: F) => {
      if (sortField !== field) return '';
      return sortDir === 'asc' ? ' ▲' : ' ▼';
    },
    [sortField, sortDir],
  );

  return { sortField, sortDir, toggleSort, sortIndicator };
}
