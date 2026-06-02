import { useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence, LayoutGroup } from 'framer-motion';
import { useTableFilter } from '../hooks/useTableFilter';

export function FilterBar() {
  const {
    filterGroups,
    filterCounts,
    activeFilter,
    setActiveFilter,
    getFilterLabel,
    searchQuery,
    setSearchQuery,
  } = useTableFilter();

  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setDropdownOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Close dropdown on escape key
  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setDropdownOpen(false);
      }
    };

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, []);

  const issueCount = filterCounts.disabledFeatures;

  return (
    <div className="flex flex-wrap items-center gap-3 py-3 border-b border-[var(--border-glass)] mb-6">
      {/* View Dropdown */}
      <div className="relative" ref={dropdownRef}>
        <button
          onClick={() => setDropdownOpen(!dropdownOpen)}
          className="flex items-center gap-2 px-3 py-1.5 bg-[var(--bg-glass)] border border-[var(--border-glass)]
                     rounded-lg text-sm font-medium text-[var(--text-secondary)]
                     hover:border-[var(--border-glow)] hover:text-[var(--text-primary)]
                     transition-all duration-150 ease-out"
          aria-haspopup="listbox"
          aria-expanded={dropdownOpen}
        >
          <span className="text-[var(--text-muted)]">View:</span>
          <span className="text-[var(--neon-cyan)]">{getFilterLabel(activeFilter)}</span>
          <motion.svg
            animate={{ rotate: dropdownOpen ? 180 : 0 }}
            transition={{ duration: 0.15 }}
            className="w-4 h-4 text-[var(--text-muted)]"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M19 9l-7 7-7-7"
            />
          </motion.svg>
        </button>

        <AnimatePresence>
          {dropdownOpen && (
            <motion.div
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.15 }}
              className="absolute top-full left-0 mt-1 w-64 bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-xl shadow-lg
                         py-1 z-50 max-h-96 overflow-y-auto"
              role="listbox"
            >
              {/* All option */}
              <button
                onClick={() => {
                  setActiveFilter('all');
                  setDropdownOpen(false);
                }}
                className={`w-full px-4 py-2 text-left text-sm hover:bg-[var(--bg-glass-hover)] flex items-center justify-between
                           transition-colors duration-150
                           ${activeFilter === 'all' ? 'bg-[var(--neon-cyan)]/10 text-[var(--neon-cyan)]' : 'text-[var(--text-secondary)]'}`}
                role="option"
                aria-selected={activeFilter === 'all'}
              >
                <span>All</span>
                {activeFilter === 'all' && (
                  <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                    <path
                      fillRule="evenodd"
                      d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                      clipRule="evenodd"
                    />
                  </svg>
                )}
              </button>

              <div className="border-t border-[var(--border-glass)] my-1" />

              {/* Filter groups */}
              {filterGroups.map((group, index) => (
                <div key={group.id}>
                  {index > 0 && <div className="border-t border-[var(--border-glass)] my-1" />}

                  {/* Group header - clickable to filter entire group */}
                  <button
                    onClick={() => {
                      setActiveFilter(group.id);
                      setDropdownOpen(false);
                    }}
                    className={`w-full px-4 py-2 text-left text-sm hover:bg-[var(--bg-glass-hover)] flex items-center justify-between
                               transition-colors duration-150
                               ${activeFilter === group.id ? 'bg-[var(--neon-cyan)]/10 text-[var(--neon-cyan)]' : 'text-[var(--text-primary)] font-medium'}`}
                    role="option"
                    aria-selected={activeFilter === group.id}
                  >
                    <span>{group.label}</span>
                    <span className="text-xs text-[var(--text-muted)] bg-[var(--bg-glass)] px-1.5 py-0.5 rounded font-mono">
                      {group.items.length}
                    </span>
                  </button>

                  {/* Group items */}
                  {group.items.map((item) => (
                    <button
                      key={item.id}
                      onClick={() => {
                        setActiveFilter(item.id);
                        setDropdownOpen(false);
                      }}
                      className={`w-full px-4 py-1.5 pl-8 text-left text-sm hover:bg-[var(--bg-glass-hover)] flex items-center justify-between
                                 transition-colors duration-150
                                 ${activeFilter === item.id ? 'bg-[var(--neon-cyan)]/10 text-[var(--neon-cyan)]' : 'text-[var(--text-secondary)]'}`}
                      role="option"
                      aria-selected={activeFilter === item.id}
                    >
                      <span>{item.label}</span>
                      {activeFilter === item.id && (
                        <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                          <path
                            fillRule="evenodd"
                            d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                            clipRule="evenodd"
                          />
                        </svg>
                      )}
                    </button>
                  ))}
                </div>
              ))}
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Quick Filters */}
      <LayoutGroup>
      <div className="flex items-center gap-2">
        <span className="text-sm text-[var(--text-muted)] hidden sm:inline">Quick:</span>

        {/* Issues Quick Filter */}
        <button
          onClick={() => setActiveFilter(activeFilter === 'issues' ? 'all' : 'issues')}
          className={`relative flex items-center gap-1.5 px-2.5 py-1 rounded-full text-sm font-medium
                     transition-all duration-150 ease-out ${
                       activeFilter === 'issues'
                         ? 'text-[var(--neon-red)] border border-transparent'
                         : 'bg-[var(--bg-glass)] text-[var(--text-secondary)] border border-[var(--border-glass)] hover:border-[var(--border-glow)] hover:text-[var(--text-primary)]'
                     }`}
        >
          {activeFilter === 'issues' && (
            <motion.div
              layoutId="activeFilter"
              className="absolute inset-0 rounded-full bg-[var(--neon-red)]/15 border border-[var(--neon-red)]/30"
              transition={{ type: 'spring', stiffness: 380, damping: 30 }}
            />
          )}
          <svg className="relative w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
            />
          </svg>
          <span className="relative">Issues</span>
          {issueCount > 0 && (
            <span className={`relative px-1.5 py-0.5 text-xs rounded-full font-mono font-semibold ${
              activeFilter === 'issues'
                ? 'bg-[var(--neon-red)]/20 text-[var(--neon-red)]'
                : 'bg-[var(--neon-red)]/15 text-[var(--neon-red)]'
            }`}>
              {issueCount}
            </span>
          )}
        </button>

        {/* Charts Quick Filter */}
        <button
          onClick={() => setActiveFilter(activeFilter === 'charts' ? 'all' : 'charts')}
          className={`relative flex items-center gap-1.5 px-2.5 py-1 rounded-full text-sm font-medium
                     transition-all duration-150 ease-out ${
                       activeFilter === 'charts'
                         ? 'text-[var(--neon-cyan)] border border-transparent'
                         : 'bg-[var(--bg-glass)] text-[var(--text-secondary)] border border-[var(--border-glass)] hover:border-[var(--border-glow)] hover:text-[var(--text-primary)]'
                     }`}
        >
          {activeFilter === 'charts' && (
            <motion.div
              layoutId="activeFilter"
              className="absolute inset-0 rounded-full bg-[var(--neon-cyan)]/15 border border-[var(--neon-cyan)]/30 shadow-[var(--glow-sm)]"
              transition={{ type: 'spring', stiffness: 380, damping: 30 }}
            />
          )}
          <svg className="relative w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
            />
          </svg>
          <span className="relative">Charts</span>
        </button>

        {/* Config Quick Filter */}
        <button
          onClick={() => setActiveFilter(activeFilter === 'config' ? 'all' : 'config')}
          className={`relative flex items-center gap-1.5 px-2.5 py-1 rounded-full text-sm font-medium
                     transition-all duration-150 ease-out ${
                       activeFilter === 'config'
                         ? 'text-[var(--neon-cyan)] border border-transparent'
                         : 'bg-[var(--bg-glass)] text-[var(--text-secondary)] border border-[var(--border-glass)] hover:border-[var(--border-glow)] hover:text-[var(--text-primary)]'
                     }`}
        >
          {activeFilter === 'config' && (
            <motion.div
              layoutId="activeFilter"
              className="absolute inset-0 rounded-full bg-[var(--neon-cyan)]/15 border border-[var(--neon-cyan)]/30 shadow-[var(--glow-sm)]"
              transition={{ type: 'spring', stiffness: 380, damping: 30 }}
            />
          )}
          <svg className="relative w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"
            />
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
            />
          </svg>
          <span className="relative">Settings</span>
        </button>
      </div>
      </LayoutGroup>

      {/* Search */}
      <div className="flex-1 max-w-xs ml-auto">
        <div className="relative">
          <svg
            className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--text-muted)]"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
            />
          </svg>
          <input
            type="text"
            placeholder="Search cards..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="input-glass w-full"
            style={{ paddingLeft: '2.25rem', paddingRight: '2rem' }}
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 p-1 hover:bg-[var(--bg-glass)] rounded
                         transition-colors duration-150"
            >
              <svg className="w-4 h-4 text-[var(--text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
