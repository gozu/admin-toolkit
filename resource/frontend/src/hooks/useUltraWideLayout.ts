import { useDiag } from '../context/DiagContext';
import { useViewportRatio } from './useViewportRatio';

const ULTRA_WIDE_RATIO_MIN = 21 / 9;

export function useUltraWideLayout() {
  const { state, setLayoutMode } = useDiag();
  const ratio = useViewportRatio();
  const isUltraWideRatio = ratio >= ULTRA_WIDE_RATIO_MIN;
  const ultraWideEnabled = state.layoutMode === 'ultrawide' && isUltraWideRatio;

  return {
    layoutMode: state.layoutMode,
    setLayoutMode,
    isUltraWideRatio,
    ultraWideEnabled,
  };
}
