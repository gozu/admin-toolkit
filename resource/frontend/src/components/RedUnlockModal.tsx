import { useState } from 'react';
import { Modal } from './Modal';
import { fetchJson, ApiRequestError } from '../utils/api';
import { markUnlocked } from '../state/redUnlockStore';

interface RedUnlockModalProps {
  isOpen: boolean;
  onClose: () => void;
  onUnlocked?: () => void;
}

interface UnlockResponse {
  unlocked: boolean;
  expiresAt: number;
}

/**
 * Plaintext password prompt that unlocks the advanced ("red") action pages.
 * Submits to the one sanctioned auth endpoint; the backend hashes + compares to
 * the admin-configured plugin-settings secret and returns a persisted token.
 */
export function RedUnlockModal({ isOpen, onClose, onUnlocked }: RedUnlockModalProps) {
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const reset = () => {
    setPassword('');
    setError('');
    setLoading(false);
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
      const res = await fetchJson<UnlockResponse>('/api/auth/red/unlock', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      markUnlocked(res.expiresAt);
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
      setLoading(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={close}
      title="Unlock Advanced Actions"
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
            className="px-4 py-1.5 rounded bg-[var(--neon-red)]/20 text-[var(--neon-red)] hover:bg-[var(--neon-red)]/30 disabled:opacity-50 transition-colors"
          >
            {loading ? 'Unlocking…' : 'Unlock'}
          </button>
        </div>
      }
    >
      <div className="space-y-4">
        <p className="text-[var(--text-secondary)]">
          Advanced actions (delete, replace, migrate, deploy, send) are locked.
          Enter the password to reveal and enable them.
        </p>
        <div className="flex items-start gap-2 rounded border border-[var(--status-warning-border)] bg-[var(--status-warning-bg)] px-3 py-2 text-sm">
          <svg className="w-4 h-4 mt-0.5 shrink-0 text-[var(--neon-amber)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <p className="text-[var(--text-secondary)]">These actions can permanently modify or delete DSS objects, with no undo. This tool has had limited testing — try these actions in a sandbox instance before running them against production.</p>
        </div>
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
        {error && <div className="text-sm text-[var(--neon-red)]">{error}</div>}
        <p className="text-xs text-[var(--text-muted)]">
          No prompt next time — the unlock is remembered on this browser (a
          secure cookie). The password is set by an administrator in the{' '}
          <a
            href="/plugins/admin-toolkit/settings/"
            target="_blank"
            rel="noopener noreferrer"
            className="text-[var(--accent)] hover:underline"
          >
            plugin settings
          </a>
          .
        </p>
      </div>
    </Modal>
  );
}
