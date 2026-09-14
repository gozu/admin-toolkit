import { useEffect } from 'react';
import { initPressJuice } from './pressJuice';

// Delegates press feedback only to buttons explicitly marked data-press-juice.
export function FxLayer() {
  useEffect(() => initPressJuice(), []);
  return null;
}
