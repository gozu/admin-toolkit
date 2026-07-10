import { createModuleScanStore } from './createModuleScanStore';
import type { AdoptionInventoryData } from '../types';

// Config-tree object inventory — a blocking macro walk of config/projects/
// (cached server-side), so a single GET like /api/cru. Separate endpoint from
// /api/adoption on purpose: this layer is "full history of SURVIVING objects"
// (survivorship bias), never the persistent git spine — keeping the layers in
// different endpoints is what makes window-honesty structural.
export const adoptionInventoryScan = createModuleScanStore<AdoptionInventoryData, never>({
  loadingField: 'adoptionInventoryLoading',
  fallbackEndpoint: '/api/adoption/inventory',
});
