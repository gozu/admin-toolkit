import { useEffect, useRef, type TdHTMLAttributes } from 'react';

/** Compare semantic values, not DOM text, so sorting and expanding aren't data arrivals. */
export function ArrivalCell({
  arrivalValue,
  ...props
}: TdHTMLAttributes<HTMLTableCellElement> & { arrivalValue?: string | number }) {
  const ref = useRef<HTMLTableCellElement>(null);
  const previous = useRef(arrivalValue);
  const last = useRef(-Infinity);
  const pulse = useRef<Animation | null>(null);
  useEffect(() => () => pulse.current?.cancel(), []);
  useEffect(() => {
    const changed =
      previous.current !== undefined &&
      arrivalValue !== undefined &&
      previous.current !== arrivalValue;
    previous.current = arrivalValue;
    if (
      !changed ||
      document.hidden ||
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    )
      return;
    const cell = ref.current;
    if (!cell || performance.now() - last.current < 1000) return;
    const bounds = cell.getBoundingClientRect();
    if (
      bounds.bottom < 0 ||
      bounds.top > window.innerHeight ||
      bounds.right < 0 ||
      bounds.left > window.innerWidth ||
      bounds.width === 0
    )
      return;
    last.current = performance.now();
    const accent = getComputedStyle(cell).getPropertyValue('--text-secondary').trim();
    pulse.current?.cancel();
    pulse.current = cell.animate(
      [{ boxShadow: `inset 2px 0 ${accent}` }, { boxShadow: 'inset 2px 0 transparent' }],
      { duration: 680, delay: 100, easing: 'ease-out' },
    );
  }, [arrivalValue]);
  return <td ref={ref} {...props} />;
}
