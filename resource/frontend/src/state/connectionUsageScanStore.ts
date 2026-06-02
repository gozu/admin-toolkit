import { createSyncStore } from './createSyncStore';

export interface ConnectionUsageScanState {
  scanning: boolean;
  scanned: number | null;
  total: number | null;
  error: string | null;
}

const initial: ConnectionUsageScanState = {
  scanning: false,
  scanned: null,
  total: null,
  error: null,
};

export const connectionUsageScanStore = createSyncStore<ConnectionUsageScanState>(initial, {
  sessionScoped: true,
});

let abortController: AbortController | null = null;

export function getConnectionUsageScanController(): AbortController | null {
  return abortController;
}

export function setConnectionUsageScanController(controller: AbortController | null): void {
  abortController = controller;
}
