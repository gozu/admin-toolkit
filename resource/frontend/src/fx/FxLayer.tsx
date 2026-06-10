import { useEffect, useRef } from 'react';
import { useDiag } from '../context/DiagContext';
import { AuroraBackground } from './AuroraBackground';
import { initPressJuice } from './pressJuice';
import { initMagnet } from './magnet';
import { fxFlare, fxEnergy, prefersReducedMotion } from './fxBus';
import { celebrate, burst } from './particles';

const CELEBRATION_COOLDOWN_MS = 10_000;

const KONAMI = [
  'ArrowUp', 'ArrowUp', 'ArrowDown', 'ArrowDown',
  'ArrowLeft', 'ArrowRight', 'ArrowLeft', 'ArrowRight',
  'b', 'a',
];

// Subtle full-screen impact when the celebration fires: a 0.5% scale pulse
// with a 2px settle, gone in under half a second.
function screenThump() {
  if (prefersReducedMotion()) return;
  const root = document.getElementById('root');
  root?.animate(
    [
      { scale: '1', translate: '0px 0px' },
      { scale: '1.005', translate: '0px -2px', offset: 0.25 },
      { scale: '0.999', translate: '0px 1px', offset: 0.6 },
      { scale: '1', translate: '0px 0px' },
    ],
    { duration: 450, easing: 'ease-out' },
  );
}

// Mounts the ambient layers (aurora, film grain) and the global interaction
// juice (button squash + click sparks, magnetic buttons). Drives the aurora's
// energy from analysis progress and fires the celebration — flare + particle
// shockwave + screen thump — when the aggregate transitions running → done
// live in this session.
export function FxLayer() {
  const { state } = useDiag();
  const analysis = state.parsedData.analysisLoading;
  const prevRef = useRef(analysis);
  const lastCelebrationRef = useRef(0);

  useEffect(() => {
    const cleanPress = initPressJuice();
    const cleanMagnet = initMagnet();
    return () => {
      cleanPress();
      cleanMagnet();
    };
  }, []);

  // Konami overdrive: ↑↑↓↓←→←→BA pushes the aurora to full energy for a few
  // seconds and rains a triple celebration. A toy, deliberately.
  useEffect(() => {
    let i = 0;
    let resetTimer: ReturnType<typeof setTimeout> | undefined;
    const onKey = (e: KeyboardEvent) => {
      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      i = key === KONAMI[i] ? i + 1 : key === KONAMI[0] ? 1 : 0;
      if (i < KONAMI.length) return;
      i = 0;
      fxFlare();
      fxEnergy(1);
      celebrate();
      const w = window.innerWidth;
      const h = window.innerHeight;
      setTimeout(() => burst(w * 0.25, h * 0.55, { count: 90, speed: [180, 700] }), 350);
      setTimeout(() => burst(w * 0.75, h * 0.55, { count: 90, speed: [180, 700] }), 700);
      clearTimeout(resetTimer);
      resetTimer = setTimeout(() => fxEnergy(0), 6000);
    };
    window.addEventListener('keydown', onKey);
    return () => {
      clearTimeout(resetTimer);
      window.removeEventListener('keydown', onKey);
    };
  }, []);

  // Aurora energy tracks analysis activity: calm at rest, drifting faster and
  // brighter while modules are loading, scaling with overall progress.
  useEffect(() => {
    if (analysis?.active) {
      fxEnergy(0.25 + 0.55 * ((analysis.progressPct ?? 0) / 100));
    } else {
      fxEnergy(0);
    }
  }, [analysis?.active, analysis?.progressPct]);

  useEffect(() => {
    const prev = prevRef.current;
    prevRef.current = analysis;
    if (!analysis || !prev) return;
    const completedLive = prev.active && !analysis.active && analysis.phase === 'done';
    if (!completedLive) return;
    const now = Date.now();
    if (now - lastCelebrationRef.current < CELEBRATION_COOLDOWN_MS) return;
    lastCelebrationRef.current = now;
    if (document.hidden) return;
    fxFlare();
    celebrate();
    screenThump();
  }, [analysis]);

  return (
    <>
      <AuroraBackground />
      <div aria-hidden className="fx-grain" />
    </>
  );
}
