import { useEffect } from 'react';
import { initPressJuice } from './pressJuice';
import { celebrate, burst } from './particles';

const KONAMI = [
  'ArrowUp', 'ArrowUp', 'ArrowDown', 'ArrowDown',
  'ArrowLeft', 'ArrowRight', 'ArrowLeft', 'ArrowRight',
  'b', 'a',
];

// Hosts the global interaction feel (button squash + spring) and one hidden
// toy: the Konami code fires a particle celebration. Deliberately nothing
// ambient or automatic — this is a professional tool; motion stays tied to
// direct user intent.
export function FxLayer() {
  useEffect(() => initPressJuice(), []);

  useEffect(() => {
    let i = 0;
    const onKey = (e: KeyboardEvent) => {
      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      i = key === KONAMI[i] ? i + 1 : key === KONAMI[0] ? 1 : 0;
      if (i < KONAMI.length) return;
      i = 0;
      celebrate();
      const w = window.innerWidth;
      const h = window.innerHeight;
      setTimeout(() => burst(w * 0.25, h * 0.55, { count: 90, speed: [180, 700] }), 350);
      setTimeout(() => burst(w * 0.75, h * 0.55, { count: 90, speed: [180, 700] }), 700);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return null;
}
