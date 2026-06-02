import type { ReactNode } from 'react';
import { motion } from 'framer-motion';
import { useTableFilter } from '../hooks/useTableFilter';
import { useCollapsible } from '../hooks/useCollapsible';

type CardVariant = 'default' | 'elevated' | 'inset' | 'critical' | 'warning' | 'compact' | 'hero';
type CardSize = '1x1' | '2x1' | '2x2' | '3x1' | '4x1';

interface CardProps {
  id: string;
  title?: string;
  children: ReactNode;
  className?: string;
  wide?: boolean;
  variant?: CardVariant;
  size?: CardSize;
  collapsible?: boolean;
  defaultOpen?: boolean;
  itemCount?: number;
  animationDelay?: number;
}

const variantStyles: Record<CardVariant, string> = {
  default: 'glass-card card-hover',
  elevated: 'glass-card-elevated card-hover',
  inset: 'bg-[var(--bg-surface)] rounded-xl border border-[var(--border-glass)]',
  critical: 'card-alert-critical rounded-xl card-hover',
  warning: 'card-alert-warning rounded-xl card-hover',
  compact: 'glass-card',
  hero: 'glass-card border-[var(--border-glow)] shadow-[var(--glow-sm)]',
};

const headerStyles: Record<CardVariant, string> = {
  default: 'border-b border-[var(--border-glass)]',
  elevated: 'border-b border-[var(--border-glass)]',
  inset: 'border-b border-[var(--border-glass)]',
  critical: 'border-b border-[var(--neon-red)]/20',
  warning: 'border-b border-[var(--neon-amber)]/20',
  compact: 'border-b border-[var(--border-glass)]',
  hero: 'border-b border-[var(--border-glow)]',
};

const titleStyles: Record<CardVariant, string> = {
  default: 'text-[var(--text-primary)]',
  elevated: 'text-[var(--text-primary)]',
  inset: 'text-[var(--text-secondary)]',
  critical: 'text-[var(--neon-red)]',
  warning: 'text-[var(--neon-amber)]',
  compact: 'text-[var(--text-primary)] text-base',
  hero: 'text-neon-subtle',
};

const countBadgeStyles: Record<CardVariant, string> = {
  default: 'badge-neutral',
  elevated: 'badge-neutral',
  inset: 'badge-neutral',
  critical: 'badge-critical',
  warning: 'badge-warning',
  compact: 'badge-neutral',
  hero: 'badge-info',
};

const sizeClasses: Record<CardSize, string> = {
  '1x1': 'card-1x1',
  '2x1': 'card-2x1',
  '2x2': 'card-2x2',
  '3x1': 'card-3x1',
  '4x1': 'card-4x1',
};

export function Card({
  id,
  title,
  children,
  className = '',
  wide = false,
  variant = 'default',
  size,
  collapsible = false,
  defaultOpen = true,
  itemCount,
  animationDelay = 0,
}: CardProps) {
  const { isVisible } = useTableFilter();
  const { isOpen, toggle } = useCollapsible({
    id: `card-${id}`,
    defaultOpen,
    persist: collapsible,
  });

  if (!isVisible(id)) {
    return null;
  }

  const sizeClass = size ? sizeClasses[size] : (wide ? 'card-2x1' : '');

  const cardClasses = `
    ${variantStyles[variant]}
    ${sizeClass}
    ${className}
    overflow-hidden
  `;

  const paddingClass = variant === 'compact' ? 'px-3 py-2' : 'px-4 py-3';
  const titleClass = variant === 'compact' ? 'text-base' : 'text-lg';

  return (
    <motion.div
      id={id}
      className={cardClasses}
      initial={{ opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '-50px' }}
      transition={{
        duration: 0.5,
        delay: animationDelay * 0.05,
        ease: [0.16, 1, 0.3, 1],
      }}
    >
      {title && (
        <div
          className={`${paddingClass} ${headerStyles[variant]} ${
            collapsible
              ? 'cursor-pointer select-none hover:bg-[var(--bg-glass-hover)] transition-colors duration-150'
              : ''
          }`}
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
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <h4 className={`${titleClass} font-semibold ${titleStyles[variant]}`}>
                {title}
              </h4>
              {itemCount !== undefined && (
                <span className={`px-2 py-0.5 text-xs font-mono font-medium rounded-full ${countBadgeStyles[variant]}`}>
                  {itemCount}
                </span>
              )}
            </div>
            {collapsible && (
              <motion.svg
                animate={{ rotate: isOpen ? 0 : -90 }}
                transition={{ duration: 0.2 }}
                className="w-5 h-5 text-[var(--text-muted)]"
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
            )}
          </div>
        </div>
      )}

      {collapsible ? (
        <div
          id={`${id}-content`}
          className="collapse-content"
          data-state={isOpen ? 'open' : 'closed'}
        >
          <div>{children}</div>
        </div>
      ) : (
        children
      )}
    </motion.div>
  );
}
