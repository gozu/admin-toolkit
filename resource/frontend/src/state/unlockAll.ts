import { fetchJson, ApiRequestError } from '../utils/api';
import { markUnlocked as markRedUnlocked } from './redUnlockStore';
import { markUnlocked as markHostKeyUnlocked } from './hostKeyUnlockStore';

// One password now covers both gates (advanced actions + encrypted remote-host
// keys), so the webapp asks once and submits to both endpoints. Each channel is
// independently configured: an endpoint that returns 400 not-configured simply
// doesn't apply, and is reported as 'not-configured' (not a failure). A wrong
// password is 401 → 'wrong-password'. Anything else → 'error'.

export type ChannelState = 'unlocked' | 'wrong-password' | 'not-configured' | 'error';

export interface UnlockOutcome {
  red: ChannelState;
  host: ChannelState;
  /** Presets that decrypted-failed even though the password was accepted
   *  (a blob made with a different password/salt). Host channel only. */
  hostFailed: string[];
}

function classifyError(e: unknown): ChannelState {
  if (e instanceof ApiRequestError) {
    const err = (e.body as { error?: string } | undefined)?.error;
    if (e.status === 400 && err === 'not-configured') return 'not-configured';
    if (e.status === 401) return 'wrong-password';
  }
  return 'error';
}

async function unlockRed(password: string): Promise<ChannelState> {
  try {
    const res = await fetchJson<{ unlocked: boolean; expiresAt: number }>('/api/auth/red/unlock', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });
    markRedUnlocked(res.expiresAt);
    return 'unlocked';
  } catch (e) {
    return classifyError(e);
  }
}

async function unlockHost(password: string): Promise<{ state: ChannelState; failed: string[] }> {
  try {
    const res = await fetchJson<{ unlocked: boolean; expiresAt: number; failed?: string[] }>(
      '/api/hosts/keys/unlock',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      },
    );
    markHostKeyUnlocked();
    return { state: 'unlocked', failed: res.failed ?? [] };
  } catch (e) {
    return { state: classifyError(e), failed: [] };
  }
}

/** Submit one password to both unlock endpoints; each store is updated on its
 *  own success, so callers only need the combined outcome for messaging. */
export async function unlockAll(password: string): Promise<UnlockOutcome> {
  const [red, host] = await Promise.all([unlockRed(password), unlockHost(password)]);
  return { red, host: host.state, hostFailed: host.failed };
}
