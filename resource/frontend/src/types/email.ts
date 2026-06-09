import type { CodeEnvUsageRef } from './codeEnvs';

export interface MailChannel {
  id: string;
  label: string;
}

export type CampaignId =
  | 'project'
  | 'code_env'
  | 'code_studio'
  | 'auto_scenario'
  | 'disabled_user'
  | 'deprecated_code_env'
  | 'default_code_env'
  | 'overshared_project'
  | 'scenario_frequency'
  | 'empty_project'
  | 'large_flow'
  | 'orphan_notebooks'
  | 'scenario_failing'
  | 'inactive_project'
  | 'unused_code_env';

export interface OutreachRecipient {
  recipientKey: string;
  owner: string;
  email: string;
  projectKeys: string[];
  codeEnvNames: string[];
  usageDetails: CodeEnvUsageRef[];
  projectKeyForSend?: string | null;
  projects?: Array<{
    projectKey: string;
    name?: string;
    codeEnvCount?: number;
    codeEnvNames?: string[];
    codeStudioCount?: number;
    autoScenarioCount?: number;
    autoScenarios?: Array<{
      id: string;
      name: string;
      type: string;
      triggerCount: number;
    }>;
    totalGB?: number;
    permissionCount?: number;
    pythonVersion?: string;
    minTriggerMinutes?: number;
    totalObjects?: number;
    notebookCount?: number;
    recipeCount?: number;
    daysInactive?: number;
  }>;
  codeEnvs?: Array<{
    key?: string;
    name?: string;
    language?: string;
    sizeBytes?: number;
    impactedProjects?: string[];
    pythonVersion?: string;
  }>;
  details?: Record<string, unknown>;
}

export interface EmailTemplate {
  subject: string;
  body: string;
}

export interface EmailPreviewItem {
  recipientKey: string;
  owner: string;
  to: string;
  projectKeys: string[];
  codeEnvNames: string[];
  projectKeyForSend?: string | null;
  objectCount: number;
  subject: string;
  body: string;
  usageDetails?: CodeEnvUsageRef[];
}

export interface EmailPreviewResponse {
  campaign: CampaignId;
  template: EmailTemplate;
  previews: EmailPreviewItem[];
  count: number;
}

export interface EmailSendResultItem {
  recipientKey: string;
  to: string;
  projectKeyForSend: string;
  status: 'sent' | 'error';
  error?: string;
}

export interface EmailSendResponse {
  campaign: CampaignId;
  channelId: string;
  requestedCount: number;
  sentCount: number;
  results: EmailSendResultItem[];
}
