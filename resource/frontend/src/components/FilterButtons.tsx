import { useDiag } from '../context/DiagContext';

interface FilterOption {
  id: string;
  label: string;
}

interface FilterButtonsProps {
  filters: FilterOption[];
}

export function FilterButtons({ filters }: FilterButtonsProps) {
  const { state, setActiveFilter } = useDiag();
  const { activeFilter } = state;

  const handleFilterClick = (filterId: string) => {
    // If clicking the active filter (and it's not 'all'), toggle back to 'all'
    const newFilter = filterId === activeFilter && filterId !== 'all' ? 'all' : filterId;
    setActiveFilter(newFilter);
  };

  return (
    <div className="flex flex-wrap gap-2 mb-6">
      <button
        onClick={() => handleFilterClick('all')}
        className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors
          ${
            activeFilter === 'all'
              ? 'bg-[var(--accent)] text-white'
              : 'bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] border border-[var(--border-default)]'
          }
        `}
      >
        All
      </button>
      {filters.map((filter) => (
        <button
          key={filter.id}
          onClick={() => handleFilterClick(filter.id)}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors
            ${
              activeFilter === filter.id
                ? 'bg-[var(--accent)] text-white'
                : 'bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] border border-[var(--border-default)]'
            }
          `}
        >
          {filter.label}
        </button>
      ))}
    </div>
  );
}
