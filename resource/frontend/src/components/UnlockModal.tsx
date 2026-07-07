import { useState } from 'react';
import { Modal } from './Modal';
import { unlockAll } from '../state/unlockAll';

interface UnlockModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** Called only on full success (every configured gate accepted the password). */
  onUnlocked?: () => void;
}

// The one master password is set as a plain PASSWORD field in plugin settings;
// it unlocks both gates (advanced actions + encrypted remote-host keys).
const PLUGIN_SETTINGS_URL = '/plugins/admin-toolkit/settings/';

/**
 * Single password prompt for both gates. One password unlocks the advanced
 * ("red") action pages and any encrypted remote-host API keys; this modal
 * submits it to both endpoints and only closes when every *configured* gate
 * accepts it. The cookies are HttpOnly — the server is the source of truth.
 */
export function UnlockModal({ isOpen, onClose, onUnlocked }: UnlockModalProps) {
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  // Bumped on each wrong-password attempt to re-trigger the shake animation.
  const [shakeTick, setShakeTick] = useState(0);

  const reset = () => {
    setPassword('');
    setError('');
    setNotice('');
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
    setNotice('');

    const { red, host, hostFailed } = await unlockAll(password);
    const channels = [red, host];
    const configured = channels.filter((s) => s !== 'not-configured');
    const unlocked = configured.filter((s) => s === 'unlocked');
    const wrong = configured.filter((s) => s === 'wrong-password');

    // Nothing was configured (shouldn't happen if the modal was shown) — let the
    // caller proceed rather than trap them behind a dead prompt.
    if (configured.length === 0) {
      reset();
      onUnlocked?.();
      onClose();
      return;
    }

    // Every configured gate accepted the password.
    if (unlocked.length === configured.length) {
      if (hostFailed.length > 0) {
        // Unlocked, but some presets were encrypted with a different password.
        setNotice(
          `Unlocked, but these hosts could not be decrypted — re-save them in Settings → Remote Hosts to re-encrypt under this password: ${hostFailed.join(', ')}`,
        );
        setLoading(false);
        return;
      }
      reset();
      onUnlocked?.();
      onClose();
      return;
    }

    // Nothing accepted it → wrong password (or a transient error).
    if (unlocked.length === 0) {
      setError(wrong.length > 0 ? 'Incorrect password.' : 'Could not unlock. Please try again.');
      if (wrong.length > 0) setShakeTick((t) => t + 1);
      setLoading(false);
      return;
    }

    // Partial: this password opened one gate but not the other — they were set
    // with different passwords. The opened gate stays unlocked; tell the admin
    // how to converge on one password.
    const opened: string[] = [];
    const stuck: string[] = [];
    if (red === 'unlocked') opened.push('advanced actions');
    if (host === 'unlocked') opened.push('remote-host keys');
    if (red === 'wrong-password') stuck.push('advanced actions');
    if (host === 'wrong-password') stuck.push('remote-host keys');
    setNotice(
      `Unlocked ${opened.join(' and ')}. ${stuck.join(' and ')} use a different password — ` +
        `re-set them to this one (plugin settings / Settings → Remote Hosts), then reload.`,
    );
    setLoading(false);
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={close}
      title="Unlock"
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
          Enter your admin password to unlock the advanced action pages (delete, replace,
          migrate, deploy, send) and any encrypted remote-host API keys. One password covers
          both.
        </p>
        <div className="flex items-start gap-2 rounded border border-[var(--status-warning-border)] bg-[var(--status-warning-bg)] px-3 py-2 text-sm">
          <svg className="w-4 h-4 mt-0.5 shrink-0 text-[var(--neon-amber)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <p className="text-[var(--text-secondary)]">
            Advanced actions can permanently modify or delete DSS objects, with no undo. This
            tool has had limited testing — try them in a sandbox instance before running them
            against production.
          </p>
        </div>
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
        {notice && <div className="text-sm text-[var(--neon-amber)]">{notice}</div>}
        <p className="text-xs text-[var(--text-muted)]">
          No prompt next time — the unlock is remembered on this browser (secure cookies). The
          master password is set by an administrator in{' '}
          <a
            href={PLUGIN_SETTINGS_URL}
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
