import { createSyncStore } from './createSyncStore';
import { fetchJson } from '../utils/api';
import { pushToast } from './toastStore';

// Agents → Permissions → "Autonomous daily agent" panel. One GET powers the
// whole panel (opt-ins, caps, schedule, delivery); every write is a partial
// POST whose response is the authoritative refreshed payload. Opting an
// action IN also enables its main per-action gate server-side.

export interface TriageActionRow {
  action: string;
  risk: 'low' | 'medium' | 'high';
  description: string;
  findings: string[];
  optedIn: boolean;
  gateEnabled: boolean;
  localOnly: boolean;
  batchable: boolean;
}

export interface TriageScenarioStatus {
  provisioned: boolean;
  active: boolean;
  hour: number | null;
  lastRun: { outcome: string | null; start: number | null; end: number | null } | null;
}

export interface TriageSettings {
  ok: boolean;
  enabled: boolean;
  remoteHosts: boolean;
  actions: TriageActionRow[];
  caps: { maxGb: number; maxObjects: number; logMinAgeDays: number };
  delivery: {
    recipient: string;
    mailChannel: string;
    threshold: number;
    llmConfigured: boolean;
  };
  killSwitch: boolean;
  masterPassword: boolean;
  scenario: TriageScenarioStatus;
}

interface TriageSettingsState {
  data: TriageSettings | null;
  loading: boolean;
  loaded: boolean;
  saving: string | null;
  testSending: boolean;
  provisioning: boolean;
  error: string | null;
}

export const triageSettingsStore = createSyncStore<TriageSettingsState>({
  data: null,
  loading: false,
  loaded: false,
  saving: null,
  testSending: false,
  provisioning: false,
  error: null,
});

export async function loadTriageSettings(): Promise<void> {
  triageSettingsStore.patch({ loading: true, error: null });
  try {
    const res = await fetchJson<TriageSettings>('/api/agents/triage-settings');
    triageSettingsStore.patch({ data: res, loading: false, loaded: true });
  } catch (e) {
    triageSettingsStore.patch({
      loading: false,
      error: e instanceof Error ? e.message : 'Failed to load the autonomous agent settings.',
    });
  }
}

export interface TriageUpdate {
  enabled?: boolean;
  remoteHosts?: boolean;
  optIn?: Record<string, boolean>;
  maxGb?: number;
  maxObjects?: number;
}

/** Partial write; optimistic for toggles, authoritative on response. The
 *  `saving` tag drives per-control spinners ('__master__', '__bulk__',
 *  '__caps__', or an action name). */
export async function updateTriageSettings(update: TriageUpdate, savingTag: string): Promise<void> {
  const prev = triageSettingsStore.get();
  if (prev.data) {
    const optimistic: TriageSettings = {
      ...prev.data,
      ...(update.enabled !== undefined ? { enabled: update.enabled } : null),
      ...(update.remoteHosts !== undefined ? { remoteHosts: update.remoteHosts } : null),
      actions: prev.data.actions.map((a) =>
        update.optIn && a.action in update.optIn
          ? { ...a, optedIn: update.optIn[a.action], gateEnabled: a.gateEnabled || update.optIn[a.action] }
          : a,
      ),
    };
    triageSettingsStore.patch({ data: optimistic, saving: savingTag, error: null });
  } else {
    triageSettingsStore.patch({ saving: savingTag, error: null });
  }
  try {
    const res = await fetchJson<TriageSettings>('/api/agents/triage-settings/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(update),
    });
    triageSettingsStore.patch({ data: res, saving: null });
  } catch (e) {
    triageSettingsStore.patch({ data: prev.data, saving: null });
    pushToast('error', 'Autonomous agent settings not saved', {
      detail: 'The change reverted. ' + (e instanceof Error ? e.message : ''),
    });
    throw e;
  }
}

export async function provisionTriageSchedule(): Promise<void> {
  triageSettingsStore.patch({ provisioning: true, error: null });
  try {
    const res = await fetchJson<{ ok: boolean; scenario?: TriageScenarioStatus; steps?: unknown }>(
      '/api/agents/triage-provision',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
    );
    const prev = triageSettingsStore.get();
    if (prev.data && res.scenario) {
      triageSettingsStore.patch({
        data: { ...prev.data, scenario: res.scenario },
        provisioning: false,
      });
    } else {
      triageSettingsStore.patch({ provisioning: false });
    }
    if (res.ok) {
      pushToast('success', 'Daily schedule provisioned', {
        detail: 'Scenario, daily trigger and failure reporter are in place.',
      });
    } else {
      pushToast('error', 'Provisioning incomplete', {
        detail: 'Check the triage connection and digest recipient in Settings.',
      });
    }
  } catch (e) {
    triageSettingsStore.patch({ provisioning: false });
    pushToast('error', 'Provisioning failed', {
      detail: e instanceof Error ? e.message : undefined,
    });
    throw e;
  }
}

export async function sendTestDigest(): Promise<void> {
  triageSettingsStore.patch({ testSending: true });
  try {
    const res = await fetchJson<{ ok: boolean; recipient?: string; error?: string }>(
      '/api/agents/triage-digest-test',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // the email's "Open Admin Toolkit" button points back at this webapp
        body: JSON.stringify({ toolkitUrl: window.location.origin + window.location.pathname }),
      },
    );
    triageSettingsStore.patch({ testSending: false });
    pushToast('success', 'Test report sent', {
      detail: res.recipient ? `Check ${res.recipient} — sample data, real template.` : undefined,
    });
  } catch (e) {
    triageSettingsStore.patch({ testSending: false });
    pushToast('error', 'Test report failed', {
      detail: e instanceof Error ? e.message : undefined,
    });
    throw e;
  }
}
