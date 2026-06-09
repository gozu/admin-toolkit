import type { ButtonHTMLAttributes } from 'react';

type ButtonVariant = 'danger' | 'ghost' | 'modalCancel' | 'modalDanger';

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  danger:
    'px-3 py-1 rounded-md text-xs font-medium border border-[var(--neon-red)]/30 bg-[var(--neon-red)]/10 text-[var(--neon-red)] hover:bg-[var(--neon-red)]/20 hover:border-[var(--neon-red)]/50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed',
  ghost:
    'px-3 py-1 rounded-md text-xs font-medium text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] transition-colors',
  modalCancel:
    'px-3 py-1.5 rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-[var(--text-secondary)]',
  modalDanger:
    'px-4 py-1.5 rounded bg-[var(--neon-red)]/20 text-[var(--neon-red)] hover:bg-[var(--neon-red)]/30 disabled:opacity-50 transition-colors',
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant: ButtonVariant;
}

export function Button({ variant, className, ...rest }: ButtonProps) {
  const base = VARIANT_CLASSES[variant];
  return <button className={className ? `${base} ${className}` : base} {...rest} />;
}
