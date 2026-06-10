// Tiny event bus for cross-cutting visual effects. Window CustomEvents keep
// the FX layer decoupled from app state plumbing: any code can request a
// flare/celebration without importing canvas internals.

export const FX_EVENTS = {
  /** Aurora background flares (success-green surge that decays over ~3s). */
  flare: 'admin-toolkit:fx-flare',
  /** Global analysis aggregate flipped running → done this session. */
  analysisComplete: 'admin-toolkit:fx-analysis-complete',
} as const;

export function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

export function fxFlare(strength = 1): void {
  window.dispatchEvent(new CustomEvent(FX_EVENTS.flare, { detail: { strength } }));
}

export function fxAnalysisComplete(): void {
  window.dispatchEvent(new CustomEvent(FX_EVENTS.analysisComplete));
}
