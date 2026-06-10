// Magnetic buttons: the hovered button leans a couple of pixels toward the
// cursor and springs back to rest on leave. Uses the standalone `translate`
// property, which composes independently with pressJuice's `scale` and any
// framer-motion/Tailwind transform.

import { prefersReducedMotion } from './fxBus';

const MAX_SHIFT = 2.5; // px
const PULL = 0.12; // fraction of cursor offset from center

export function initMagnet(): () => void {
  if (prefersReducedMotion()) return () => {};

  let active: HTMLElement | null = null;
  let raf = 0;
  let lastX = 0;
  let lastY = 0;
  let lastTarget: EventTarget | null = null;

  const rest = (el: HTMLElement) => {
    const from = getComputedStyle(el).translate;
    el.style.removeProperty('translate');
    if (from && from !== 'none') {
      el.animate(
        { translate: [from, '0px 0px'] },
        { duration: 320, easing: 'cubic-bezier(0.34, 1.56, 0.64, 1)' },
      );
    }
  };

  const apply = () => {
    raf = 0;
    const t = lastTarget as HTMLElement | null;
    const btn =
      t && typeof t.closest === 'function'
        ? (t.closest('button:not(:disabled)') as HTMLElement | null)
        : null;
    if (active && active !== btn) {
      rest(active);
      active = null;
    }
    if (!btn) return;
    const r = btn.getBoundingClientRect();
    // Very large buttons (full-width rows, drag handles) shouldn't drift.
    if (r.width > 360 || r.height > 80) return;
    const dx = lastX - (r.left + r.width / 2);
    const dy = lastY - (r.top + r.height / 2);
    const clamp = (v: number) => Math.max(-MAX_SHIFT, Math.min(MAX_SHIFT, v * PULL));
    btn.style.translate = `${clamp(dx).toFixed(2)}px ${clamp(dy).toFixed(2)}px`;
    active = btn;
  };

  const onMove = (e: PointerEvent) => {
    lastX = e.clientX;
    lastY = e.clientY;
    lastTarget = e.target;
    if (!raf) raf = requestAnimationFrame(apply);
  };

  const onLeave = () => {
    if (active) {
      rest(active);
      active = null;
    }
  };

  window.addEventListener('pointermove', onMove, { passive: true });
  document.documentElement.addEventListener('pointerleave', onLeave);
  return () => {
    if (raf) cancelAnimationFrame(raf);
    window.removeEventListener('pointermove', onMove);
    document.documentElement.removeEventListener('pointerleave', onLeave);
    onLeave();
  };
}
