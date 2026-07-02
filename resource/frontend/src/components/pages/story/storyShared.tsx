/** Shared presentational bits for the Story pages. Non-component helpers
 *  (slotLifecycle, button classes) live in storyUtils.ts. */
import type { ReactNode } from 'react';

export function StatusPill({ ok, okLabel, badLabel }: {
  ok: boolean;
  okLabel: string;
  badLabel: string;
}) {
  return (
    <span
      className={`px-2 py-0.5 text-xs font-medium rounded border ${
        ok
          ? 'bg-[var(--success)]/15 text-[var(--success)] border-[var(--success)]/40'
          : 'bg-[var(--neon-red)]/15 text-[var(--neon-red)] border-[var(--neon-red)]/40'
      }`}
    >
      {ok ? okLabel : badLabel}
    </span>
  );
}

export function StoryCard({ title, subtitle, children }: {
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="glass-card p-4 space-y-3">
      <div>
        <h3 className="text-lg font-semibold text-[var(--text-primary)]">{title}</h3>
        {subtitle && <p className="text-sm text-[var(--text-muted)]">{subtitle}</p>}
      </div>
      {children}
    </section>
  );
}

export function StoryNotConfiguredNotice({ error }: { error?: string | null }) {
  return (
    <div className="glass-card p-6 text-center space-y-2">
      <h3 className="text-lg font-semibold text-[var(--text-primary)]">Story is not configured</h3>
      <p className="text-sm text-[var(--text-muted)]">
        Select a PostgreSQL connection in Plugin settings → “Story (experimental)”, then open
        Story → Setup and press Provision.
      </p>
      {error && <p className="text-xs text-[var(--neon-red)]">{error}</p>}
    </div>
  );
}

/** windowDays selector shared by the data pages. */
export function WindowSelect({ value, options, onChange }: {
  value: number;
  options: number[];
  onChange: (days: number) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
      Window
      <select
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="input-glass text-sm"
      >
        {options.map((d) => (
          <option key={d} value={d}>{d} days</option>
        ))}
      </select>
    </label>
  );
}
