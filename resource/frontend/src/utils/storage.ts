const STORAGE_KEY_PREFIX = 'diagparser.tools';

export function loadFromStorage<T>(key: string, defaultValue: T): T {
  if (typeof window === 'undefined') return defaultValue;
  try {
    const stored = window.localStorage.getItem(`${STORAGE_KEY_PREFIX}.${key}`);
    return stored ? JSON.parse(stored) : defaultValue;
  } catch {
    return defaultValue;
  }
}

export function saveToStorage<T>(key: string, value: T): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(`${STORAGE_KEY_PREFIX}.${key}`, JSON.stringify(value));
  } catch {
    // Ignore storage errors
  }
}
