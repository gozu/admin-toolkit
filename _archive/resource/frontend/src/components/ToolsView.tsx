import { Fragment, lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AnimatePresence, motion } from 'framer-motion';
import { Container } from './Container';
import { Modal } from './Modal';
import { useDiag } from '../context/DiagContext';
import { useModal, useUltraWideLayout } from '../hooks';
import { useThresholds } from '../hooks/useThresholds';
import { fetchJson } from '../utils/api';
import { fetchWithSessionCache } from '../state/sessionCache';
import { loadFromStorage, saveToStorage } from '../utils/storage';
import type {
  CampaignExemption,
  CampaignId,
  CodeEnv,
  CodeEnvUsageRef,
  EmailPreviewItem,
  EmailPreviewResponse,
  EmailSendResponse,
  EmailTemplate,
  OutreachData,
  OutreachRecipient,
  ParsedData,
  ProjectFootprintRow,
} from '../types';

const PluginComparator = lazy(() =>
  import('./PluginComparator').then((m) => ({ default: m.PluginComparator })),
);
const InactiveProjectCleaner = lazy(() =>
  import('./InactiveProjectCleaner').then((m) => ({ default: m.InactiveProjectCleaner })),
);


// ── Campaign configuration ──

interface CampaignConfig {
  id: CampaignId;
  title: string;
  description: string;
  summaryLabel: string;
  summaryKey: keyof OutreachData['summary'];
  recipientsKey: string;
}

export const CAMPAIGN_CONFIGS: CampaignConfig[] = [
  {
    id: 'project',
    title: 'Code Env Sprawl',
    description: 'Warn owners of projects with too many code environments.',
    summaryLabel: 'Unhealthy Projects',
    summaryKey: 'unhealthyProjectCount',
    recipientsKey: 'projectRecipients',
  },
  {
    id: 'code_env',
    title: 'Code Env Ownership',
    description: 'Warn project owners using code envs they do not own.',
    summaryLabel: 'Unhealthy Code Envs',
    summaryKey: 'unhealthyCodeEnvCount',
    recipientsKey: 'codeEnvRecipients',
  },
  {
    id: 'code_studio',
    title: 'Code Studio Sprawl',
    description: 'Warn owners of projects with too many Code Studios.',
    summaryLabel: 'Code Studio Projects',
    summaryKey: 'unhealthyCodeStudioProjectCount',
    recipientsKey: 'codeStudioRecipients',
  },
  {
    id: 'auto_scenario',
    title: 'Auto-Start Scenarios',
    description: 'Notify owners of projects with auto-start scenarios.',
    summaryLabel: 'Auto-Start Scenarios',
    summaryKey: 'autoScenarioCount',
    recipientsKey: 'autoScenarioRecipients',
  },
  {
    id: 'disabled_user',
    title: 'Projects owned by disabled users',
    description: 'Projects owned by disabled user accounts that need reassignment.',
    summaryLabel: 'Projects owned by disabled users',
    summaryKey: 'disabledUserProjectCount',
    recipientsKey: 'disabledUserRecipients',
  },
  {
    id: 'deprecated_code_env',
    title: 'Deprecated Python Versions',
    description: 'Code environments using deprecated Python versions (2.x, 3.6, 3.7).',
    summaryLabel: 'Deprecated Code Envs',
    summaryKey: 'deprecatedCodeEnvCount',
    recipientsKey: 'deprecatedCodeEnvRecipients',
  },
  {
    id: 'default_code_env',
    title: 'Missing Default Code Env',
    description: 'Projects with code envs but no default Python environment configured.',
    summaryLabel: 'Missing Default Env',
    summaryKey: 'defaultCodeEnvMissingCount',
    recipientsKey: 'defaultCodeEnvRecipients',
  },
  {
    id: 'overshared_project',
    title: 'Overshared Projects',
    description: 'Projects with excessive permission entries (>20).',
    summaryLabel: 'Overshared Projects',
    summaryKey: 'oversharedProjectCount',
    recipientsKey: 'oversharedProjectRecipients',
  },
  {
    id: 'scenario_frequency',
    title: 'High-Frequency Scenarios',
    description: 'Scenarios running more often than every 30 minutes.',
    summaryLabel: 'High-Freq Scenarios',
    summaryKey: 'scenarioFrequencyCount',
    recipientsKey: 'scenarioFrequencyRecipients',
  },
  {
    id: 'empty_project',
    title: 'Empty Projects',
    description: 'Projects with no code envs, no code studios, and minimal data.',
    summaryLabel: 'Empty Projects',
    summaryKey: 'emptyProjectCount',
    recipientsKey: 'emptyProjectRecipients',
  },
  {
    id: 'large_flow',
    title: 'Large Flow Projects',
    description: 'Projects with very large flows (100+ objects).',
    summaryLabel: 'Large Flow Projects',
    summaryKey: 'largeFlowProjectCount',
    recipientsKey: 'largeFlowRecipients',
  },
  {
    id: 'orphan_notebooks',
    title: 'Orphan Notebooks',
    description: 'Projects with many notebooks but few recipes.',
    summaryLabel: 'Orphan Notebook Projects',
    summaryKey: 'orphanNotebookProjectCount',
    recipientsKey: 'orphanNotebookRecipients',
  },
  {
    id: 'scenario_failing',
    title: 'Failing Scenarios',
    description: 'Scenarios whose last run failed or was aborted.',
    summaryLabel: 'Failing Scenarios',
    summaryKey: 'scenarioFailingCount',
    recipientsKey: 'scenarioFailingRecipients',
  },
  {
    id: 'inactive_project',
    title: 'Inactive Projects',
    description: 'Projects inactive for 180+ days with no active scenarios or deployed bundles.',
    summaryLabel: 'Inactive Projects',
    summaryKey: 'inactiveProjectCount',
    recipientsKey: 'inactiveProjectRecipients',
  },
  {
    id: 'unused_code_env',
    title: 'Unused Code Envs',
    description: 'Email owners of code environments with zero usages.',
    summaryLabel: 'Unused Code Envs',
    summaryKey: 'unusedCodeEnvCount',
    recipientsKey: 'unusedCodeEnvRecipients',
  },
];

// ── Helpers ──

function dedupeStrings(values: string[]): string[] {
  return Array.from(new Set(values.filter((v) => !!v)));
}

function dedupeUsageDetails(items: CodeEnvUsageRef[]): CodeEnvUsageRef[] {
  const seen = new Set<string>();
  const out: CodeEnvUsageRef[] = [];
  for (const item of items) {
    const key = [
      item.projectKey || '',
      item.usageType || '',
      item.objectType || '',
      item.objectId || '',
      item.codeEnvName || '',
      item.codeEnvLanguage || '',
    ].join('|');
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out;
}

function defaultTemplates(): OutreachData['templates'] {
  return {
    project: {
      subject: '[DSS Health] Please reduce code environments in your projects',
      body: `Hi {{owner}},

DSS health checks flagged that some of your projects use too many code environments.
Please keep one code environment per project unless absolutely necessary.

{{project_env_list}}

Thanks.`,
    },
    code_env: {
      subject: '[DSS Health] Code environment ownership mismatch in your projects',
      body: `Hi {{owner}},

DSS health checks flagged code environments in your projects that are owned by other users.
Project owners should own their project code environments (ideally one per project) so changes do not break other projects.

Impacted projects:
{{project_list}}

Code environments not owned by you:
{{code_env_list}}

Detected objects:
{{objects_list}}

Thanks.`,
    },
    code_studio: {
      subject: '[DSS Health] Too many Code Studios in your projects',
      body: `Hi {{owner}},

DSS health checks flagged that some of your projects have too many Code Studios.
Please consolidate or remove unused Code Studios to reduce resource consumption.

Projects with excessive Code Studios:
{{code_studio_list}}

Thanks.`,
    },
    auto_scenario: {
      subject: '[DSS Health] Review auto-start scenarios in your projects',
      body: `Hi {{owner}},

DSS health checks found scenarios set to automatically start in your projects.
Please review these scenarios to ensure they are still needed and properly configured.

Projects and auto-start scenarios:
{{scenario_list}}

Thanks.`,
    },
    disabled_user: {
      subject: '[DSS Health] Projects owned by disabled users need reassignment',
      body: `Hi admin,

The following projects are owned by disabled user accounts.
Please reassign ownership to active users.

Projects owned by disabled users:
{{project_list}}

Thanks.`,
    },
    deprecated_code_env: {
      subject: '[DSS Health] Deprecated Python versions in your code environments',
      body: `Hi {{owner}},

Some of your code environments use deprecated Python versions (2.x, 3.6, or 3.7).
Please upgrade to a supported Python version.

Code environments:
{{code_env_list}}

Impacted projects:
{{project_list}}

Thanks.`,
    },
    default_code_env: {
      subject: '[DSS Health] Projects missing default code environment',
      body: `Hi {{owner}},

Some of your projects use code environments but have no default Python environment.
Setting a default code environment prevents unexpected version conflicts.

Projects:
{{project_list}}

Thanks.`,
    },
    overshared_project: {
      subject: '[DSS Health] Projects with excessive permissions',
      body: `Hi {{owner}},

Some of your projects have a large number of permission entries.
Please review and consolidate permissions using groups where possible.

Projects:
{{project_list}}

Thanks.`,
    },
    scenario_frequency: {
      subject: '[DSS Health] High-frequency scenarios in your projects',
      body: `Hi {{owner}},

Some scenarios in your projects run very frequently (under 30 minutes).
Please review whether this frequency is necessary.

Projects and scenarios:
{{scenario_list}}

Thanks.`,
    },
    empty_project: {
      subject: '[DSS Health] Empty projects that may need cleanup',
      body: `Hi {{owner}},

Some of your projects appear to be empty or unused.
Please archive or delete projects that are no longer needed.

Projects:
{{project_list}}

Thanks.`,
    },
    large_flow: {
      subject: '[DSS Health] Projects with large flows',
      body: `Hi {{owner}},

Some of your projects have very large flows with many objects.
Consider splitting large flows into smaller, focused projects.

Projects:
{{project_list}}

Thanks.`,
    },
    orphan_notebooks: {
      subject: '[DSS Health] Projects with many notebooks but few recipes',
      body: `Hi {{owner}},

Some of your projects have many notebooks but few recipes.
Consider converting mature notebooks into recipes for production use.

Projects:
{{project_list}}

Thanks.`,
    },
    scenario_failing: {
      subject: '[DSS Health] Failing scenarios in your projects',
      body: `Hi {{owner}},

Some scenarios in your projects have failed in their last run.
Please investigate and fix the failing scenarios.

Projects and failing scenarios:
{{scenario_list}}

Thanks.`,
    },
    inactive_project: {
      subject: '[DSS Health] Inactive projects that may need cleanup',
      body: `Hi {{owner}},

Some of your projects have been inactive for a long time.
A project is considered inactive when it has no recent modifications, no active scenarios, and no deployed bundles.

Please delete or archive projects that are no longer needed to keep the instance clean.

Inactive projects:
{{inactive_project_list}}

Thanks.`,
    },
    unused_code_env: {
      subject: '[DSS Health] Unused code environments you own',
      body: `Hi {{owner}},

Some code environments you own have zero usages across all projects.
Please delete code environments that are no longer needed to free up resources.

Unused code environments:
{{code_env_list}}

Thanks.`,
    },
  };
}

interface OutreachThresholds {
  codeEnvCountUnhealthy: number;
  codeStudioCountUnhealthy: number;
}

function finalizeRecipients(recipientsMap: Map<string, OutreachRecipient>): OutreachRecipient[] {
  return Array.from(recipientsMap.values())
    .map((recipient) => {
      const projectKeys = dedupeStrings(recipient.projectKeys).sort();
      return {
        ...recipient,
        projectKeys,
        codeEnvNames: dedupeStrings(recipient.codeEnvNames).sort(),
        usageDetails: dedupeUsageDetails(recipient.usageDetails),
        projectKeyForSend: projectKeys[0] || null,
      };
    })
    .sort((a, b) => b.projectKeys.length - a.projectKeys.length);
}

function makeRecipient(owner: string, emailByOwner: Map<string, string>): OutreachRecipient {
  return {
    recipientKey: owner,
    owner,
    email: emailByOwner.get(owner) || owner,
    projectKeys: [],
    codeEnvNames: [],
    usageDetails: [],
    projects: [],
  };
}

function buildOutreachDataFromParsedData(
  parsedData: ParsedData,
  thresholds?: OutreachThresholds,
): OutreachData | null {
  const codeEnvThreshold = thresholds?.codeEnvCountUnhealthy ?? 1;
  const codeStudioThreshold = thresholds?.codeStudioCountUnhealthy ?? 7;
  const projectRows = parsedData.projectFootprint || [];
  const codeEnvs = parsedData.codeEnvs || [];
  if (projectRows.length === 0 && codeEnvs.length === 0) return null;

  const users = parsedData.users || [];
  const emailByOwner = new Map<string, string>();
  const disabledLogins = new Set<string>();
  for (const user of users) {
    if (!user?.login) continue;
    emailByOwner.set(String(user.login), String(user.email || user.login));
    if (user.enabled === false) {
      disabledLogins.add(String(user.login));
    }
  }

  const unhealthyProjects: ProjectFootprintRow[] = projectRows.filter(
    (row) => Number(row.codeEnvCount || 0) > codeEnvThreshold,
  );
  const unhealthyProjectKeys = new Set(
    unhealthyProjects.map((row) => String(row.projectKey || '')),
  );

  const codeEnvByKey = new Map<string, CodeEnv>();
  for (const env of codeEnvs) {
    const envKey = `${String(env.language || 'python')}:${String(env.name || '')}`;
    codeEnvByKey.set(envKey, env);
  }

  const codeEnvUsageByProject = new Map<string, CodeEnvUsageRef[]>();
  const codeEnvNamesByProject = new Map<string, Set<string>>();
  const unhealthyCodeEnvs: CodeEnv[] = [];

  for (const env of codeEnvs) {
    const envName = String(env.name || '');
    const usageDetails = (env.usageDetails || []).filter((usage) =>
      unhealthyProjectKeys.has(String(usage.projectKey || '')),
    );
    if (usageDetails.length === 0) continue;
    unhealthyCodeEnvs.push({ ...env, usageDetails });
    for (const usage of usageDetails) {
      const projectKey = String(usage.projectKey || '');
      if (!projectKey) continue;
      if (!codeEnvUsageByProject.has(projectKey)) codeEnvUsageByProject.set(projectKey, []);
      codeEnvUsageByProject.get(projectKey)!.push({
        ...usage,
        codeEnvName: usage.codeEnvName || envName,
        codeEnvLanguage: usage.codeEnvLanguage || env.language,
        codeEnvOwner: usage.codeEnvOwner || env.owner,
      });
      if (!codeEnvNamesByProject.has(projectKey))
        codeEnvNamesByProject.set(projectKey, new Set<string>());
      codeEnvNamesByProject.get(projectKey)!.add(envName);
    }
  }

  // Fallback path: if code env usage details are sparse in codeEnvs payload, build
  // unhealthy code env entries from project footprint rows (which already contain
  // codeEnvKeys and usageDetails).
  if (unhealthyCodeEnvs.length === 0 && unhealthyProjects.length > 0) {
    const envAccumulator = new Map<
      string,
      { env: CodeEnv; usageDetails: CodeEnvUsageRef[]; impacted: Set<string> }
    >();
    for (const row of unhealthyProjects) {
      const rowAny = row as unknown as { codeEnvKeys?: string[]; usageDetails?: CodeEnvUsageRef[] };
      const projectKey = String(row.projectKey || '');
      const envKeys = (rowAny.codeEnvKeys || []).map((k) => String(k || '')).filter(Boolean);
      const usageDetails = (rowAny.usageDetails || []).filter((u) => !!u);
      for (const envKey of envKeys) {
        const env = codeEnvByKey.get(envKey);
        const envNameFromKey = envKey.includes(':') ? envKey.split(':').slice(1).join(':') : envKey;
        const base: CodeEnv = env || {
          name: envNameFromKey,
          language: (envKey.split(':', 1)[0] as 'python' | 'r') || 'python',
          version: 'Unknown',
          owner: 'Unknown',
          sizeBytes: 0,
        };
        const bucket = envAccumulator.get(envKey) || {
          env: base,
          usageDetails: [],
          impacted: new Set<string>(),
        };
        bucket.impacted.add(projectKey);
        for (const usage of usageDetails) {
          const usageEnvKey =
            usage.codeEnvKey ||
            (usage.codeEnvName ? `${usage.codeEnvLanguage || 'python'}:${usage.codeEnvName}` : '');
          if (usageEnvKey === envKey || (!usageEnvKey && usage.projectKey === projectKey)) {
            bucket.usageDetails.push({
              ...usage,
              codeEnvKey: usage.codeEnvKey || envKey,
              codeEnvName: usage.codeEnvName || base.name,
              codeEnvLanguage: usage.codeEnvLanguage || base.language,
              codeEnvOwner: usage.codeEnvOwner || base.owner,
            });
          }
        }
        envAccumulator.set(envKey, bucket);
      }
    }
    for (const [envKey, bucket] of envAccumulator.entries()) {
      const mergedUsage = dedupeUsageDetails(bucket.usageDetails);
      const hasSignal = mergedUsage.length > 0 || bucket.impacted.size > 0;
      if (!hasSignal) continue;
      unhealthyCodeEnvs.push({
        ...bucket.env,
        usageDetails: mergedUsage,
        projectKeys: Array.from(bucket.impacted),
      });
      for (const projectKey of bucket.impacted) {
        if (!codeEnvNamesByProject.has(projectKey))
          codeEnvNamesByProject.set(projectKey, new Set<string>());
        codeEnvNamesByProject.get(projectKey)!.add(String(bucket.env.name || envKey));
        // Also populate codeEnvUsageByProject so recipients get usageDetails
        const projectUsages = mergedUsage.filter(
          (u) => String(u.projectKey || '') === projectKey,
        );
        if (projectUsages.length > 0) {
          if (!codeEnvUsageByProject.has(projectKey))
            codeEnvUsageByProject.set(projectKey, []);
          codeEnvUsageByProject.get(projectKey)!.push(...projectUsages);
        }
      }
    }
  }

  const projectRecipientsMap = new Map<string, OutreachRecipient>();
  for (const row of unhealthyProjects) {
    const owner = String(row.owner || 'Unknown');
    if (!projectRecipientsMap.has(owner))
      projectRecipientsMap.set(owner, makeRecipient(owner, emailByOwner));
    const recipient = projectRecipientsMap.get(owner)!;
    const projectKey = String(row.projectKey || '');
    if (projectKey) recipient.projectKeys.push(projectKey);
    const names = Array.from(codeEnvNamesByProject.get(projectKey) || []);
    recipient.codeEnvNames.push(...names);
    recipient.usageDetails.push(...(codeEnvUsageByProject.get(projectKey) || []));
    recipient.projects = [
      ...(recipient.projects || []),
      { projectKey, name: row.name, codeEnvCount: row.codeEnvCount, totalGB: row.totalGB },
    ];
  }
  const projectRecipients = finalizeRecipients(projectRecipientsMap);

  // Code Env Ownership recipients: group by project owner, include only
  // code envs where the env owner != project owner (ownership mismatch).
  // Mirrors backend.py lines 6230-6288.
  const codeEnvRecipientsMap = new Map<string, OutreachRecipient>();
  for (const row of unhealthyProjects) {
    const projectOwner = String(row.owner || 'Unknown');
    const projectOwnerKey = projectOwner.trim().toLowerCase();
    const projectKey = String(row.projectKey || '');
    const rowUsageDetails = codeEnvUsageByProject.get(projectKey) || [];

    const mismatchedUsage: CodeEnvUsageRef[] = [];
    const mismatchedEnvNames = new Set<string>();
    for (const usage of rowUsageDetails) {
      const envName = String(usage.codeEnvName || '').trim();
      const envOwner = String(usage.codeEnvOwner || 'Unknown').trim();
      if (!envName) continue;
      if (envOwner.toLowerCase() === projectOwnerKey) continue;
      mismatchedUsage.push(usage);
      mismatchedEnvNames.add(envName);
    }
    if (mismatchedUsage.length === 0) continue;

    if (!codeEnvRecipientsMap.has(projectOwner))
      codeEnvRecipientsMap.set(projectOwner, makeRecipient(projectOwner, emailByOwner));
    const recipient = codeEnvRecipientsMap.get(projectOwner)!;
    if (projectKey) recipient.projectKeys.push(projectKey);
    recipient.codeEnvNames.push(...Array.from(mismatchedEnvNames).sort());
    recipient.usageDetails.push(...mismatchedUsage);
    recipient.projects = [
      ...(recipient.projects || []),
      { projectKey, name: row.name, codeEnvCount: mismatchedEnvNames.size },
    ];
  }
  const codeEnvRecipients = Array.from(codeEnvRecipientsMap.values())
    .map((r) => {
      const pkeys = dedupeStrings(r.projectKeys).sort();
      return {
        ...r,
        projectKeys: pkeys,
        codeEnvNames: dedupeStrings(r.codeEnvNames).sort(),
        usageDetails: dedupeUsageDetails(r.usageDetails),
        projectKeyForSend: pkeys[0] || null,
      };
    })
    .sort((a, b) => b.codeEnvNames.length - a.codeEnvNames.length);

  unhealthyCodeEnvs.sort((a, b) => Number(b.sizeBytes || 0) - Number(a.sizeBytes || 0));

  // Code Studio recipients
  const unhealthyCodeStudioProjects: ProjectFootprintRow[] = projectRows.filter(
    (row) => Number(row.codeStudioCount || 0) > codeStudioThreshold,
  );
  const codeStudioRecipientsMap = new Map<string, OutreachRecipient>();
  for (const row of unhealthyCodeStudioProjects) {
    const owner = String(row.owner || 'Unknown');
    if (!codeStudioRecipientsMap.has(owner))
      codeStudioRecipientsMap.set(owner, makeRecipient(owner, emailByOwner));
    const recipient = codeStudioRecipientsMap.get(owner)!;
    const projectKey = String(row.projectKey || '');
    if (projectKey) recipient.projectKeys.push(projectKey);
    recipient.projects = [
      ...(recipient.projects || []),
      { projectKey, name: row.name, codeStudioCount: row.codeStudioCount, totalGB: row.totalGB },
    ];
  }
  const codeStudioRecipients = finalizeRecipients(codeStudioRecipientsMap);

  // ── New ZIP-based campaigns ──

  // Disabled user projects
  const disabledUserRecipientsMap = new Map<string, OutreachRecipient>();
  let disabledUserProjectCount = 0;
  for (const row of projectRows) {
    const owner = String(row.owner || 'Unknown');
    if (!disabledLogins.has(owner)) continue;
    disabledUserProjectCount++;
    if (!disabledUserRecipientsMap.has(owner))
      disabledUserRecipientsMap.set(owner, makeRecipient(owner, emailByOwner));
    const recipient = disabledUserRecipientsMap.get(owner)!;
    const projectKey = String(row.projectKey || '');
    if (projectKey) recipient.projectKeys.push(projectKey);
    recipient.projects = [
      ...(recipient.projects || []),
      { projectKey, name: row.name, totalGB: row.totalGB },
    ];
  }
  const disabledUserRecipients = finalizeRecipients(disabledUserRecipientsMap);

  // Deprecated code envs
  const deprecatedCodeEnvRecipientsMap = new Map<string, OutreachRecipient>();
  let deprecatedCodeEnvCount = 0;
  for (const env of codeEnvs) {
    const v = String(env.version || '');
    if (!v.startsWith('2.') && !v.startsWith('3.6') && !v.startsWith('3.7')) continue;
    deprecatedCodeEnvCount++;
    const owner = String(env.owner || 'Unknown');
    if (!deprecatedCodeEnvRecipientsMap.has(owner)) {
      deprecatedCodeEnvRecipientsMap.set(owner, {
        ...makeRecipient(owner, emailByOwner),
        codeEnvs: [],
      });
    }
    const recipient = deprecatedCodeEnvRecipientsMap.get(owner)!;
    recipient.codeEnvNames.push(String(env.name || ''));
    const impacted = (env.projectKeys || []).map(String);
    recipient.projectKeys.push(...impacted);
    recipient.codeEnvs = [
      ...(recipient.codeEnvs || []),
      {
        key: `${env.language}:${env.name}`,
        name: env.name,
        language: env.language,
        pythonVersion: v,
        impactedProjects: impacted,
      },
    ];
  }
  const deprecatedCodeEnvRecipients = Array.from(deprecatedCodeEnvRecipientsMap.values())
    .map((r) => {
      const pkeys = dedupeStrings(r.projectKeys).sort();
      return {
        ...r,
        projectKeys: pkeys,
        codeEnvNames: dedupeStrings(r.codeEnvNames).sort(),
        projectKeyForSend: pkeys[0] || null,
      };
    })
    .sort((a, b) => b.codeEnvNames.length - a.codeEnvNames.length);

  // Overshared projects (frontend-only: parsedData.projects has permissions)
  const oversharedProjectRecipientsMap = new Map<string, OutreachRecipient>();
  let oversharedProjectCount = 0;
  const projects = parsedData.projects || [];
  for (const proj of projects) {
    const permCount = (proj.permissions || []).length;
    if (permCount <= 20) continue;
    oversharedProjectCount++;
    const owner = String(proj.owner || 'Unknown');
    if (!oversharedProjectRecipientsMap.has(owner))
      oversharedProjectRecipientsMap.set(owner, makeRecipient(owner, emailByOwner));
    const recipient = oversharedProjectRecipientsMap.get(owner)!;
    recipient.projectKeys.push(String(proj.key || ''));
    recipient.projects = [
      ...(recipient.projects || []),
      { projectKey: proj.key, name: proj.name, permissionCount: permCount },
    ];
  }
  const oversharedProjectRecipients = finalizeRecipients(oversharedProjectRecipientsMap);

  // Empty projects
  const emptyProjectRecipientsMap = new Map<string, OutreachRecipient>();
  let emptyProjectCount = 0;
  for (const row of projectRows) {
    const codeEnvCount = Number(row.codeEnvCount || 0);
    const codeStudioCount = Number(row.codeStudioCount || 0);
    const totalBytes = Number(row.totalBytes || 0);
    const breakdown = row.usageBreakdown || {};
    const totalObjects = Object.values(breakdown).reduce((sum, v) => sum + Number(v || 0), 0);
    if (codeEnvCount > 0 || codeStudioCount > 0 || totalBytes > 1048576 || totalObjects > 0)
      continue;
    emptyProjectCount++;
    const owner = String(row.owner || 'Unknown');
    if (!emptyProjectRecipientsMap.has(owner))
      emptyProjectRecipientsMap.set(owner, makeRecipient(owner, emailByOwner));
    const recipient = emptyProjectRecipientsMap.get(owner)!;
    const projectKey = String(row.projectKey || '');
    if (projectKey) recipient.projectKeys.push(projectKey);
    recipient.projects = [
      ...(recipient.projects || []),
      { projectKey, name: row.name, totalGB: row.totalGB },
    ];
  }
  const emptyProjectRecipients = finalizeRecipients(emptyProjectRecipientsMap);

  // Unused code envs — cross-reference against project footprint codeEnvKeys.
  // NOTE: env.usageCount from /api/code-envs is always 0 (loaded with include_usages=False),
  // so we compute "used" by collecting all env keys referenced by any project footprint row.
  const usedEnvKeys = new Set<string>();
  for (const row of projectRows) {
    const rowAny = row as unknown as { codeEnvKeys?: string[] };
    for (const ek of rowAny.codeEnvKeys || []) {
      if (ek) usedEnvKeys.add(String(ek));
    }
  }
  const unusedCodeEnvRecipientsMap = new Map<string, OutreachRecipient>();
  let unusedCodeEnvCount = 0;
  for (const env of codeEnvs) {
    const envKey = `${env.language}:${env.name}`;
    if (usedEnvKeys.has(envKey)) continue;
    unusedCodeEnvCount++;
    const owner = String(env.owner || 'Unknown');
    if (!unusedCodeEnvRecipientsMap.has(owner)) {
      unusedCodeEnvRecipientsMap.set(owner, {
        ...makeRecipient(owner, emailByOwner),
        codeEnvs: [],
      });
    }
    const recipient = unusedCodeEnvRecipientsMap.get(owner)!;
    recipient.codeEnvNames.push(String(env.name || ''));
    recipient.codeEnvs = [
      ...(recipient.codeEnvs || []),
      {
        key: envKey,
        name: env.name,
        language: env.language,
      },
    ];
  }
  const unusedCodeEnvRecipients = Array.from(unusedCodeEnvRecipientsMap.values())
    .map((r) => ({
      ...r,
      codeEnvNames: dedupeStrings(r.codeEnvNames).sort(),
    }))
    .sort((a, b) => b.codeEnvNames.length - a.codeEnvNames.length);

  return {
    summary: {
      projectCount: projectRows.length,
      unhealthyProjectCount: unhealthyProjects.length,
      unhealthyCodeEnvCount: unhealthyCodeEnvs.length,
      unhealthyCodeStudioProjectCount: unhealthyCodeStudioProjects.length,
      autoScenarioCount: 0,
      projectRecipientCount: projectRecipients.length,
      codeEnvRecipientCount: codeEnvRecipients.length,
      codeStudioRecipientCount: codeStudioRecipients.length,
      autoScenarioRecipientCount: 0,
      disabledUserProjectCount,
      deprecatedCodeEnvCount,
      defaultCodeEnvMissingCount: 0,
      oversharedProjectCount,
      scenarioFrequencyCount: 0,
      emptyProjectCount,
      largeFlowProjectCount: 0,
      orphanNotebookProjectCount: 0,
      scenarioFailingCount: 0,
      unusedCodeEnvCount,
    },
    mailChannels: [],
    templates: defaultTemplates(),
    unhealthyProjects,
    unhealthyCodeEnvs,
    unhealthyCodeStudioProjects,
    projectRecipients,
    codeEnvRecipients,
    codeStudioRecipients,
    autoScenarioRecipients: [],
    disabledUserRecipients,
    deprecatedCodeEnvRecipients: deprecatedCodeEnvRecipients,
    defaultCodeEnvRecipients: [],
    oversharedProjectRecipients,
    scenarioFrequencyRecipients: [],
    emptyProjectRecipients,
    largeFlowRecipients: [],
    orphanNotebookRecipients: [],
    scenarioFailingRecipients: [],
    unusedCodeEnvRecipients,
  };
}

// ── Sin extraction (shared between CampaignPanel and ToolsView) ──

interface Sin {
  key: string;
  label: string;
  details: string;
  children?: string[];
}

function getRecipientSins(recipient: OutreachRecipient): Sin[] {
  if (recipient.projects && recipient.projects.length > 0) {
    const envsByProject = new Map<string, string[]>();
    for (const u of recipient.usageDetails || []) {
      if (u.projectKey && u.codeEnvName) {
        const list = envsByProject.get(u.projectKey) || [];
        if (!list.includes(u.codeEnvName)) list.push(u.codeEnvName);
        envsByProject.set(u.projectKey, list);
      }
    }
    return recipient.projects.map((p) => ({
      key: p.projectKey,
      label: p.name || p.projectKey,
      details: [
        p.codeEnvCount != null ? `${p.codeEnvCount} code envs` : null,
        p.codeStudioCount != null ? `${p.codeStudioCount} studios` : null,
        p.totalGB != null ? `${p.totalGB} GB` : null,
        p.permissionCount != null ? `${p.permissionCount} perms` : null,
        p.daysInactive != null ? `${p.daysInactive}d inactive` : null,
        p.totalObjects != null ? `${p.totalObjects} objects` : null,
        p.notebookCount != null ? `${p.notebookCount} notebooks` : null,
        p.minTriggerMinutes != null ? `every ${p.minTriggerMinutes}m` : null,
      ].filter(Boolean).join(', '),
      children: envsByProject.get(p.projectKey)?.sort() || (p.codeEnvNames?.length ? [...p.codeEnvNames].sort() : []),
    }));
  }
  if (recipient.codeEnvs && recipient.codeEnvs.length > 0) {
    return recipient.codeEnvs.map((ce) => ({
      key: ce.key || ce.name || '',
      label: ce.name || ce.key || 'unknown',
      details: [
        ce.pythonVersion ? `Python ${ce.pythonVersion}` : null,
        ce.sizeBytes ? `${(ce.sizeBytes / 1e9).toFixed(1)} GB` : null,
        ce.impactedProjects?.length ? `${ce.impactedProjects.length} projects` : null,
      ].filter(Boolean).join(', '),
    }));
  }
  return recipient.projectKeys.map((k) => ({ key: k, label: k, details: '' }));
}

// ── Campaign Panel ──

interface CampaignPanelProps {
  title: string;
  description: string;
  recipients: OutreachRecipient[];
  selectedKeys: string[];
  setSelectedKeys: (next: string[]) => void;
  template: EmailTemplate;
  setTemplate: (template: EmailTemplate) => void;
  onPreview: () => void;
  previewDisabled: boolean;
  defaultCollapsed?: boolean;
  disabled?: boolean;
  onExempt?: (entityKey: string) => void;
  onUnexempt?: (entityKey: string) => void;
  isEntityExempt?: (entityKey: string) => boolean;
  alwaysExpanded?: boolean;
  hideProjects?: boolean;
  hideCodeEnvs?: boolean;
  hideObjects?: boolean;
}

function CampaignPanel({
  title,
  description,
  recipients,
  selectedKeys,
  setSelectedKeys,
  template,
  setTemplate,
  onPreview,
  previewDisabled,
  defaultCollapsed,
  disabled,
  onExempt,
  onUnexempt,
  isEntityExempt,
  alwaysExpanded,
  hideProjects,
  hideCodeEnvs,
  hideObjects,
}: CampaignPanelProps) {
  const [collapsed, setCollapsed] = useState(alwaysExpanded ? false : (defaultCollapsed ?? false));
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const selectedSet = useMemo(() => new Set(selectedKeys), [selectedKeys]);

  const toggle = (recipientKey: string) => {
    if (selectedSet.has(recipientKey)) {
      setSelectedKeys(selectedKeys.filter((key) => key !== recipientKey));
      return;
    }
    setSelectedKeys([...selectedKeys, recipientKey]);
  };

  const toggleExpand = (recipientKey: string) => {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(recipientKey)) next.delete(recipientKey);
      else next.add(recipientKey);
      return next;
    });
  };

  const allKeys = recipients.map((recipient) => recipient.recipientKey);
  const allSelected = allKeys.length > 0 && allKeys.every((key) => selectedSet.has(key));

  // Check if all sins for a recipient are exempt
  const isRecipientFullyExempt = (recipient: OutreachRecipient) => {
    if (!isEntityExempt) return false;
    const sins = getRecipientSins(recipient);
    return sins.length > 0 && sins.every((s) => isEntityExempt(s.key));
  };

  // Count of non-exempt sins for a recipient
  const getActiveSinCount = (recipient: OutreachRecipient) => {
    if (!isEntityExempt) return null;
    const sins = getRecipientSins(recipient);
    const active = sins.filter((s) => !isEntityExempt(s.key)).length;
    if (active === sins.length) return null; // no exemptions — don't show
    return { active, total: sins.length };
  };

  return (
    <section className="glass-card p-4 flex flex-col gap-4 flex-1 min-h-0">
      <div
        className={`flex flex-wrap items-start justify-between gap-3 ${alwaysExpanded ? '' : 'cursor-pointer select-none'}`}
        onClick={alwaysExpanded ? undefined : () => setCollapsed(!collapsed)}
      >
        <div className="flex items-center gap-2">
          {!alwaysExpanded && (
            <span className="text-[var(--text-muted)] text-sm">
              {collapsed ? '\u25B6' : '\u25BC'}
            </span>
          )}
          <div>
            <h3 className="text-lg font-semibold text-[var(--text-primary)]">
              {title}
              {disabled && (
                <span className="ml-2 inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-amber-500/15 text-amber-400">
                  Disabled
                </span>
              )}
              {!disabled && (
                <span className="ml-2 text-sm font-normal text-[var(--text-muted)]">
                  ({recipients.length})
                </span>
              )}
            </h3>
            <p className="text-sm text-[var(--text-muted)]">{description}</p>
          </div>
        </div>
        <div className="text-sm text-[var(--text-secondary)]">
          <span className="font-mono">{selectedKeys.length}</span> / {recipients.length} selected
        </div>
      </div>

      {!collapsed && disabled && (
        <div className="py-3 px-1 text-sm text-[var(--text-muted)] italic">
          This campaign is disabled. Enable it in Settings to send emails and track findings.
        </div>
      )}

      {!collapsed && !disabled && (
        <>
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => setSelectedKeys(allSelected ? [] : allKeys)}
              className="px-3 py-1.5 rounded text-sm bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-[var(--text-secondary)] transition-colors"
            >
              {allSelected ? 'Clear Selection' : 'Select All'}
            </button>
          </div>

          <div className="overflow-auto flex-1 min-h-0 border border-[var(--border-glass)] rounded-lg">
            <table className="table-dark">
              <thead>
                <tr>
                  <th className="w-10"></th>
                  <th>Owner</th>
                  <th>Email</th>
                  {!hideProjects && <th className="text-right">Projects</th>}
                  {!hideCodeEnvs && <th className="text-right">Code Envs</th>}
                  {!hideObjects && <th className="text-right">Objects</th>}
                  {onExempt && <th className="w-24 text-right">Exempt</th>}
                </tr>
              </thead>
              <tbody>
                {recipients.map((recipient) => {
                  const isExpanded = expandedRows.has(recipient.recipientKey);
                  const sins = getRecipientSins(recipient);
                  const fullyExempt = isRecipientFullyExempt(recipient);
                  const sinCount = getActiveSinCount(recipient);
                  return (
                    <Fragment key={recipient.recipientKey}>
                      <tr
                        className={`cursor-pointer select-none ${fullyExempt ? 'bg-green-500/10' : 'hover:bg-[var(--bg-glass)]'}`}
                        onClick={() => toggleExpand(recipient.recipientKey)}
                      >
                        <td>
                          <input
                            type="checkbox"
                            checked={selectedSet.has(recipient.recipientKey)}
                            onChange={() => toggle(recipient.recipientKey)}
                            onClick={(e) => e.stopPropagation()}
                          />
                        </td>
                        <td className="text-[var(--text-primary)]">
                          <span className="mr-1.5 text-xs text-[var(--text-muted)]">
                            {isExpanded ? '\u25BC' : '\u25B6'}
                          </span>
                          {recipient.owner}
                        </td>
                        <td className="font-mono text-xs text-[var(--text-secondary)]">
                          {recipient.email}
                        </td>
                        {!hideProjects && <td className="text-right font-mono">{recipient.projectKeys.length}</td>}
                        {!hideCodeEnvs && <td className="text-right font-mono">{recipient.codeEnvNames.length}</td>}
                        {!hideObjects && <td className="text-right font-mono">{recipient.usageDetails.length}</td>}
                        {onExempt && (
                          <td className="text-right text-xs">
                            {sinCount && (
                              <span className="text-green-400 font-mono">
                                {sinCount.total - sinCount.active}<span className="text-[var(--text-muted)]">/{sinCount.total}</span>
                              </span>
                            )}
                          </td>
                        )}
                      </tr>
                      {isExpanded && sins.map((sin) => {
                        const exempt = isEntityExempt?.(sin.key) ?? false;
                        return (
                          <Fragment key={`${recipient.recipientKey}:${sin.key}`}>
                            <tr
                              className={exempt ? 'bg-green-500/10' : ''}
                            >
                              <td></td>
                              <td colSpan={5 - (hideProjects ? 1 : 0) - (hideCodeEnvs ? 1 : 0) - (hideObjects ? 1 : 0)} className="pl-8 text-sm">
                                <span className="text-[var(--text-primary)]">{sin.label}</span>
                                {sin.details && (
                                  <span className="ml-2 text-xs text-[var(--text-muted)]">
                                    {sin.details}
                                  </span>
                                )}
                              </td>
                              {onExempt && onUnexempt && (
                                <td>
                                  {exempt ? (
                                    <button
                                      onClick={() => onUnexempt(sin.key)}
                                      className="text-xs px-2 py-0.5 rounded bg-green-500/20 text-green-400 hover:bg-red-500/20 hover:text-red-400 transition-colors"
                                    >
                                      Undo
                                    </button>
                                  ) : (
                                    <button
                                      onClick={() => onExempt(sin.key)}
                                      className="text-xs px-2 py-0.5 rounded text-[var(--text-muted)] hover:text-[var(--neon-amber)] hover:bg-[var(--bg-glass)] transition-colors"
                                    >
                                      Whitelist
                                    </button>
                                  )}
                                </td>
                              )}
                            </tr>
                            {sin.children?.map((child) => (
                              <tr key={`${recipient.recipientKey}:${sin.key}:${child}`}>
                                <td></td>
                                <td colSpan={5 - (hideProjects ? 1 : 0) - (hideCodeEnvs ? 1 : 0) - (hideObjects ? 1 : 0)} className="pl-14 text-xs text-[var(--text-muted)]">
                                  {child}
                                </td>
                                {onExempt && onUnexempt && <td></td>}
                              </tr>
                            ))}
                          </Fragment>
                        );
                      })}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="grid grid-cols-1 gap-3">
            <label className="text-sm text-[var(--text-secondary)]">
              Subject
              <input
                value={template.subject}
                onChange={(event) => setTemplate({ ...template, subject: event.target.value })}
                className="mt-1 w-full input-glass"
              />
            </label>
            <label className="text-sm text-[var(--text-secondary)]">
              Body
              <textarea
                value={template.body}
                onChange={(event) => setTemplate({ ...template, body: event.target.value })}
                className="mt-1 w-full input-glass min-h-[150px] font-mono text-xs"
              />
            </label>
          </div>

          <div className="text-xs text-[var(--text-muted)]">
            Supported variables: <code>{'{{owner}}'}</code>, <code>{'{{project_list}}'}</code>,{' '}
            <code>{'{{code_env_list}}'}</code>, <code>{'{{objects_list}}'}</code>,{' '}
            <code>{'{{project_env_list}}'}</code>, <code>{'{{code_studio_list}}'}</code>,{' '}
            <code>{'{{scenario_list}}'}</code>
          </div>

          <div className="flex justify-end">
            <button
              onClick={onPreview}
              disabled={previewDisabled}
              className="px-4 py-2 rounded btn-primary btn-preview disabled:opacity-50"
            >
              Preview Emails
            </button>
          </div>
        </>
      )}
    </section>
  );
}

// ── Helpers for getting recipients from OutreachData by campaign config ──

function getRecipients(data: OutreachData, config: CampaignConfig): OutreachRecipient[] {
  const key = config.recipientsKey as keyof OutreachData;
  const value = data[key];
  return Array.isArray(value) ? (value as OutreachRecipient[]) : [];
}

function getSummaryCount(data: OutreachData, config: CampaignConfig): number {
  const value = data.summary[config.summaryKey];
  return typeof value === 'number' ? value : 0;
}

// ── Main component ──

export function ToolsView() {
  const { dispatch, state } = useDiag();
  const { ultraWideEnabled } = useUltraWideLayout();
  const previewModal = useModal();
  const { parsedData, activePage } = state;
  const [selectedCampaignId, setSelectedCampaignId] = useState<CampaignId>(
    () => loadFromStorage('selectedCampaignId', CAMPAIGN_CONFIGS[0].id) as CampaignId,
  );
  const { thresholds } = useThresholds();
  const [isLoading, setIsLoading] = useState(!parsedData.outreachData);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<OutreachData | null>(() => parsedData.outreachData ?? null);

  const mailChannels = data?.mailChannels ?? [];
  const selectedChannel = data?.configuredMailChannel
    || mailChannels[0]?.id
    || '';

  // Data-driven state: templates and selections keyed by campaign ID
  const [templates, setTemplates] = useState<Record<string, EmailTemplate>>(() =>
    loadFromStorage('campaignTemplates', {}),
  );
  const [selectedKeys, setSelectedKeys] = useState<Record<string, string[]>>(() =>
    loadFromStorage('campaignSelectedKeys', {}),
  );

  const [previewCampaign, setPreviewCampaign] = useState<CampaignId>('project');
  const [previewItems, setPreviewItems] = useState<EmailPreviewItem[]>([]);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [sendLoading, setSendLoading] = useState(false);
  const [sendResult, setSendResult] = useState<EmailSendResponse | null>(null);
  const [sendAllLoading, setSendAllLoading] = useState(false);
  const [sendAllStatus, setSendAllStatus] = useState<string | null>(null);
  const [showSendAllConfirm, setShowSendAllConfirm] = useState(false);
  const [disabledCampaigns, setDisabledCampaigns] = useState<Set<string>>(new Set());
  const [exemptions, setExemptions] = useState<Map<CampaignId, Map<string, number>>>(new Map());
  const exemptionsRef = useRef(exemptions);
  exemptionsRef.current = exemptions;
  useEffect(() => {
    saveToStorage('selectedCampaignId', selectedCampaignId);
  }, [selectedCampaignId]);

  const log = useCallback(
    (message: string, level: 'info' | 'warn' | 'error' = 'info') => {
      dispatch({ type: 'ADD_DEBUG_LOG', payload: { scope: 'tools-email', level, message } });
    },
    [dispatch],
  );

  // Template getter/setter per campaign
  const getTemplate = useCallback(
    (campaignId: CampaignId): EmailTemplate => {
      if (templates[campaignId]?.subject || templates[campaignId]?.body) {
        const tmpl = templates[campaignId];
        // Auto-migrate: project campaign should use {{project_env_list}} instead of {{objects_list}}
        if (
          campaignId === 'project' &&
          tmpl.body.includes('{{objects_list}}') &&
          !tmpl.body.includes('{{project_env_list}}')
        ) {
          return { ...tmpl, body: tmpl.body.replace('{{objects_list}}', '{{project_env_list}}') };
        }
        return tmpl;
      }
      const defaults = defaultTemplates();
      return defaults[campaignId] || { subject: '', body: '' };
    },
    [templates],
  );

  const setTemplateFor = useCallback((campaignId: CampaignId, template: EmailTemplate) => {
    setTemplates((prev) => ({ ...prev, [campaignId]: template }));
  }, []);

  // Selection getter/setter per campaign
  const getSelectedKeys = useCallback(
    (campaignId: CampaignId): string[] => selectedKeys[campaignId] || [],
    [selectedKeys],
  );

  const setSelectedKeysFor = useCallback((campaignId: CampaignId, keys: string[]) => {
    setSelectedKeys((prev) => ({ ...prev, [campaignId]: keys }));
  }, []);

  // Persist state
  useEffect(() => {
    saveToStorage('campaignTemplates', templates);
  }, [templates]);

  useEffect(() => {
    saveToStorage('campaignSelectedKeys', selectedKeys);
  }, [selectedKeys]);

  useEffect(() => {
    saveToStorage('selectedCampaignId', selectedCampaignId);
  }, [selectedCampaignId]);

  // Migrate legacy per-campaign localStorage keys to unified storage on first load
  useEffect(() => {
    const legacyKeys: Record<string, CampaignId> = {
      projectTemplate: 'project',
      codeEnvTemplate: 'code_env',
      codeStudioTemplate: 'code_studio',
      autoScenarioTemplate: 'auto_scenario',
    };
    const legacySelectionKeys: Record<string, CampaignId> = {
      selectedProjectRecipientKeys: 'project',
      selectedCodeEnvRecipientKeys: 'code_env',
      selectedCodeStudioRecipientKeys: 'code_studio',
      selectedAutoScenarioRecipientKeys: 'auto_scenario',
    };
    let migratedTemplates = false;
    let migratedSelections = false;
    const newTemplates: Record<string, EmailTemplate> = {};
    const newSelections: Record<string, string[]> = {};

    for (const [storageKey, campaignId] of Object.entries(legacyKeys)) {
      const val = loadFromStorage<EmailTemplate | null>(storageKey, null);
      if (val && (val.subject || val.body)) {
        newTemplates[campaignId] = val;
        migratedTemplates = true;
      }
    }
    for (const [storageKey, campaignId] of Object.entries(legacySelectionKeys)) {
      const val = loadFromStorage<string[] | null>(storageKey, null);
      if (val !== null) {
        newSelections[campaignId] = val;
        migratedSelections = true;
      }
    }
    if (migratedTemplates) {
      setTemplates((prev) => {
        const merged = { ...prev };
        for (const [k, v] of Object.entries(newTemplates)) {
          if (!merged[k]?.subject && !merged[k]?.body) merged[k] = v;
        }
        return merged;
      });
    }
    if (migratedSelections) {
      setSelectedKeys((prev) => {
        const merged = { ...prev };
        for (const [k, v] of Object.entries(newSelections)) {
          if (!merged[k]) merged[k] = v;
        }
        return merged;
      });
    }
  }, []);

  // Helper: restore defaults into templates/selections from an OutreachData source
  const restoreFromSource = useCallback(
    (source: OutreachData) => {
      setTemplates((prev) => {
        const next = { ...prev };
        for (const config of CAMPAIGN_CONFIGS) {
          if (next[config.id]?.subject || next[config.id]?.body) continue;
          const tmpl = source.templates[config.id];
          if (tmpl) next[config.id] = tmpl;
        }
        return next;
      });
      {
        setSelectedKeys((prev) => {
          const next = { ...prev };
          for (const config of CAMPAIGN_CONFIGS) {
            if (next[config.id] !== undefined && next[config.id].length > 0) continue;
            const recipients = getRecipients(source, config);
            next[config.id] = recipients.map((r) => r.recipientKey);
          }
          return next;
        });
      }
    },
    [],
  );

  // Effect 1: Seed data from parsed ZIP (runs when parsedData changes, no API call)
  useEffect(() => {
    const outreachThresholds: OutreachThresholds = {
      codeEnvCountUnhealthy: thresholds.codeEnvCountUnhealthy,
      codeStudioCountUnhealthy: thresholds.codeStudioCountUnhealthy,
    };
    const localSeed = buildOutreachDataFromParsedData(parsedData, outreachThresholds);
    if (localSeed) {
      setData((prev) => {
        if (!prev) return localSeed;
        // Merge: localSeed updates locally-derivable fields, prev keeps API/enrichment data
        const merged: OutreachData = {
          ...prev,
          ...localSeed,
          summary: { ...prev.summary, ...localSeed.summary },
          mailChannels: prev.mailChannels?.length ? prev.mailChannels : localSeed.mailChannels,
          templates: prev.templates ?? localSeed.templates,
        };
        for (const config of CAMPAIGN_CONFIGS) {
          const rKey = config.recipientsKey as keyof OutreachData;
          const sKey = config.summaryKey as keyof OutreachData['summary'];
          const localR = (localSeed as any)[rKey] as OutreachRecipient[] | undefined;
          const prevR = (prev as any)[rKey] as OutreachRecipient[] | undefined;
          if ((!localR || localR.length === 0) && prevR && prevR.length > 0) {
            (merged as any)[rKey] = prevR;
            (merged.summary as any)[sKey] = (prev.summary as any)[sKey];
          }
        }
        return merged;
      });
      restoreFromSource(localSeed);
      setIsLoading(false);
    }
  }, [parsedData, thresholds.codeEnvCountUnhealthy, thresholds.codeStudioCountUnhealthy, restoreFromSource]);

  // Effect 2: Fetch API outreach data in background — triggers server-side tracking ingest
  // and enriches data with real mail channels. Skipped if app-level loader already fetched.
  useEffect(() => {
    let cancelled = false;
    if (!parsedData.outreachApiLoaded) {
      fetchWithSessionCache('outreach:data', () =>
        fetchJson<OutreachData>('/api/tools/outreach-data'),
      ).then((apiData) => {
        if (cancelled) return;
        let finalData: OutreachData = apiData;
        setData((prev) => {
          if (!prev) {
            // No ZIP data yet — use API data entirely and restore defaults
            restoreFromSource(apiData);
            setIsLoading(false);
            return apiData;
          }
          // Merge: keep ZIP-derived recipients (richer), take API mail channels + templates.
          // For API-only campaigns (ones the local seed can't compute), pull in API recipients and counts.
          const merged: OutreachData = {
            ...prev,
            mailChannels: apiData.mailChannels ?? prev.mailChannels,
            templates: apiData.templates ?? prev.templates,
            summary: { ...prev.summary },
          };
          // Backfill API-only recipient arrays and summary counts
          for (const config of CAMPAIGN_CONFIGS) {
            const rKey = config.recipientsKey as keyof OutreachData;
            const sKey = config.summaryKey as keyof OutreachData['summary'];
            const localRecipients = (prev as unknown as Record<string, unknown>)[rKey] as OutreachRecipient[] | undefined;
            const apiRecipients = (apiData as unknown as Record<string, unknown>)[rKey] as OutreachRecipient[] | undefined;
            // If local has no recipients but API does, backfill
            if ((!localRecipients || localRecipients.length === 0) && apiRecipients && apiRecipients.length > 0) {
              (merged as unknown as Record<string, unknown>)[rKey] = apiRecipients;
              const apiCount = (apiData.summary as Record<string, unknown>)[sKey];
              if (apiCount != null) {
                (merged.summary as Record<string, unknown>)[sKey] = apiCount;
              }
            }
          }
          finalData = merged;
          return merged;
        });
        dispatch({ type: 'SET_PARSED_DATA', payload: { outreachData: finalData, outreachApiLoaded: true } });
        log(`API outreach data loaded: channels=${apiData.mailChannels?.length ?? 0}, apiUnusedCodeEnvs=${apiData.summary?.unusedCodeEnvCount ?? 0}, apiUnusedRecipients=${apiData.unusedCodeEnvRecipients?.length ?? 0}`);
      }).catch((err) => {
        if (cancelled) return;
        dispatch({ type: 'SET_PARSED_DATA', payload: { outreachApiLoaded: true } });
        log(`API outreach data fetch failed (non-critical): ${String(err)}`, 'error');
      });
    }
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Effect: Fetch inactive project recipients from fast endpoint (~0.1s)
  useEffect(() => {
    fetchJson<{ projects: Array<{ projectKey: string; name: string; owner: string; daysInactive: number }> }>(
      '/api/tools/inactive-projects'
    ).then((res) => {
      const emails = new Map<string, string>();
      for (const u of parsedData.users || []) if (u?.login) emails.set(String(u.login), String(u.email || u.login));
      const recipientsMap = new Map<string, OutreachRecipient>();
      for (const p of res.projects) {
        const owner = p.owner || 'Unknown';
        if (!recipientsMap.has(owner)) {
          recipientsMap.set(owner, makeRecipient(owner, emails));
        }
        const r = recipientsMap.get(owner)!;
        r.projectKeys.push(p.projectKey);
        r.projects = [...(r.projects || []), { projectKey: p.projectKey, name: p.name, daysInactive: p.daysInactive }];
      }
      const recipients = finalizeRecipients(recipientsMap);
      setData((prev) => {
        const base = prev || {
          summary: {
            projectCount: 0,
            unhealthyProjectCount: 0,
            unhealthyCodeEnvCount: 0,
            projectRecipientCount: 0,
            codeEnvRecipientCount: 0,
          },
          mailChannels: [],
          templates: defaultTemplates(),
          unhealthyProjects: [],
          unhealthyCodeEnvs: [],
          unhealthyCodeStudioProjects: [],
          projectRecipients: [],
          codeEnvRecipients: [],
          codeStudioRecipients: [],
          autoScenarioRecipients: [],
        } satisfies OutreachData;
        return {
          ...base,
          inactiveProjectRecipients: recipients,
          summary: { ...base.summary, inactiveProjectCount: res.projects.length },
        };
      });
      log(`Fast inactive-projects endpoint: ${res.projects.length} projects, ${recipientsMap.size} recipients`);
    }).catch(() => {}); // silent — outreach-data may still backfill
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Effect 3: Fetch disabled campaign settings
  useEffect(() => {
    fetchJson<{ campaigns: Record<string, boolean> }>('/api/tracking/campaign-settings')
      .then((res) => {
        const disabled = new Set<string>();
        for (const [id, enabled] of Object.entries(res.campaigns)) {
          if (!enabled) disabled.add(id);
        }
        setDisabledCampaigns(disabled);
      })
      .catch(() => {});
  }, []);

  // Effect 4: Fetch campaign exemptions
  useEffect(() => {
    fetchJson<{ exemptions: CampaignExemption[] }>('/api/tracking/exemptions')
      .then((res) => {
        const map = new Map<CampaignId, Map<string, number>>();
        for (const ex of res.exemptions) {
          const campaignId = ex.campaign_id as CampaignId;
          if (!map.has(campaignId)) map.set(campaignId, new Map());
          map.get(campaignId)!.set(ex.entity_key, ex.exemption_id);
        }
        setExemptions(map);
      })
      .catch(() => {});
  }, []);

  // Filter recipients by removing exempted sins (used for email send/preview only).
  // Uses sin keys (which may be project keys, code env keys, etc. depending on campaign).
  const getFilteredRecipients = useCallback((campaignId: CampaignId, recipients: OutreachRecipient[]): OutreachRecipient[] => {
    const exempted = exemptions.get(campaignId);
    if (!exempted || exempted.size === 0) return recipients;
    return recipients.filter((r) => {
      const sins = getRecipientSins(r);
      return sins.some((s) => !exempted.has(s.key));
    });
  }, [exemptions]);

  // Handle exempting a single entity from a campaign
  const handleExempt = useCallback(async (campaignId: CampaignId, entityKey: string) => {
    try {
      const res = await fetchJson<{ ok: boolean; exemption: CampaignExemption }>('/api/tracking/exemptions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaign_id: campaignId, entity_key: entityKey }),
      });
      const eid = res.exemption?.exemption_id;
      if (eid == null) return;
      setExemptions((prev) => {
        const next = new Map(prev);
        const existing = next.get(campaignId) ?? new Map<string, number>();
        const updated = new Map(existing);
        updated.set(entityKey, eid);
        next.set(campaignId, updated);
        return next;
      });
    } catch {
      // ignore
    }
  }, []);

  // Handle un-exempting (undo) a single entity
  const handleUnexempt = useCallback(async (campaignId: CampaignId, entityKey: string) => {
    const exemptionId = exemptionsRef.current.get(campaignId)?.get(entityKey);
    if (exemptionId == null) return;
    try {
      await fetchJson(`/api/tracking/exemptions`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ exemption_id: exemptionId }),
      });
      setExemptions((prev) => {
        const next = new Map(prev);
        const existing = next.get(campaignId);
        if (existing) {
          const updated = new Map(existing);
          updated.delete(entityKey);
          next.set(campaignId, updated);
        }
        return next;
      });
    } catch {
      // ignore
    }
  }, []);

  // Check if a specific entity is exempt in a campaign
  const isEntityExempt = useCallback((campaignId: CampaignId, entityKey: string): boolean => {
    return exemptions.get(campaignId)?.has(entityKey) ?? false;
  }, [exemptions]);

  const openPreview = async (campaign: CampaignId) => {
    if (!data) return;
    const config = CAMPAIGN_CONFIGS.find((c) => c.id === campaign);
    if (!config) return;
    const allRecipients = getFilteredRecipients(campaign, getRecipients(data, config));
    const selected = new Set(getSelectedKeys(campaign));
    const recipients = allRecipients.filter((r) => selected.has(r.recipientKey));
    if (recipients.length === 0) return;

    setPreviewLoading(true);
    setSendResult(null);
    setPreviewCampaign(campaign);
    try {
      const tmpl = getTemplate(campaign);
      if (recipients[0]) {
        const r = recipients[0];
        log(`[preview] sample: owner=${r.owner} usageDetails=${r.usageDetails.length} projects=${r.projects?.length ?? 0} codeEnvNames=${r.codeEnvNames.length}`);
        if (r.projects?.length) {
          for (const p of r.projects) {
            log(`[preview]   project=${p.projectKey} envCount=${p.codeEnvCount ?? '?'}`);
          }
        }
        if (r.usageDetails.length > 0) {
          const u = r.usageDetails[0];
          log(`[preview]   sampleUsage: project=${u.projectKey} env=${u.codeEnvName ?? u.codeEnvKey ?? '?'} type=${u.usageType}`);
        }
      }
      log(`POST /api/tools/email/preview campaign=${campaign} recipients=${recipients.length}`);
      const response = await fetchJson<EmailPreviewResponse>('/api/tools/email/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          campaign,
          template: tmpl,
          recipients,
        }),
      });
      setPreviewItems(response.previews);
      previewModal.open();
      // Show backend debug info
      const firstPreview = response.previews?.[0] as unknown as Record<string, unknown> | undefined;
      const dbg = firstPreview?._debug as Record<string, unknown> | undefined;
      if (dbg) {
        log(`[backend] usageDetails=${dbg.usageDetailsCount} types=${JSON.stringify(dbg.usageTypes)} envGroups=${JSON.stringify(dbg.envGroups)}`);
        const projs = dbg.projectsInRecipient as Array<Record<string, unknown>> | undefined;
        if (projs) {
          for (const p of projs) {
            log(`[backend]   project=${p.projectKey} envNames=${JSON.stringify(p.codeEnvNames)}`);
          }
        }
      }
      log(`Preview generated campaign=${campaign} count=${response.count}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      log(`Preview failed: ${message}`, 'error');
    } finally {
      setPreviewLoading(false);
    }
  };

  const sendEmails = async () => {
    if (previewItems.length === 0) return;
    setSendLoading(true);
    try {
      log(`POST /api/tools/email/send campaign=${previewCampaign} messages=${previewItems.length}`);
      const response = await fetchJson<EmailSendResponse>('/api/tools/email/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          campaign: previewCampaign,
          channelId: selectedChannel || undefined,
          plainText: false,
          previews: previewItems,
        }),
      });
      setSendResult(response);
      log(
        `Send completed channel=${response.channelId} sent=${response.sentCount}/${response.requestedCount}`,
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      log(`Send failed: ${message}`, 'error');
    } finally {
      setSendLoading(false);
    }
  };

  const sendAllCampaigns = async () => {
    if (!data) return;
    setSendAllLoading(true);
    setSendAllStatus(null);
    let totalSent = 0;
    let totalCampaigns = 0;
    try {
      for (const config of CAMPAIGN_CONFIGS) {
        if (disabledCampaigns.has(config.id)) continue;
        const allRecipients = getFilteredRecipients(config.id, getRecipients(data, config));
        const selected = new Set(getSelectedKeys(config.id));
        // use selected if any, otherwise all recipients
        const recipients =
          selected.size > 0
            ? allRecipients.filter((r) => selected.has(r.recipientKey))
            : allRecipients;
        if (recipients.length === 0) continue;

        setSendAllStatus(`Sending ${config.title}…`);
        log(`[send-all] preview campaign=${config.id} recipients=${recipients.length}`);
        const preview = await fetchJson<EmailPreviewResponse>('/api/tools/email/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ campaign: config.id, template: getTemplate(config.id), recipients }),
        });
        if (preview.previews.length === 0) continue;

        log(`[send-all] send campaign=${config.id} count=${preview.previews.length}`);
        const result = await fetchJson<EmailSendResponse>('/api/tools/email/send', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            campaign: config.id,
            channelId: selectedChannel || undefined,
            plainText: false,
            previews: preview.previews,
          }),
        });
        totalSent += result.sentCount;
        totalCampaigns++;
      }
      setSendAllStatus(`Done — ${totalSent} emails sent across ${totalCampaigns} campaigns`);
      log(`[send-all] complete totalSent=${totalSent} campaigns=${totalCampaigns}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setSendAllStatus(`Error: ${message}`);
      log(`[send-all] failed: ${message}`, 'error');
    } finally {
      setSendAllLoading(false);
    }
  };

  // Campaigns with recipients to display in summary (excludes disabled)
  const activeSummaryItems = useMemo(() => {
    if (!data) return [];
    return CAMPAIGN_CONFIGS.filter((config) => {
      if (disabledCampaigns.has(config.id)) return false;
      const count = getSummaryCount(data, config);
      return count > 0;
    }).slice(0, 6);
  }, [data, disabledCampaigns]);

  // Sidebar data for campaigns — count only recipients with non-exempt sins
  const campaignSidebarItems = useMemo(() => {
    if (!data) return [];
    return CAMPAIGN_CONFIGS.map((config) => {
      const isDisabled = disabledCampaigns.has(config.id);
      const allRecipients = getRecipients(data, config);
      const exempted = exemptions.get(config.id);
      let recipientCount: number;
      if (isDisabled || !exempted || exempted.size === 0) {
        recipientCount = allRecipients.length;
      } else {
        recipientCount = allRecipients.filter((r) => {
          const sins = getRecipientSins(r);
          return sins.some((s) => !exempted.has(s.key));
        }).length;
      }
      return { config, recipientCount, isDisabled };
    });
  }, [data, disabledCampaigns, exemptions]);

  // Selected campaign detail
  const selectedConfig = useMemo(
    () => CAMPAIGN_CONFIGS.find((c) => c.id === selectedCampaignId) ?? CAMPAIGN_CONFIGS[0],
    [selectedCampaignId],
  );

  if (activePage === 'outreach' && isLoading) {
    const analysisLoading = parsedData.analysisLoading;
    return (
      <main className="flex-1 py-4">
        <Container ultraWide={ultraWideEnabled}>
          <div className="glass-card p-6">
            {analysisLoading?.active ? (
              <>
                <div className="flex items-center justify-between text-xs text-[var(--text-secondary)]">
                  <span>{analysisLoading.message || 'Analyzing...'}</span>
                  <span className="font-mono">
                    {Math.max(0, Math.min(100, Math.round(analysisLoading.progressPct || 0)))}%
                  </span>
                </div>
                <div className="mt-2 h-3 rounded-full bg-[var(--bg-glass)] overflow-hidden">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-[var(--neon-cyan)] to-[var(--neon-green)] transition-all duration-300 ease-out"
                    style={{
                      width: `${Math.max(0, Math.min(100, Math.round(analysisLoading.progressPct || 0)))}%`,
                    }}
                  />
                </div>
                {analysisLoading.phase && (
                  <div className="mt-1 text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
                    {analysisLoading.phase.replace(/_/g, ' ')}
                  </div>
                )}
              </>
            ) : (
              <div className="flex items-center gap-3 text-[var(--text-secondary)]">
                <div className="w-5 h-5 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin" />
                Preparing outreach data...
              </div>
            )}
          </div>
        </Container>
      </main>
    );
  }

  if (activePage === 'outreach' && !data) {
    return (
      <main className="flex-1 py-4">
        <Container ultraWide={ultraWideEnabled}>
          <div className="glass-card p-6 space-y-4">
            <div className="text-[var(--neon-red)]">Failed to load tools data</div>
            {error && <div className="text-sm text-[var(--text-muted)] font-mono">{error}</div>}
            <div className="text-sm text-[var(--text-secondary)]">
              Navigate using the sidebar to return to other pages.
            </div>
          </div>
        </Container>
      </main>
    );
  }

  const previewConfig = CAMPAIGN_CONFIGS.find((c) => c.id === previewCampaign);

  return (
    <>
      <main className="flex-1 py-4 flex flex-col">
        <Container ultraWide={ultraWideEnabled} className="flex-1 flex flex-col min-h-0">
          <div className="flex flex-col gap-4 flex-1 min-h-0">
            {/* Owner Outreach */}
            {activePage === 'outreach' && (
              <>
                <section className="glass-card p-4">
                  <p className="text-sm text-[var(--text-muted)]">
                    Email owners about health issues using DSS mail channels.{' '}
                    {CAMPAIGN_CONFIGS.length} campaigns available.
                  </p>
                  <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3 mt-4">
                    {activeSummaryItems.map((config) => (
                      <div
                        key={config.id}
                        className="p-3 rounded border border-[var(--border-glass)] bg-[var(--bg-glass)]"
                      >
                        <div className="text-xs text-[var(--text-muted)] truncate">
                          {config.summaryLabel}
                        </div>
                        <div className="text-2xl font-mono text-[var(--text-primary)]">
                          {getSummaryCount(data!, config)}
                        </div>
                      </div>
                    ))}
                  </div>
                </section>

                {error && (
                  <section className="glass-card p-3 border border-[var(--status-critical-border)] text-[var(--neon-red)] text-sm">
                    {error}
                  </section>
                )}

                {/* Sidebar + Detail layout */}
                <div className="flex flex-col lg:flex-row gap-4 flex-1 min-h-0">
                  {/* Campaign sidebar - vertical on lg+, horizontal tabs below lg */}
                  <aside className="lg:w-64 shrink-0 lg:sticky lg:top-4 lg:self-start">
                    {/* Mobile: horizontal scrollable tab bar */}
                    <div className="flex lg:hidden gap-2 overflow-x-auto pb-2">
                      {campaignSidebarItems.map(({ config, recipientCount, isDisabled }) => (
                        <button
                          key={config.id}
                          onClick={() => setSelectedCampaignId(config.id)}
                          className={`shrink-0 whitespace-nowrap px-3 py-1.5 rounded-full text-xs font-medium border transition-colors ${
                            selectedCampaignId === config.id
                              ? 'bg-[var(--accent-muted)] text-[var(--accent)] border-[var(--accent)]'
                              : 'bg-[var(--bg-glass)] text-[var(--text-secondary)] border-[var(--border-glass)] hover:bg-[var(--bg-glass-hover)]'
                          }`}
                        >
                          {config.title}
                          {!isDisabled && recipientCount > 0 && (
                            <span className="ml-1.5 font-mono">{recipientCount}</span>
                          )}
                          {isDisabled && (
                            <span className="ml-1.5 text-amber-400">Off</span>
                          )}
                        </button>
                      ))}
                    </div>
                    {/* Mobile: Send All below tab bar */}
                    <div className="flex lg:hidden items-center gap-3 mt-2">
                      <button
                        onClick={() => setShowSendAllConfirm(true)}
                        disabled={sendAllLoading || previewLoading || !data}
                        data-testid="send-all-campaigns-mobile"
                        className="px-4 py-2 rounded btn-primary disabled:opacity-50 text-sm font-medium"
                      >
                        {sendAllLoading ? (sendAllStatus ?? 'Sending\u2026') : 'Send All Campaigns'}
                      </button>
                      {sendAllStatus && !sendAllLoading && (
                        <span className="text-sm text-[var(--text-secondary)]">{sendAllStatus}</span>
                      )}
                    </div>

                    {/* Desktop: vertical sidebar */}
                    <div className="hidden lg:flex flex-col glass-card p-2">
                      <nav className="flex-1 flex flex-col gap-0.5">
                        {campaignSidebarItems.map(({ config, recipientCount, isDisabled }) => (
                          <button
                            key={config.id}
                            onClick={() => setSelectedCampaignId(config.id)}
                            className={`relative w-full flex items-center gap-3 px-2.5 py-1.5 rounded-md text-left text-sm transition-colors ${
                              selectedCampaignId === config.id
                                ? 'bg-[var(--accent-muted)] text-[var(--accent)]'
                                : 'text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]'
                            }`}
                          >
                            {selectedCampaignId === config.id && (
                              <motion.div
                                layoutId="outreach-sidebar-active"
                                className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 rounded-r bg-[var(--accent)]"
                                transition={{ type: 'spring', stiffness: 500, damping: 35 }}
                              />
                            )}
                            <span className="flex-1 truncate">{config.title}</span>
                            <span className="flex items-center gap-1.5 shrink-0">
                              {isDisabled && (
                                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-500/15 text-amber-400">
                                  Off
                                </span>
                              )}
                              {!isDisabled && recipientCount > 0 && (
                                <span className="flex-shrink-0 min-w-[20px] h-5 flex items-center justify-center rounded-full bg-[var(--accent-muted)] text-[var(--accent)] text-xs font-medium px-1.5">
                                  {recipientCount}
                                </span>
                              )}
                            </span>
                          </button>
                        ))}
                      </nav>
                      <div className="pt-2 border-t border-[var(--border-default)]">
                        <button
                          onClick={() => setShowSendAllConfirm(true)}
                          disabled={sendAllLoading || previewLoading || !data}
                          data-testid="send-all-campaigns"
                          className="w-full px-4 py-2 rounded btn-primary disabled:opacity-50 text-sm font-medium"
                        >
                          {sendAllLoading ? (sendAllStatus ?? 'Sending\u2026') : 'Send All Campaigns'}
                        </button>
                        {sendAllStatus && !sendAllLoading && (
                          <div className="text-xs text-[var(--text-secondary)] mt-1 text-center">{sendAllStatus}</div>
                        )}
                      </div>
                    </div>
                  </aside>

                  {/* Detail area */}
                  <div className="flex-1 min-w-0 flex flex-col">
                    <AnimatePresence mode="wait">
                      <motion.div
                        key={selectedCampaignId}
                        className="flex-1 min-h-0 flex flex-col"
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -8 }}
                        transition={{ duration: 0.15 }}
                      >
                        {(() => {
                          const isDisabled = disabledCampaigns.has(selectedConfig.id);
                          // Show all recipients (unfiltered) — exempt ones shown in green
                          const recipients = getRecipients(data!, selectedConfig);
                          const keys = getSelectedKeys(selectedConfig.id);
                          const selectedCount = recipients.filter((r) =>
                            keys.includes(r.recipientKey),
                          ).length;
                          return (
                            <CampaignPanel
                              title={selectedConfig.title}
                              description={selectedConfig.description}
                              recipients={recipients}
                              selectedKeys={keys}
                              setSelectedKeys={(next) => setSelectedKeysFor(selectedConfig.id, next)}
                              template={getTemplate(selectedConfig.id)}
                              setTemplate={(tmpl) => setTemplateFor(selectedConfig.id, tmpl)}
                              onPreview={() => openPreview(selectedConfig.id)}
                              previewDisabled={previewLoading || selectedCount === 0 || isDisabled}
                              disabled={isDisabled}
                              onExempt={isDisabled ? undefined : (entityKey) =>
                                handleExempt(selectedConfig.id, entityKey)
                              }
                              onUnexempt={isDisabled ? undefined : (entityKey) =>
                                handleUnexempt(selectedConfig.id, entityKey)
                              }
                              isEntityExempt={isDisabled ? undefined : (entityKey) =>
                                isEntityExempt(selectedConfig.id, entityKey)
                              }
                              alwaysExpanded
                              hideProjects={selectedConfig.id === 'unused_code_env'}
                              hideCodeEnvs={selectedConfig.id === 'inactive_project'}
                              hideObjects={selectedConfig.id === 'inactive_project' || selectedConfig.id === 'unused_code_env'}
                            />
                          );
                        })()}
                      </motion.div>
                    </AnimatePresence>
                  </div>
                </div>
              </>
            )}

            {/* Project Cleaner */}
            {activePage === 'project-cleaner' && (
              <Suspense
                fallback={
                  <div className="glass-card p-6 text-[var(--text-secondary)]">Loading...</div>
                }
              >
                <InactiveProjectCleaner />
              </Suspense>
            )}

            {/* Plugin Comparator */}
            {activePage === 'plugins' && (
              <Suspense
                fallback={
                  <div className="glass-card p-6 text-[var(--text-secondary)]">Loading...</div>
                }
              >
                <PluginComparator />
              </Suspense>
            )}
          </div>
        </Container>
      </main>

      <Modal
        isOpen={previewModal.isOpen}
        onClose={previewModal.close}
        title={`Email Preview (${previewConfig?.title || previewCampaign})`}
        sizePreset="large"
        footer={
          <div className="flex items-center justify-between gap-3">
            <div className="text-sm text-[var(--text-secondary)]">
              {sendResult
                ? `Sent ${sendResult.sentCount}/${sendResult.requestedCount}`
                : `${previewItems.length} email(s) ready`}
            </div>
            <div className="flex gap-2">
              <button
                onClick={previewModal.close}
                className="px-3 py-1.5 rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-[var(--text-secondary)]"
              >
                Close
              </button>
              <button
                onClick={sendEmails}
                disabled={sendLoading || previewItems.length === 0}
                className="px-4 py-1.5 rounded btn-primary disabled:opacity-50"
              >
                {sendLoading ? 'Sending...' : 'Send Emails'}
              </button>
            </div>
          </div>
        }
      >
        <div className="space-y-3 max-h-[58vh] overflow-y-auto pr-1">
          {sendResult && (
            <div className="text-sm border border-[var(--border-glass)] rounded p-2 bg-[var(--bg-glass)]">
              Channel: <span className="font-mono">{sendResult.channelId}</span> | Sent:{' '}
              {sendResult.sentCount}/{sendResult.requestedCount}
            </div>
          )}
          {previewItems.map((item) => {
            const result = sendResult?.results.find(
              (entry) => entry.recipientKey === item.recipientKey,
            );
            return (
              <article
                key={`${item.recipientKey}-${item.to}`}
                className="border border-[var(--border-glass)] rounded p-3 space-y-2 bg-[var(--bg-glass)]"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="text-sm text-[var(--text-secondary)]">
                    To: <span className="font-mono">{item.to}</span>
                  </div>
                  {result && (
                    <span
                      className={
                        result.status === 'sent'
                          ? 'text-[var(--neon-green)] text-xs font-mono'
                          : 'text-[var(--neon-red)] text-xs font-mono'
                      }
                    >
                      {result.status === 'sent' ? 'sent' : `error: ${result.error || 'failed'}`}
                    </span>
                  )}
                </div>
                <div className="text-sm text-[var(--text-primary)]">
                  Subject: <span className="font-medium">{item.subject}</span>
                </div>
                <iframe
                  srcDoc={item.body}
                  className="w-full border border-[var(--border-primary)] rounded bg-white"
                  style={{ minHeight: '320px', maxHeight: '600px' }}
                  sandbox="allow-same-origin"
                  title={`Email preview for ${item.owner}`}
                />
              </article>
            );
          })}
        </div>
      </Modal>

      {/* Send All Campaigns confirmation dialog */}
      {showSendAllConfirm &&
        createPortal(
          <motion.div
            className="fixed inset-0 z-50 flex items-center justify-center modal-overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            onClick={() => setShowSendAllConfirm(false)}
          >
            <motion.div
              className="modal-content w-full max-w-md mx-4 p-6 rounded-xl"
              initial={{ opacity: 0, scale: 0.95, y: 10 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              transition={{ duration: 0.15 }}
              onClick={(e) => e.stopPropagation()}
            >
              <h3 className="text-lg font-semibold text-[var(--text-primary)] mb-3">
                Send All Campaigns
              </h3>
              <p className="text-sm text-[var(--text-secondary)] mb-4">
                This will send emails for all enabled campaigns:
              </p>
              <ul className="text-sm space-y-1 mb-5">
                {campaignSidebarItems
                  .filter((item) => !item.isDisabled && item.recipientCount > 0)
                  .map(({ config, recipientCount }) => (
                    <li key={config.id} className="flex items-center justify-between text-[var(--text-secondary)]">
                      <span>{config.title}</span>
                      <span className="font-mono text-xs text-[var(--accent)]">
                        {recipientCount} {recipientCount === 1 ? 'recipient' : 'recipients'}
                      </span>
                    </li>
                  ))}
              </ul>
              <div className="flex items-center justify-end gap-3">
                <button
                  onClick={() => setShowSendAllConfirm(false)}
                  className="px-4 py-2 rounded text-sm font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-glass)] transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={() => {
                    setShowSendAllConfirm(false);
                    sendAllCampaigns();
                  }}
                  data-testid="send-all-confirm"
                  className="px-4 py-2 rounded btn-primary text-sm font-medium"
                >
                  Confirm & Send
                </button>
              </div>
            </motion.div>
          </motion.div>,
          document.body,
        )}
    </>
  );
}
