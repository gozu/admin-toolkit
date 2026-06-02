import { createModuleScanStore } from './createModuleScanStore';
import type { CodeEnvCompareResult } from '../types';

export const codeEnvComparisonScan = createModuleScanStore<CodeEnvCompareResult, never>({
  loadingField: 'codeEnvsComparisonLoading',
  fallbackEndpoint: '/api/code-envs/compare',
});
