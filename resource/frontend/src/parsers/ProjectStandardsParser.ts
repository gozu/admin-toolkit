import { BaseJSONParser } from './BaseParser';
import type { ContainerExecMode } from '../types';

interface ProjectStandardsData {
  generalParameters?: {
    containerSelection?: { containerMode?: unknown };
    containerForVisualRecipesWorkloads?: { containerMode?: unknown };
  };
}

export interface ProjectStandardsResult {
  userCodeMode: ContainerExecMode;
  visualRecipesMode: ContainerExecMode;
}

function normalizeContainerMode(raw: unknown): ContainerExecMode {
  if (typeof raw !== 'string' || raw.length === 0) return 'NONE';
  const upper = raw.toUpperCase();
  if (upper === 'NONE') return 'NONE';
  if (upper === 'INHERIT') return 'INHERIT';
  return 'CONTAINER';
}

export class ProjectStandardsParser extends BaseJSONParser<ProjectStandardsResult> {
  processData(data: ProjectStandardsData): ProjectStandardsResult {
    const gp = data?.generalParameters ?? {};
    return {
      userCodeMode: normalizeContainerMode(gp.containerSelection?.containerMode),
      visualRecipesMode: normalizeContainerMode(
        gp.containerForVisualRecipesWorkloads?.containerMode
      ),
    };
  }
}
