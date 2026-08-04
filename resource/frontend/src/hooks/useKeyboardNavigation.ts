import { useEffect } from 'react';
import type { PageId } from '../types';

interface UseKeyboardNavigationOptions {
  onNavigate: (page: PageId) => void;
  onOpenPalette: () => void;
  onOpenShortcuts?: () => void;
  onToggleTheme?: () => void;
  /** Suspend all bindings while a top layer (palette/overlay) owns the keyboard. */
  enabled?: boolean;
}

const PAGE_ORDER: PageId[] = [
  'summary',
  'filesystem',
  'resources',
  'connections-inventory',
  'logs',
  'plugins',
  'projects',
  'project-cleaner',
  'code-envs',
  'code-envs-comparison',
  'code-envs-broken',
];

const NUMBER_KEY_MAP: Record<string, PageId> = {
  '1': 'summary',
  '2': 'filesystem',
  '3': 'resources',
  '4': 'connections-inventory',
  '5': 'logs',
  '6': 'plugins',
};

function isInputFocused(): boolean {
  const el = document.activeElement;
  if (!el) return false;
  const tag = el.tagName.toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true;
  if ((el as HTMLElement).isContentEditable) return true;
  return false;
}

export function useKeyboardNavigation({
  onNavigate,
  onOpenPalette,
  onOpenShortcuts,
  onToggleTheme,
  enabled = true,
}: UseKeyboardNavigationOptions): void {
  useEffect(() => {
    if (!enabled) return;
    function handleKeyDown(e: KeyboardEvent) {
      // Cmd+K / Ctrl+K always triggers palette, even in inputs
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        onOpenPalette();
        return;
      }

      // All other shortcuts are disabled when focus is in an input element
      if (isInputFocused()) return;

      // Number keys: jump to specific pages
      if (!e.metaKey && !e.ctrlKey && !e.altKey) {
        const page = NUMBER_KEY_MAP[e.key];
        if (page) {
          e.preventDefault();
          onNavigate(page);
          return;
        }
      }

      // Forward slash: open command palette
      if (e.key === '/' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        onOpenPalette();
        return;
      }

      // Question mark (Shift+/): open the shortcuts overlay; falls back to the
      // palette for callers that don't wire onOpenShortcuts.
      if (e.key === '?' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        if (onOpenShortcuts) {
          onOpenShortcuts();
        } else {
          onOpenPalette();
        }
        return;
      }

      // Bracket keys: prev/next page
      if (e.key === '[' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        const currentPath = window.location.hash.replace('#', '') || '';
        const currentIndex = PAGE_ORDER.indexOf(currentPath as PageId);
        const prevIndex = currentIndex <= 0 ? PAGE_ORDER.length - 1 : currentIndex - 1;
        onNavigate(PAGE_ORDER[prevIndex]);
        return;
      }

      if (e.key === ']' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        const currentPath = window.location.hash.replace('#', '') || '';
        const currentIndex = PAGE_ORDER.indexOf(currentPath as PageId);
        const nextIndex =
          currentIndex < 0 || currentIndex >= PAGE_ORDER.length - 1 ? 0 : currentIndex + 1;
        onNavigate(PAGE_ORDER[nextIndex]);
        return;
      }

      // t: toggle theme
      if (e.key === 't' && !e.metaKey && !e.ctrlKey && !e.altKey && onToggleTheme) {
        e.preventDefault();
        onToggleTheme();
        return;
      }
    }

    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [onNavigate, onOpenPalette, onOpenShortcuts, onToggleTheme, enabled]);
}
