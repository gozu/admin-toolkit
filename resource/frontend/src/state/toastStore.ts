// App-wide transient feedback (ToastHub renders these). Deliberately tiny:
// fire-and-forget chips for outcomes that would otherwise be invisible or
// ad-hoc inline text. Errors that need reading live in cards, not here.
import { createSyncStore } from './createSyncStore';

export type ToastKind = 'success' | 'error' | 'info';

export interface Toast {
  id: number;
  kind: ToastKind;
  title: string;
  detail?: string;
  /** ms before auto-dismiss */
  duration: number;
}

interface ToastState {
  toasts: Toast[];
}

export const toastStore = createSyncStore<ToastState>({ toasts: [] });

let nextId = 1;
const MAX_VISIBLE = 4;

export function pushToast(
  kind: ToastKind,
  title: string,
  opts: { detail?: string; duration?: number } = {},
): void {
  const toast: Toast = {
    id: nextId++,
    kind,
    title,
    detail: opts.detail,
    duration: opts.duration ?? (kind === 'error' ? 6500 : 4200),
  };
  const { toasts } = toastStore.get();
  toastStore.set({ toasts: [...toasts, toast].slice(-MAX_VISIBLE) });
  window.setTimeout(() => dismissToast(toast.id), toast.duration);
}

export function dismissToast(id: number): void {
  const { toasts } = toastStore.get();
  if (toasts.some((t) => t.id === id)) {
    toastStore.set({ toasts: toasts.filter((t) => t.id !== id) });
  }
}
