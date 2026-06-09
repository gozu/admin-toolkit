interface SpinnerProps {
  size?: string;
  color?: string;
}

export function Spinner({
  size = 'w-4 h-4',
  color = 'border-[var(--text-tertiary)]',
}: SpinnerProps) {
  return (
    <span
      className={`inline-block ${size} border-2 ${color} border-t-transparent rounded-full animate-spin`}
    />
  );
}
