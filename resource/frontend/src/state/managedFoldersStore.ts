import { createModuleScanStore } from './createModuleScanStore';

export interface ManagedFolder {
  id: string;
  name: string;
}

export interface ManagedFoldersData {
  folders: ManagedFolder[];
}

export const managedFoldersScan = createModuleScanStore<ManagedFoldersData, never>({
  loadingField: 'codeEnvCleanerLoading',
  fallbackEndpoint: '/api/managed-folders',
});
