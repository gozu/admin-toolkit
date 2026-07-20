import { useCallback, useEffect, useRef, useState } from 'react';

/** Per-scene step state machine for the explainer.
 *
 * Each scene is ONE persistent diagram whose look is driven almost entirely by
 * CSS selectors keyed on the section's `data-step` attribute; React state is
 * only the step index plus viewport bookkeeping:
 *
 *  - `live`      — the scene is (partially) on screen. CSS loops key their
 *                  `animation-play-state` on `[data-live]`; tracked WAAPI
 *                  animations are paused/resumed here.
 *  - `revealed`  — the scene has entered the viewport at least once. Draw-in
 *                  effects (`getTotalLength()` measurements) key on this, so
 *                  they never run against a hidden subtree — and never on
 *                  mount, only on first scroll-into-view.
 *
 * WAAPI choreography pattern: diagrams start their animations inside a
 * `useEffect(..., [step, live])`, wrap each `element.animate(...)` in
 * `track(...)`, and return `cancelTracked` as the cleanup. The cleanup of the
 * previous run fires before the next run (and on unmount, covering the
 * router's 70ms AnimatePresence exit) — so no Animation ever leaks across a
 * step change or a page swap.
 *
 * `sectionRef` is a CALLBACK ref (not a RefObject): the observed element is
 * plain state, so nothing ref-shaped escapes into render.
 */
export interface SceneApi {
  step: number;
  stepCount: number;
  live: boolean;
  revealed: boolean;
  next: () => void;
  prev: () => void;
  goTo: (i: number) => void;
  bindSection: (el: HTMLElement | null) => void;
  /** Register a WAAPI Animation for offscreen pause + cleanup. Returns it. */
  track: (anim: Animation) => Animation;
  /** Cancel every tracked Animation (use as the [step] effect cleanup). */
  cancelTracked: () => void;
}

export function useSceneSteps(stepCount: number): SceneApi {
  const [step, setStep] = useState(0);
  const [el, setEl] = useState<HTMLElement | null>(null);
  const [live, setLive] = useState(false);
  const [revealed, setRevealed] = useState(false);
  const animsRef = useRef<Animation[]>([]);
  // Animations WE paused on scroll-out — the only ones resumed on scroll-in
  // (calling .play() on a finished Animation would restart it from zero).
  const pausedRef = useRef<Set<Animation>>(new Set());

  useEffect(() => {
    if (!el) return;
    const io = new IntersectionObserver(
      ([entry]) => {
        setLive(entry.isIntersecting);
        if (entry.isIntersecting) setRevealed(true);
      },
      { threshold: 0.15 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [el]);

  // Offscreen pause / on-screen resume for tracked WAAPI animations.
  useEffect(() => {
    if (live) {
      for (const anim of pausedRef.current) {
        if (anim.playState === 'paused') anim.play();
      }
      pausedRef.current.clear();
    } else {
      for (const anim of animsRef.current) {
        if (anim.playState === 'running') {
          anim.pause();
          pausedRef.current.add(anim);
        }
      }
    }
  }, [live]);

  const track = useCallback((anim: Animation): Animation => {
    // Opportunistic prune so long-lived scenes don't accumulate dead handles.
    animsRef.current = animsRef.current.filter(
      (a) => a.playState === 'running' || a.playState === 'paused',
    );
    animsRef.current.push(anim);
    return anim;
  }, []);

  const cancelTracked = useCallback(() => {
    for (const anim of animsRef.current) anim.cancel();
    animsRef.current = [];
    pausedRef.current.clear();
  }, []);

  // Belt-and-braces: never leak animations past unmount.
  useEffect(() => cancelTracked, [cancelTracked]);

  const goTo = useCallback(
    (i: number) => setStep(Math.max(0, Math.min(stepCount - 1, i))),
    [stepCount],
  );
  const next = useCallback(
    () => setStep((s) => Math.min(stepCount - 1, s + 1)),
    [stepCount],
  );
  const prev = useCallback(() => setStep((s) => Math.max(0, s - 1)), []);

  return {
    step,
    stepCount,
    live,
    revealed,
    next,
    prev,
    goTo,
    bindSection: setEl,
    track,
    cancelTracked,
  };
}

/** Start a WAAPI leg along an SVG-syntax path. Chain legs via `.finished`;
 * ALWAYS wrap the returned Animation in `track(...)` so it pauses offscreen
 * and cancels on step change / unmount. */
export function animateAlongPath(
  el: HTMLElement,
  pathD: string,
  opts: { duration: number; delay?: number; easing?: string; fill?: FillMode } = {
    duration: 1000,
  },
): Animation {
  el.style.offsetPath = `path('${pathD}')`;
  el.style.offsetRotate = '0deg';
  return el.animate(
    [{ offsetDistance: '0%' }, { offsetDistance: '100%' }],
    {
      duration: opts.duration,
      delay: opts.delay ?? 0,
      easing: opts.easing ?? 'cubic-bezier(0.45, 0, 0.25, 1)',
      fill: opts.fill ?? 'forwards',
    },
  );
}
