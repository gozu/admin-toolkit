import { useEffect } from 'react';
import { initPressJuice } from './pressJuice';

// Hosts the global interaction feel: button squash + spring on every press.
// Deliberately nothing ambient or automatic — this is a professional tool;
// motion stays tied to direct user intent.
export function FxLayer() {
  useEffect(() => initPressJuice(), []);
  return null;
}
