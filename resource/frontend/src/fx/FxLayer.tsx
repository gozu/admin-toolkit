import { useEffect, useRef } from 'react';
import { useDiag } from '../context/DiagContext';
import { AuroraBackground } from './AuroraBackground';
import { initSpotlight } from './spotlight';
import { initPressJuice } from './pressJuice';
import { fxFlare } from './fxBus';
import { celebrate } from './particles';

const CELEBRATION_COOLDOWN_MS = 10_000;

// Mounts the ambient layers (aurora, film grain) and the global interaction
// juice (cursor spotlight, button press squash), and fires the celebration
// moment — aurora flare + particle shockwave — when the analysis aggregate
// transitions running → done live in this session.
export function FxLayer() {
  const { state } = useDiag();
  const analysis = state.parsedData.analysisLoading;
  const prevRef = useRef(analysis);
  const lastCelebrationRef = useRef(0);

  useEffect(() => {
    const cleanSpotlight = initSpotlight();
    const cleanPress = initPressJuice();
    return () => {
      cleanSpotlight();
      cleanPress();
    };
  }, []);

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
  }, [analysis]);

  return (
    <>
      <AuroraBackground />
      <div aria-hidden className="fx-grain" />
    </>
  );
}
