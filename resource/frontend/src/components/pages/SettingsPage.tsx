import { useState } from 'react';
import { useDiag } from '../../context/DiagContext';
import { loadFromStorage, saveToStorage } from '../../utils/storage';
import { useToggleFlag } from '../../hooks/useToggleFlag';
import { useRedState, forgetRed } from '../../state/redUnlockStore';
import { RedUnlockModal } from '../RedUnlockModal';

export const SELECTED_MAIL_CHANNEL_STORAGE_KEY = 'selectedMailChannel';
export const SHOW_EXPERIMENTAL_STORAGE_KEY = 'showExperimental';
export const SHOW_DEPRECATED_STORAGE_KEY = 'showDeprecated';
export const SIDEBAR_CLASSIC_STORAGE_KEY = 'sidebarClassic';

// The only place to turn a password into a secret. Hosted on the admin's public
// page; type the password there and paste the result into the plugin setting.
const SECRET_PAGE_URL = 'https://gozu.github.io/hash.html';

export function SettingsPage() {
  const { state } = useDiag();
  const mailChannels = state.parsedData.mailChannels ?? [];

  const [stored, setStored] = useState<string>(() =>
    loadFromStorage<string>(SELECTED_MAIL_CHANNEL_STORAGE_KEY, ''),
  );

  const [showExperimental, setShowExperimental] = useToggleFlag(
    SHOW_EXPERIMENTAL_STORAGE_KEY,
    'experimental-flag-changed',
  );

  const [showDeprecated, setShowDeprecated] = useToggleFlag(
    SHOW_DEPRECATED_STORAGE_KEY,
    'deprecated-flag-changed',
  );

  const [classicSidebar, setClassicSidebar] = useToggleFlag(
    SIDEBAR_CLASSIC_STORAGE_KEY,
    'sidebar-style-changed',
  );

  const { authed: unlocked, expiresAt } = useRedState();
  const [showUnlock, setShowUnlock] = useState(false);

  const isStoredValid = !!stored && mailChannels.some((c) => c.id === stored);
  const selectedChannel = isStoredValid ? stored : mailChannels[0]?.id ?? '';

  const handleChange = (id: string) => {
    setStored(id);
    saveToStorage(SELECTED_MAIL_CHANNEL_STORAGE_KEY, id);
  };

  return (
    <div className="w-full py-4 flex flex-col gap-4">
      <section className="glass-card p-4 space-y-3">
        <div>
          <h3 className="text-lg font-semibold text-[var(--text-primary)]">Messaging</h3>
          <p className="text-sm text-[var(--text-muted)]">
            Select the DSS mail channel used for outreach emails. Only email-type messaging channels are listed.
          </p>
        </div>
        <label className="block space-y-1 max-w-sm">
          <span className="text-sm font-medium text-[var(--text-primary)]">DSS Mail Channel</span>
          {mailChannels.length > 0 ? (
            <select
              value={selectedChannel}
              onChange={(e) => handleChange(e.target.value)}
              className="mt-1 input-glass w-full"
            >
              {mailChannels.map((channel) => (
                <option key={channel.id} value={channel.id}>
                  {channel.label}
                </option>
              ))}
            </select>
          ) : (
            <p className="text-xs text-[var(--text-muted)] italic mt-1">
              No mail channels available. They load during Phase 2 of the main loader.
            </p>
          )}
        </label>
      </section>

      <section className="glass-card p-4 space-y-3">
        <div>
          <h3 className="text-lg font-semibold text-[var(--text-primary)]">Sidebar layout</h3>
          <p className="text-sm text-[var(--text-muted)]">
            Choose the navigation style: collapsible big-icon tiles (default), or the
            classic always-expanded icon + label list.
          </p>
        </div>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={classicSidebar}
            onChange={(e) => setClassicSidebar(e.target.checked)}
            className="h-4 w-4 accent-[var(--accent)]"
          />
          <span className="text-sm font-medium text-[var(--text-primary)]">
            Use the classic sidebar (icon + label list)
          </span>
        </label>
      </section>

      <section className="glass-card p-4 space-y-3">
        <div>
          <h3 className="text-lg font-semibold text-[var(--text-primary)]">Advanced Actions</h3>
          <p className="text-sm text-[var(--text-muted)]">
            Delete / replace / migrate / deploy / send pages are hidden and server-blocked
            until unlocked with the password. The unlock is remembered on this browser in a
            secure cookie — hiding the pages from the toolbar keeps you signed in (no re-prompt).
            Use “Forget on this device” to require the password again.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <span
            className={`px-2 py-0.5 text-xs font-medium rounded border ${
              unlocked
                ? 'bg-[var(--neon-red)]/20 text-[var(--neon-red)] border-[var(--neon-red)]/50'
                : 'bg-[var(--bg-glass)] text-[var(--text-secondary)] border-[var(--border-default)]'
            }`}
          >
            {unlocked ? 'Unlocked' : 'Locked'}
          </span>
          {unlocked && expiresAt > 0 && (
            <span className="text-xs text-[var(--text-muted)]">
              until {new Date(expiresAt).toLocaleString()}
            </span>
          )}
          {unlocked ? (
            <button
              type="button"
              onClick={() => void forgetRed()}
              className="px-3 py-1.5 rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-sm text-[var(--text-secondary)] transition-colors"
            >
              Forget on this device
            </button>
          ) : (
            <button
              type="button"
              onClick={() => setShowUnlock(true)}
              className="px-3 py-1.5 rounded bg-[var(--neon-red)]/20 text-[var(--neon-red)] hover:bg-[var(--neon-red)]/30 text-sm transition-colors"
            >
              Unlock…
            </button>
          )}
        </div>

        <p className="text-sm text-[var(--text-muted)]">
          <span className="font-medium text-[var(--text-secondary)]">Admin setup:</span>{' '}
          open the{' '}
          <a
            href={SECRET_PAGE_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[var(--accent)] hover:underline"
          >
            secret generator
          </a>
          , type a password to get a secret, then paste it into Plugin settings →
          “Advanced Actions secret”. The plaintext never leaves the browser.
        </p>
      </section>

      <section className="glass-card p-4 space-y-3">
        <div>
          <h3 className="text-lg font-semibold text-[var(--text-primary)]">Experimental features</h3>
          <p className="text-sm text-[var(--text-muted)]">
            Reveals in-progress tools that are still rough: Docker Images and Model Audit.
          </p>
        </div>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={showExperimental}
            onChange={(e) => setShowExperimental(e.target.checked)}
            className="h-4 w-4 accent-[var(--accent)]"
          />
          <span className="text-sm font-medium text-[var(--text-primary)]">
            Show experimental features
          </span>
        </label>
      </section>

      <section className="glass-card p-4 space-y-3">
        <div>
          <h3 className="text-lg font-semibold text-[var(--text-primary)]">Deprecated features</h3>
          <p className="text-sm text-[var(--text-muted)]">
            Reveals modules slated for removal.
          </p>
        </div>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={showDeprecated}
            onChange={(e) => setShowDeprecated(e.target.checked)}
            className="h-4 w-4 accent-[var(--accent)]"
          />
          <span className="text-sm font-medium text-[var(--text-primary)]">
            Show deprecated features
          </span>
        </label>
      </section>

      <RedUnlockModal isOpen={showUnlock} onClose={() => setShowUnlock(false)} />
    </div>
  );
}
