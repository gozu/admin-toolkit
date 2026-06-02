import { useEffect, useState } from 'react';
import { loadFromStorage, saveToStorage } from '../utils/storage';

export function useToggleFlag(
  storageKey: string,
  eventName: string,
): [boolean, (v: boolean) => void] {
  const [value, setValue] = useState<boolean>(() =>
    loadFromStorage<boolean>(storageKey, false),
  );
  useEffect(() => {
    const sync = () => setValue(loadFromStorage<boolean>(storageKey, false));
    window.addEventListener(eventName, sync);
    window.addEventListener('storage', sync);
    return () => {
      window.removeEventListener(eventName, sync);
      window.removeEventListener('storage', sync);
    };
  }, [storageKey, eventName]);
  const set = (next: boolean) => {
    setValue(next);
    saveToStorage(storageKey, next);
    window.dispatchEvent(new Event(eventName));
  };
  return [value, set];
}
