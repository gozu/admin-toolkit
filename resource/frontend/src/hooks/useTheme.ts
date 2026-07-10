import { useCallback, useState, useEffect } from 'react';
import { flushSync } from 'react-dom';

type Theme = 'dark' | 'light' | 'dss-dark';
const THEME_STORAGE_KEY = 'admin-toolkit-theme';
// Which dark flavor the sun/moon toggle returns to after a round-trip to light.
const DARK_FLAVOR_STORAGE_KEY = 'admin-toolkit-dark-flavor';

const isTheme = (value: string | null): value is Theme =>
  value === 'dark' || value === 'light' || value === 'dss-dark';

type ViewTransitionDocument = Document & {
  startViewTransition?: (callback: () => void) => unknown;
};

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return isTheme(stored) ? stored : 'dark';
  });

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem(THEME_STORAGE_KEY, theme);
    if (theme !== 'light') localStorage.setItem(DARK_FLAVOR_STORAGE_KEY, theme);
  }, [theme]);

  // Circular reveal from the click point via the View Transitions API; CSS in
  // index.css animates ::view-transition-new(root)'s clip-path. Falls back to
  // an instant swap when the API is missing or reduced motion is preferred.
  const applyTheme = useCallback((next: Theme, origin?: { clientX: number; clientY: number }) => {
    const doc = document as ViewTransitionDocument;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!doc.startViewTransition || reduced) {
      setTheme(next);
      return;
    }
    const root = document.documentElement;
    root.style.setProperty('--theme-wipe-x', `${origin?.clientX ?? window.innerWidth - 80}px`);
    root.style.setProperty('--theme-wipe-y', `${origin?.clientY ?? 40}px`);
    doc.startViewTransition(() => {
      // Snapshot integrity: the attribute must change synchronously inside the
      // transition callback; the effect above re-applies the same value later.
      root.setAttribute('data-theme', next);
      flushSync(() => setTheme(next));
    });
  }, []);

  const toggle = (ev?: { clientX: number; clientY: number }) => {
    let next: Theme;
    if (theme === 'light') {
      const flavor = localStorage.getItem(DARK_FLAVOR_STORAGE_KEY);
      next = isTheme(flavor) && flavor !== 'light' ? flavor : 'dark';
    } else {
      next = 'light';
    }
    applyTheme(next, ev);
  };

  // Hidden "DSS dark" flavor (keyword easter egg): flips between the two dark
  // flavors; from light it lands directly in dss-dark. No click point — the
  // wipe originates from the screen center.
  const toggleDssDark = useCallback(() => {
    const next: Theme = theme === 'dss-dark' ? 'dark' : 'dss-dark';
    applyTheme(next, { clientX: window.innerWidth / 2, clientY: window.innerHeight / 2 });
  }, [theme, applyTheme]);

  return { theme, toggle, toggleDssDark };
}
