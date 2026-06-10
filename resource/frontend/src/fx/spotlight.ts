// Cursor spotlight: one delegated pointermove listener feeds --spot-x/y/o
// custom properties on whichever card surface the pointer is over; index.css
// turns those into a mouse-tracking border glow + interior sheen. rAF-throttled
// so the cost is one closest() + three style writes per frame, max.

const SELECTOR = [
  '.glass-card',
  '.glass-card-elevated',
  '.metric-card',
  '.chart-container',
  '.card-alert-critical',
  '.card-alert-warning',
  '.card-alert-success',
  '.card-alert-info',
].join(', ');

export function initSpotlight(): () => void {
  let active: HTMLElement | null = null;
  let raf = 0;
  let lastX = 0;
  let lastY = 0;
  let lastTarget: EventTarget | null = null;

  const clear = () => {
    if (active) {
      active.style.setProperty('--spot-o', '0');
      active = null;
    }
  };

  const apply = () => {
    raf = 0;
    const t = lastTarget as HTMLElement | null;
    const card = t && typeof t.closest === 'function' ? (t.closest(SELECTOR) as HTMLElement | null) : null;
    if (active && active !== card) clear();
    if (card) {
      const r = card.getBoundingClientRect();
      card.style.setProperty('--spot-x', `${(lastX - r.left).toFixed(1)}px`);
      card.style.setProperty('--spot-y', `${(lastY - r.top).toFixed(1)}px`);
      card.style.setProperty('--spot-o', '1');
      active = card;
    }
  };

  const onMove = (e: PointerEvent) => {
    lastX = e.clientX;
    lastY = e.clientY;
    lastTarget = e.target;
    if (!raf) raf = requestAnimationFrame(apply);
  };

  const onLeave = () => clear();

  window.addEventListener('pointermove', onMove, { passive: true });
  document.documentElement.addEventListener('pointerleave', onLeave);
  return () => {
    if (raf) cancelAnimationFrame(raf);
    window.removeEventListener('pointermove', onMove);
    document.documentElement.removeEventListener('pointerleave', onLeave);
    clear();
  };
}
