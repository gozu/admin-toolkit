import { createSyncStore } from './createSyncStore';

export interface ConnectionUsageScanError {
  projectKey: string;
  area: string;
  error: string;
}

export interface ConnectionUsageScanState {
  scanning: boolean;
  scanned: number | null;
  total: number | null;
  error: string | null;
  scanErrors: ConnectionUsageScanError[];
  failedProjectCount: number;
  scannedProjectCount: number;
}

const initial: ConnectionUsageScanState = {
  scanning: false,
  scanned: null,
  total: null,
  error: null,
  scanErrors: [],
  failedProjectCount: 0,
  scannedProjectCount: 0,
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
