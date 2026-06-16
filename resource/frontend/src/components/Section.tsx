import type { ReactNode } from 'react';
import { useCollapsible } from '../hooks/useCollapsible';

interface SectionProps {
  id: string;
  title: string;
  icon?: ReactNode;
  variant?: 'default' | 'alert';
  collapsible?: boolean;
  defaultOpen?: boolean;
  itemCount?: number;
  children: ReactNode;
}

export function Section({
  id,
  title,
  icon,
  variant = 'default',
  collapsible = false,
  defaultOpen = true,
  itemCount,
  children,
}: SectionProps) {
  const { isOpen, toggle } = useCollapsible({
    id: `section-${id}`,
    defaultOpen,
    persist: collapsible,
  });

  const headerColorClass = variant === 'alert' ? 'text-[var(--neon-red)]' : 'text-[var(--text-primary)]';
  const shouldShow = !collapsible || isOpen;

  return (
    <section className="mb-6" id={id}>
      <div
        className={`flex items-center gap-2 mb-4 ${collapsible ? 'cursor-pointer select-none' : ''}`}
        onClick={collapsible ? toggle : undefined}
        role={collapsible ? 'button' : undefined}
        tabIndex={collapsible ? 0 : undefined}
        onKeyDown={
          collapsible
            ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  toggle();
                }
              }
            : undefined
        }
        aria-expanded={collapsible ? isOpen : undefined}
        aria-controls={collapsible ? `${id}-content` : undefined}
      >
        {icon && <span className={`${headerColorClass}`}>{icon}</span>}
        <h2 className={`text-lg font-semibold ${headerColorClass}`}>{title}</h2>
        {itemCount !== undefined && (
          <span className="px-2 py-0.5 bg-[var(--bg-hover)] text-[var(--text-secondary)] text-xs font-medium rounded-full">
            {itemCount}
          </span>
        )}
        {collapsible && (
          <svg
            className={`w-5 h-5 text-[var(--text-tertiary)] transition-transform duration-200 ${
              isOpen ? '' : '-rotate-90'
            }`}
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
          </svg>
        )}
      </div>

      {collapsible ? (
        <div
          id={`${id}-content`}
          className="collapse-content"
          data-state={isOpen ? 'open' : 'closed'}
        >
          <div>{children}</div>
        </div>
      ) : (
        shouldShow && <div>{children}</div>
      )}
    </section>
  );
}
