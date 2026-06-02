import type { ChangeType, DeltaDirection } from '../../types';

interface DeltaBadgeProps {
  changeType: ChangeType;
  direction?: DeltaDirection;
  size?: 'sm' | 'md';
}

const changeTypeConfig: Record<ChangeType, { label: string; baseClass: string }> = {
  added: {
    label: 'Added',
    baseClass: 'bg-[var(--status-success-bg)] text-[var(--neon-green)] border-[var(--status-success-border)]',
  },
  removed: {
    label: 'Removed',
    baseClass: 'bg-[var(--status-critical-bg)] text-[var(--neon-red)] border-[var(--status-critical-border)]',
  },
  modified: {
    label: 'Changed',
    baseClass: 'bg-[var(--status-warning-bg)] text-[var(--neon-amber)] border-[var(--status-warning-border)]',
  },
  unchanged: {
    label: 'Unchanged',
    baseClass: 'bg-[var(--bg-glass)] text-[var(--text-muted)] border-[var(--border-glass)]',
  },
};

export function DeltaBadge({ changeType, direction, size = 'sm' }: DeltaBadgeProps) {
  const config = changeTypeConfig[changeType];
  const sizeClass = size === 'sm' ? 'px-1.5 py-0.5 text-xs' : 'px-2 py-1 text-sm';

  // For modified, adjust color based on direction
  let finalClass = config.baseClass;
  if (changeType === 'modified' && direction) {
    if (direction === 'improvement') {
      finalClass = 'bg-[var(--status-success-bg)] text-[var(--neon-green)] border-[var(--status-success-border)]';
    } else if (direction === 'regression') {
      finalClass = 'bg-[var(--status-critical-bg)] text-[var(--neon-red)] border-[var(--status-critical-border)]';
    }
  }

  return (
    <span className={`inline-flex items-center gap-1 ${sizeClass} rounded border font-medium ${finalClass}`}>
      {changeType === 'added' && (
        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
        </svg>
      )}
      {changeType === 'removed' && (
        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" />
        </svg>
      )}
      {changeType === 'modified' && direction === 'improvement' && (
        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 10l7-7m0 0l7 7m-7-7v18" />
        </svg>
      )}
      {changeType === 'modified' && direction === 'regression' && (
        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
        </svg>
      )}
      {changeType === 'modified' && direction === 'neutral' && (
        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7h12M8 12h12m-12 5h12" />
        </svg>
      )}
      {config.label}
    </span>
  );
}

interface DirectionBadgeProps {
  direction: DeltaDirection;
  delta?: number;
  showDelta?: boolean;
  size?: 'sm' | 'md';
}

const directionConfig: Record<DeltaDirection, { label: string; icon: React.ReactNode; baseClass: string }> = {
  improvement: {
    label: 'Improved',
    icon: (
      <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 10l7-7m0 0l7 7m-7-7v18" />
      </svg>
    ),
    baseClass: 'bg-[var(--status-success-bg)] text-[var(--neon-green)] border-[var(--status-success-border)]',
  },
  regression: {
    label: 'Regression',
    icon: (
      <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
      </svg>
    ),
    baseClass: 'bg-[var(--status-critical-bg)] text-[var(--neon-red)] border-[var(--status-critical-border)]',
  },
  neutral: {
    label: 'Neutral',
    icon: (
      <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" />
      </svg>
    ),
    baseClass: 'bg-[var(--status-info-bg)] text-[var(--neon-cyan)] border-[var(--status-info-border)]',
  },
};

export function DirectionBadge({ direction, delta, showDelta = false, size = 'sm' }: DirectionBadgeProps) {
  const config = directionConfig[direction];
  const sizeClass = size === 'sm' ? 'px-1.5 py-0.5 text-xs' : 'px-2 py-1 text-sm';

  const displayText = showDelta && delta !== undefined
    ? `${delta > 0 ? '+' : ''}${delta}`
    : config.label;

  return (
    <span className={`inline-flex items-center gap-1 ${sizeClass} rounded border font-medium ${config.baseClass}`}>
      {config.icon}
      {displayText}
    </span>
  );
}

interface CountBadgeProps {
  count: number;
  type: 'added' | 'removed' | 'modified';
  size?: 'sm' | 'md';
}

export function CountBadge({ count, type, size = 'sm' }: CountBadgeProps) {
  if (count === 0) return null;

  const config = changeTypeConfig[type];
  const sizeClass = size === 'sm' ? 'px-1.5 py-0.5 text-xs' : 'px-2 py-1 text-sm';

  const prefix = type === 'added' ? '+' : type === 'removed' ? '-' : '~';

  return (
    <span className={`inline-flex items-center gap-1 ${sizeClass} rounded border font-mono font-medium ${config.baseClass}`}>
      {prefix}{count}
    </span>
  );
}
