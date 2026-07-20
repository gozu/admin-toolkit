import { createSyncStore } from './createSyncStore';
import { fetchJson } from '../utils/api';
import { pushToast } from './toastStore';

// Agent Permissions → per-capability Enabled + Autonomous gates. Read-only
// sensor tools default ON (and autonomous — reading is side-effect free);
// every actuator action defaults OFF and non-autonomous until an admin
// enables it. Server invariants: autonomous ⇒ enabled (allowing Auto forces
// the gate on; disabling a gate clears Auto), and python-run can never be
// autonomous. Toggles persist into the plugin config through the backend
// (advanced-gated) and reach running agent kernels within ~30s
// (action_gates.py cache TTL).

export interface SensorRow {
  name: string;
  mode: 'read';
  description: string;
  enabled: boolean;
  autonomous: boolean;
}

export interface ActionRow {
  action: string;
  mode: 'read/write' | 'execute';
  risk: 'green' | 'amber' | 'red';
  shape: string;
  batchable: boolean;
  localOnly: boolean;
  enabled: boolean;
  autonomous: boolean;
  /** false only for python-run — its Auto checkbox renders permanently off. */
  autoCapable: boolean;
}

interface ActionSettingsResponse {
  ok: boolean;
  sensors: SensorRow[];
  actions: ActionRow[];
  gates: Record<string, boolean>;
  autonomous: Record<string, boolean>;
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

type UpdateBody = {
  gates?: Record<string, boolean>;
  autonomous?: Record<string, boolean>;
};

/** Shared write path: snapshot → optimistic patch → POST → authoritative
 *  response (the server enforces the autonomous ⇒ enabled coupling) → revert
 *  + toast on failure. */
async function postGateUpdate(
  body: UpdateBody,
  savingTag: string,
  optimistic: {
    sensor: (s: SensorRow) => SensorRow;
    action: (a: ActionRow) => ActionRow;
  },
): Promise<void> {
  const prev = agentActionGatesStore.get();
  agentActionGatesStore.patch({
    saving: savingTag,
    error: null,
    sensors: prev.sensors.map(optimistic.sensor),
    actions: prev.actions.map(optimistic.action),
  });
  try {
    const res = await fetchJson<ActionSettingsResponse>('/api/agents/action-settings/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
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
      error: e instanceof Error ? e.message : 'Failed to save the toggles.',
    });
    pushToast('error', 'Toggles not saved', {
      detail: 'The switches reverted. ' + (e instanceof Error ? e.message : ''),
    });
    throw e;
  }
}

/** Persist one bulk flip over many gates in a single request (the "Read
 *  access — all toolkit data" master toggle). Disabling also clears the
 *  Autonomous flag optimistically — the server does the same. */
export async function toggleGatesBulk(names: string[], enabled: boolean): Promise<void> {
  const nameSet = new Set(names);
  return postGateUpdate(
    { gates: Object.fromEntries(names.map((n) => [n, enabled])) },
    '__bulk__',
    {
      sensor: (s) =>
        nameSet.has(s.name) ? { ...s, enabled, autonomous: enabled && s.autonomous } : s,
      action: (a) =>
        nameSet.has(a.action) ? { ...a, enabled, autonomous: enabled && a.autonomous } : a,
    },
  );
}

/** Persist one Enabled toggle (optimistic; server response is authoritative). */
export async function toggleActionGate(name: string, enabled: boolean): Promise<void> {
  return postGateUpdate({ gates: { [name]: enabled } }, name, {
    sensor: (s) =>
      s.name === name ? { ...s, enabled, autonomous: enabled && s.autonomous } : s,
    action: (a) =>
      a.action === name ? { ...a, enabled, autonomous: enabled && a.autonomous } : a,
  });
}

/** Persist Autonomous flags for one or many capabilities. Allowing also
 *  checks Enabled optimistically (the server forces it anyway). */
export async function toggleAutonomous(names: string[], allowed: boolean): Promise<void> {
  const nameSet = new Set(names);
  return postGateUpdate(
    { autonomous: Object.fromEntries(names.map((n) => [n, allowed])) },
    names.length === 1 ? names[0] : '__bulk-auto__',
    {
      sensor: (s) =>
        nameSet.has(s.name)
          ? { ...s, autonomous: allowed, enabled: s.enabled || allowed }
          : s,
      action: (a) =>
        nameSet.has(a.action) && a.autoCapable
          ? { ...a, autonomous: allowed, enabled: a.enabled || allowed }
          : a,
    },
  );
}
