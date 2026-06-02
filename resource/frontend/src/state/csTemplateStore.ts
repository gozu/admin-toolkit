import { createModuleScanStore } from './createModuleScanStore';

export interface CSTemplate {
  id: string;
  label: string;
  description: string;
}

export interface CodeStudioRow {
  id: string;
  name: string;
  owner: string;
  templateId: string;
  templateLabel: string;
  libName: string;
  state: string | null;
}

export interface ProjectWithCodeStudios {
  projectKey: string;
  codeStudios: CodeStudioRow[];
}

export interface CSTemplateData {
  projects: ProjectWithCodeStudios[];
  templates: CSTemplate[];
}

export const csTemplateScan = createModuleScanStore<CSTemplateData, never>({
  loadingField: 'csTemplateReplacementLoading',
  fallbackEndpoint: '/api/cs-template/projects',
});
