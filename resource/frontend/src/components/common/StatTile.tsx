import type { ReactNode } from 'react';
import { RollingNumber } from './RollingNumber';

interface StatTileProps {
  value: ReactNode;
  label: string;
  valueClassName?: string;
}

export function StatTile({
  value,
  label,
  valueClassName = 'text-[var(--text-primary)]',
}: StatTileProps) {
  const rollable = typeof value === 'string' || typeof value === 'number';
  return (
    <div className="text-center">
      <div className={`text-2xl font-mono ${valueClassName}`}>
        {rollable ? <RollingNumber value={value} /> : value}
      </div>
      <div className="text-xs text-[var(--text-muted)]">{label}</div>
    </div>
  );
}
