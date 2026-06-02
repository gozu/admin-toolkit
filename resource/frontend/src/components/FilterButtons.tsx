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
              ? 'bg-[#00b5aa] text-white'
              : 'bg-white text-gray-700 hover:bg-gray-100 border border-gray-200'
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
                ? 'bg-[#00b5aa] text-white'
                : 'bg-white text-gray-700 hover:bg-gray-100 border border-gray-200'
            }
          `}
        >
          {filter.label}
        </button>
      ))}
    </div>
  );
}
