import { createSyncStore } from '../state/createSyncStore';
import {
  DEFAULT_HEALTH_FACTOR_TOGGLES,
  HEALTH_FACTOR_CONTROLS,
  type HealthFactorKey,
  type HealthFactorToggles,
} from './useHealthScore';

const STORAGE_KEY = `health-factors:v${HEALTH_FACTOR_CONTROLS.length}`;

function loadInitial(): HealthFactorToggles {
  try {
    const raw = typeof window !== 'undefined' ? window.localStorage.getItem(STORAGE_KEY) : null;
    if (!raw) return DEFAULT_HEALTH_FACTOR_TOGGLES;
    return { ...DEFAULT_HEALTH_FACTOR_TOGGLES, ...(JSON.parse(raw) as Partial<HealthFactorToggles>) };
  } catch {
    return DEFAULT_HEALTH_FACTOR_TOGGLES;
  }
}

const store = createSyncStore<HealthFactorToggles>(loadInitial(), { sessionScoped: true });

export function useSharedHealthFactors() {
  const toggles = store.use();
  const toggleHealthFactor = (key: HealthFactorKey) => {
    const next = { ...store.get(), [key]: !store.get()[key] };
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* localStorage unavailable */
    }
    store.set(next);
  };
  return { healthFactorToggles: toggles, toggleHealthFactor };
}
