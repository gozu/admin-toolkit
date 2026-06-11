import { motion, useReducedMotion } from 'framer-motion';
import { useEffect, useRef } from 'react';

// Slot-machine digits: each digit is a vertical 0-9 column that springs to the
// current value, so stats roll up on mount and tick smoothly on change.
// Non-digit characters (separators, units) render statically in place.

const DIGITS = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9'];

interface RollingNumberProps {
  value: string | number;
  className?: string;
}

function DigitColumn({ digit, instant }: { digit: number; instant: boolean }) {
  return (
    <span
      className="inline-block overflow-hidden align-baseline"
      style={{ height: '1em', lineHeight: '1em' }}
    >
      <motion.span
        className="block"
        initial={false}
        animate={{ y: `${-digit}em` }}
        transition={
          instant
            ? { duration: 0 }
            : { type: 'spring', stiffness: 120, damping: 16, mass: 0.9 }
        }
      >
        {DIGITS.map((d) => (
          <span key={d} className="block" style={{ height: '1em', lineHeight: '1em' }}>
            {d}
          </span>
        ))}
      </motion.span>
    </span>
  );
}

export function RollingNumber({ value, className = '' }: RollingNumberProps) {
  const reduced = useReducedMotion();
  const str = String(value);
  // Brightness pulse on value change (not on mount). Imperative WAAPI rather
  // than state/class toggling: no re-render cascade, no remount — the digit
  // columns' in-flight roll springs keep playing underneath the flash.
  const prevValueRef = useRef(str);
  const pulseRef = useRef<HTMLSpanElement>(null);
  const lastPulseRef = useRef(0);
  useEffect(() => {
    if (prevValueRef.current === str) return;
    prevValueRef.current = str;
    if (reduced) return;
    // Coalesce streaming ticks: at most ~2 flashes/sec — the digit-roll
    // springs already convey every intermediate change.
    const now = performance.now();
    if (now - lastPulseRef.current < 500) return;
    lastPulseRef.current = now;
    pulseRef.current?.animate(
      [{ filter: 'brightness(1.6)' }, { filter: 'brightness(1)' }],
      { duration: 300, easing: 'cubic-bezier(0.16, 1, 0.3, 1)' },
    );
  }, [str, reduced]);
  const chars = str.split('');
  return (
    <span
      className={`inline-flex ${className}`}
      style={{ fontVariantNumeric: 'tabular-nums', lineHeight: '1em' }}
    >
      <span className="sr-only">{str}</span>
      <span aria-hidden ref={pulseRef} className="inline-flex">
        {chars.map((ch, i) =>
          /\d/.test(ch) ? (
            // Keyed by distance from the right edge so trailing digits keep
            // their columns when the digit count changes (999 → 1,000).
            <DigitColumn key={`d${chars.length - i}`} digit={Number(ch)} instant={!!reduced} />
          ) : (
            <span key={`c${chars.length - i}`} style={{ height: '1em', lineHeight: '1em' }}>
              {ch}
            </span>
          ),
        )}
      </span>
    </span>
  );
}
