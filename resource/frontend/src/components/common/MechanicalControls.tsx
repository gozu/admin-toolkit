import { motion, useReducedMotion } from 'framer-motion';
import { useId } from 'react';

const SPRING = { type: 'spring' as const, stiffness: 520, damping: 30, mass: 0.65 };

export function SwitchThumb({ checked }: { checked: boolean }) {
  const reduced = useReducedMotion();
  return (
    <motion.span
      aria-hidden
      className="absolute left-[2px] top-[2px] h-2.5 w-2.5 rounded-full bg-current"
      initial={false}
      animate={{ x: checked ? 11 : 0 }}
      transition={reduced ? { duration: 0 } : SPRING}
    />
  );
}

export function MechanicalChevron({
  expanded,
  className = '',
}: {
  expanded: boolean;
  className?: string;
}) {
  const reduced = useReducedMotion();
  return (
    <motion.svg
      aria-hidden
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`shrink-0 ${className}`}
      initial={false}
      animate={{ rotate: expanded ? 90 : 0 }}
      transition={reduced ? { duration: 0 } : { ...SPRING, stiffness: 650, damping: 32 }}
    >
      <path d="m6 3 5 5-5 5" />
    </motion.svg>
  );
}

export function SegmentedControl<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  const reduced = useReducedMotion();
  const id = useId();
  return (
    <div role="radiogroup" aria-label={label} className="mechanical-segments">
      {options.map((option, index) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={value === option.value}
          tabIndex={value === option.value ? 0 : -1}
          onClick={() => onChange(option.value)}
          onKeyDown={(event) => {
            const delta =
              event.key === 'ArrowRight' || event.key === 'ArrowDown'
                ? 1
                : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
                  ? -1
                  : 0;
            if (!delta && event.key !== 'Home' && event.key !== 'End') return;
            event.preventDefault();
            const next =
              event.key === 'Home'
                ? 0
                : event.key === 'End'
                  ? options.length - 1
                  : (index + delta + options.length) % options.length;
            onChange(options[next].value);
            (event.currentTarget.parentElement?.children[next] as HTMLButtonElement)?.focus();
          }}
        >
          {value === option.value && (
            <motion.span
              aria-hidden
              className="mechanical-segment-seat"
              layoutId={id}
              initial={false}
              transition={reduced ? { duration: 0 } : SPRING}
            />
          )}
          <span className="relative">{option.label}</span>
        </button>
      ))}
    </div>
  );
}
