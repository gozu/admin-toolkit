import { createSyncStore } from './createSyncStore';
import { fetchJson } from '../utils/api';

// Agent Permissions → per-action enablement gates. Read-only sensor tools
// default ON; every actuator action defaults OFF until an admin enables it.
// Toggles persist into the plugin config through the backend (advanced-gated)
// and reach running agent kernels within ~30s (action_gates.py cache TTL).

export interface SensorRow {
  name: string;
  mode: 'read';
  description: string;
  enabled: boolean;
}

export interface ActionRow {
  action: string;
  mode: 'read/write' | 'execute';
  risk: 'green' | 'amber' | 'red';
  shape: string;
  batchable: boolean;
  localOnly: boolean;
  enabled: boolean;
}

interface ActionSettingsResponse {
  ok: boolean;
  sensors: SensorRow[];
  actions: ActionRow[];
  gates: Record<string, boolean>;
}

interface AgentActionGatesState {
  sensors: SensorRow[];
  actions: ActionRow[];
  loading: boolean;
  loaded: boolean;
  saving: string | null;
  error: string | null;
}

export const agentActionGatesStore = createSyncStore<AgentActionGatesState>({
  sensors: [],
  actions: [],
  loading: false,
  loaded: false,
  saving: null,
  error: null,
});

export async function loadActionGates(): Promise<void> {
  agentActionGatesStore.patch({ loading: true, error: null });
  try {
    const res = await fetchJson<ActionSettingsResponse>('/api/agents/action-settings');
    agentActionGatesStore.patch({
      sensors: res.sensors ?? [],
      actions: res.actions ?? [],
      loading: false,
      loaded: true,
    });
  } catch (e) {
    agentActionGatesStore.patch({
      loading: false,
      error: e instanceof Error ? e.message : 'Failed to load the action catalog.',
    });
  }
}

/** Persist one toggle (optimistic; server response is authoritative). */
export async function toggleActionGate(name: string, enabled: boolean): Promise<void> {
  const prev = agentActionGatesStore.get();
  agentActionGatesStore.patch({
    saving: name,
    error: null,
    sensors: prev.sensors.map((s) => (s.name === name ? { ...s, enabled } : s)),
    actions: prev.actions.map((a) => (a.action === name ? { ...a, enabled } : a)),
  });
  try {
    const res = await fetchJson<ActionSettingsResponse>('/api/agents/action-settings/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ gates: { [name]: enabled } }),
    });
    agentActionGatesStore.patch({
      sensors: res.sensors ?? [],
      actions: res.actions ?? [],
      saving: null,
    });
  } catch (e) {
    agentActionGatesStore.patch({
      sensors: prev.sensors,
      actions: prev.actions,
      saving: null,
      error: e instanceof Error ? e.message : 'Failed to save the toggle.',
    });
    throw e;
  }
}
