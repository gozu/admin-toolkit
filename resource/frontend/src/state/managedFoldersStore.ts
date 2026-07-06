import { createModuleScanStore } from './createModuleScanStore';

export interface ManagedFolder {
  id: string;
  name: string;
}

export interface ManagedFoldersData {
  folders: ManagedFolder[];
  /** Auto-provisioned 'admin-toolkit-archive' folder id ('' unless the
   * Archive Folders Connection plugin setting is configured). */
  archiveDefaultId?: string;
  archiveConnection?: string;
}

export const managedFoldersScan = createModuleScanStore<ManagedFoldersData, never>({
  loadingField: 'codeEnvCleanerLoading',
  fallbackEndpoint: '/api/managed-folders',
});
