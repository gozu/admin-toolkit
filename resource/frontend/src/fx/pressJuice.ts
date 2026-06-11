// Game-feel button presses: every button squashes on pointerdown and springs
// back with a slight overshoot on release. WAAPI on the standalone `scale`
// property, so it never fights Tailwind transitions or framer-motion
// transforms, and it covers every current and future button for free.

import { prefersReducedMotion } from './fxBus';

const PRESS_EASE = 'cubic-bezier(0.16, 1, 0.3, 1)';
const RELEASE_EASE = 'cubic-bezier(0.34, 1.56, 0.64, 1)';

export function initPressJuice(): () => void {
  if (prefersReducedMotion()) return () => {};

  let pressed: HTMLElement | null = null;

  const release = () => {
    if (!pressed) return;
    pressed.animate(
      { scale: ['0.96', '1.015', '1'] },
      { duration: 300, easing: RELEASE_EASE, fill: 'forwards' },
    );
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

  window.addEventListener('pointerdown', onDown, true);
  window.addEventListener('pointerup', release, true);
  window.addEventListener('pointercancel', release, true);
  return () => {
    window.removeEventListener('pointerdown', onDown, true);
    window.removeEventListener('pointerup', release, true);
    window.removeEventListener('pointercancel', release, true);
  };
}
