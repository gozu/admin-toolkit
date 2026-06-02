import { useSyncExternalStore } from 'react';
import { subscribeSessionEpoch } from './sessionCache';

export interface SyncStore<T> {
  get: () => T;
  set: (next: T) => void;
  patch: (p: Partial<T>) => void;
  subscribe: (listener: () => void) => () => void;
  use: () => T;
}

export function createSyncStore<T>(
  initial: T,
  opts: { sessionScoped?: boolean } = {},
): SyncStore<T> {
  let state = initial;
  const listeners = new Set<() => void>();
  const emit = () => listeners.forEach((l) => l());
  const subscribe = (l: () => void) => {
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  };
  if (opts.sessionScoped) {
    subscribeSessionEpoch(() => {
      state = initial;
      emit();
    });
  }
  return {
    get: () => state,
    set: (next: T) => {
      state = next;
      emit();
    },
    patch: (p: Partial<T>) => {
      state = { ...state, ...p };
      emit();
    },
    subscribe,
    use: () => useSyncExternalStore(subscribe, () => state, () => state),
  };
}
