import type { ReactNode } from 'react';

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
  return (
    <div className="text-center">
      <div className={`text-2xl font-mono ${valueClassName}`}>{value}</div>
      <div className="text-xs text-[var(--text-muted)]">{label}</div>
    </div>
  );
}
