import { useState } from 'react';
import { Modal } from './Modal';
import { fetchJson, ApiRequestError } from '../utils/api';
import { markUnlocked } from '../state/hostKeyUnlockStore';

interface HostKeyUnlockModalProps {
  isOpen: boolean;
  onClose: () => void;
  onUnlocked?: () => void;
}

interface UnlockResponse {
  unlocked: boolean;
  expiresAt: number;
  failed?: string[];
}

// Where the admin turns a password + API key into an adkfk1$ blob.
const ENCRYPT_TOOL_URL = 'https://gozu.github.io/hash.html';

/**
 * Password prompt that unlocks encrypted remote-host API keys. Submits to the
 * sanctioned endpoint; the backend derives the Fernet key, verifies it decrypts
 * the stored blobs, and stores the derived key in an HttpOnly cookie so every
 * subsequent request / worker / restart can decrypt without re-persisting the
 * password.
 */
export function HostKeyUnlockModal({ isOpen, onClose, onUnlocked }: HostKeyUnlockModalProps) {
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [shakeTick, setShakeTick] = useState(0);

  const reset = () => {
    setPassword('');
    setError('');
    setLoading(false);
    setShakeTick(0);
  };

  const close = () => {
    reset();
    onClose();
  };

  const submit = async () => {
    if (!password || loading) return;
    setLoading(true);
    setError('');
    try {
      const res = await fetchJson<UnlockResponse>('/api/hosts/keys/unlock', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      markUnlocked();
      if (res.failed && res.failed.length > 0) {
        // Unlocked, but some presets were encrypted with a different salt/password.
        setError(
          `Unlocked, but these hosts could not be decrypted (re-encrypt them with the same password + salt): ${res.failed.join(', ')}`,
        );
        setLoading(false);
        return;
      }
      reset();
      onUnlocked?.();
      onClose();
    } catch (e) {
      let msg = 'Could not unlock. Please try again.';
      if (e instanceof ApiRequestError) {
        const body = e.body as { message?: string } | undefined;
        if (body?.message) msg = body.message;
        else if (e.status === 401) msg = 'Incorrect password.';
      }
      setError(msg);
      setShakeTick((t) => t + 1);
      setLoading(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={close}
      title="Unlock remote host API keys"
      footer={
        <div className="flex items-center justify-end gap-2">
          <button
            onClick={close}
            className="px-3 py-1.5 rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-[var(--text-secondary)]"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={loading || !password}
            className="px-4 py-1.5 rounded bg-[var(--accent)]/20 text-[var(--accent)] hover:bg-[var(--accent)]/30 disabled:opacity-50 transition-colors"
          >
            {loading ? 'Unlocking…' : 'Unlock'}
          </button>
        </div>
      }
    >
      <div className="space-y-4">
        <p className="text-[var(--text-secondary)]">
          One or more remote hosts store their admin API key encrypted. Enter the
          password used to encrypt them so this tool can reach those hosts.
        </p>
        <div key={shakeTick} className={shakeTick > 0 ? 'fx-shake' : undefined}>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                submit();
              }
            }}
            placeholder="Password"
            autoComplete="current-password"
            className="w-full input-glass text-sm"
            autoFocus
          />
        </div>
        {error && <div className="text-sm text-[var(--neon-red)]">{error}</div>}
        <p className="text-xs text-[var(--text-muted)]">
          No prompt next time — the unlock is remembered on this browser (a secure
          cookie). Keys are encrypted with the{' '}
          <a
            href={ENCRYPT_TOOL_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[var(--accent)] hover:underline"
          >
            encrypt tool
          </a>
          ; the same password must be used for every host. The password never
          leaves the browser.
        </p>
      </div>
    </Modal>
  );
}
