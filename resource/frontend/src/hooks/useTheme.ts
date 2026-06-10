import { useState, useEffect } from 'react';
import { flushSync } from 'react-dom';

type Theme = 'dark' | 'light';
const THEME_STORAGE_KEY = 'admin-toolkit-theme';

type ViewTransitionDocument = Document & {
  startViewTransition?: (callback: () => void) => unknown;
};

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    return (localStorage.getItem(THEME_STORAGE_KEY) as Theme) || 'dark';
  });

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  // Circular reveal from the click point via the View Transitions API; CSS in
  // index.css animates ::view-transition-new(root)'s clip-path. Falls back to
  // an instant swap when the API is missing or reduced motion is preferred.
  const toggle = (ev?: { clientX: number; clientY: number }) => {
    const next: Theme = theme === 'dark' ? 'light' : 'dark';
    const doc = document as ViewTransitionDocument;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!doc.startViewTransition || reduced) {
      setTheme(next);
      return;
    }
    const root = document.documentElement;
    root.style.setProperty('--theme-wipe-x', `${ev?.clientX ?? window.innerWidth - 80}px`);
    root.style.setProperty('--theme-wipe-y', `${ev?.clientY ?? 40}px`);
    doc.startViewTransition(() => {
      // Snapshot integrity: the attribute must change synchronously inside the
      // transition callback; the effect above re-applies the same value later.
      root.setAttribute('data-theme', next);
      flushSync(() => setTheme(next));
    });
  };

  return { theme, toggle };
}
