// Game-feel button presses: every button squashes on pointerdown, springs
// back with overshoot on release, and pops a tiny spark burst at the pointer
// when the press completes inside the button. WAAPI on the standalone `scale`
// property, so it never fights Tailwind transitions, framer-motion transforms,
// or the magnet layer's `translate`.

import { prefersReducedMotion } from './fxBus';
import { burst } from './particles';

const PRESS_EASE = 'cubic-bezier(0.16, 1, 0.3, 1)';
const RELEASE_EASE = 'cubic-bezier(0.34, 1.56, 0.64, 1)';

export function initPressJuice(): () => void {
  if (prefersReducedMotion()) return () => {};

  let pressed: HTMLElement | null = null;

  const release = (e?: PointerEvent) => {
    if (!pressed) return;
    pressed.animate(
      { scale: ['0.96', '1.015', '1'] },
      { duration: 300, easing: RELEASE_EASE, fill: 'forwards' },
    );
    // Spark pop only for a completed click (pointer still inside the button).
    if (e && pressed.contains(e.target as Node)) {
      burst(e.clientX, e.clientY, {
        count: 7,
        speed: [40, 240],
        size: [0.8, 1.8],
        ttl: [0.3, 0.6],
        gravity: 220,
      });
    }
    pressed = null;
  };

  const onDown = (e: PointerEvent) => {
    const t = e.target as HTMLElement | null;
    if (!t || typeof t.closest !== 'function') return;
    const el = t.closest('button, [role="button"]') as HTMLElement | null;
    if (!el || el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true') return;
    release();
    pressed = el;
    el.animate({ scale: ['1', '0.96'] }, { duration: 110, easing: PRESS_EASE, fill: 'forwards' });
  };

  const onUp = (e: PointerEvent) => release(e);
  const onCancel = () => release();

  window.addEventListener('pointerdown', onDown, true);
  window.addEventListener('pointerup', onUp, true);
  window.addEventListener('pointercancel', onCancel, true);
  return () => {
    window.removeEventListener('pointerdown', onDown, true);
    window.removeEventListener('pointerup', onUp, true);
    window.removeEventListener('pointercancel', onCancel, true);
  };
}
