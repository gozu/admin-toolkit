import { useState, useEffect } from 'react';
import { useDiag } from '../../context/DiagContext';
import { loadFromStorage, saveToStorage } from '../../utils/storage';
import { useToggleFlag } from '../../hooks/useToggleFlag';
import { useRedState, forgetRed } from '../../state/redUnlockStore';
import { UnlockModal } from '../UnlockModal';
import { AgentsOutreachCard } from '../AgentsOutreachCard';
import { RemoteHostsCard } from '../RemoteHostsCard';
import { FindingWhitelistCard } from '../FindingWhitelistCard';
import { AlgorithmReviewCard } from '../AlgorithmReviewCard';
import { PerfAutoTuneCard } from '../PerfAutoTuneCard';
import { SupportBundleCard } from '../SupportBundleCard';
import { datasetExportConfigStore } from '../../state/datasetExportConfigStore';

export const SELECTED_MAIL_CHANNEL_STORAGE_KEY = 'selectedMailChannel';
export const SHOW_EXPERIMENTAL_STORAGE_KEY = 'showExperimental';
export const SHOW_DEPRECATED_STORAGE_KEY = 'showDeprecated';

// Where the admin sets the one master password (a PASSWORD field).
const PLUGIN_SETTINGS_URL = '/plugins/admin-toolkit/settings/';

export function SettingsPage() {
  const { state } = useDiag();
  const mailChannels = state.parsedData.mailChannels ?? [];

  const [stored, setStored] = useState<string>(() =>
    loadFromStorage<string>(SELECTED_MAIL_CHANNEL_STORAGE_KEY, ''),
  );

  const [showDeprecated, setShowDeprecated] = useToggleFlag(
    SHOW_DEPRECATED_STORAGE_KEY,
    'deprecated-flag-changed',
  );

  const { authed: unlocked, expiresAt } = useRedState();
  const [showUnlock, setShowUnlock] = useState(false);

  const {
    configuredConnection: datasetExportConnection,
    project: datasetExportProject,
    loaded: datasetExportLoaded,
  } = datasetExportConfigStore.use();
  useEffect(() => {
    datasetExportConfigStore.loadConfig();
  }, []);

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
              No mail channels available. They load shortly after the page opens.
            </p>
          )}
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
          set one master password in{' '}
          <a
            href={PLUGIN_SETTINGS_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[var(--accent)] hover:underline"
          >
            plugin settings → “Master password”
          </a>
          . It unlocks advanced actions here, encrypts remote-host API keys, and powers the
          agents.
        </p>
      </section>

      <AgentsOutreachCard />

      <RemoteHostsCard />

      <FindingWhitelistCard />

      <PerfAutoTuneCard />

      <AlgorithmReviewCard />

      <SupportBundleCard />

      <section className="glass-card p-4 space-y-3">
        <div>
          <h3 className="text-lg font-semibold text-[var(--text-primary)]">Save Tables as Datasets</h3>
          <p className="text-sm text-[var(--text-muted)]">
            The toolbar “Save tables as datasets” button persists every table on the current page as a
            managed Dataiku dataset (one per table) in the Admin Toolkit’s own project. All columns are
            stored as text; re-running overwrites the datasets in place.
          </p>
        </div>
        {datasetExportLoaded && datasetExportConnection ? (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="px-2 py-0.5 text-xs font-medium rounded border bg-white/10 text-[var(--text-primary)] border-[var(--border-default)]">
              Enabled
            </span>
            <span className="text-[var(--text-secondary)]">
              saving to connection{' '}
              <span className="font-mono text-[var(--text-primary)]">{datasetExportConnection}</span>
              {datasetExportProject && (
                <>
                  {' '}in project{' '}
                  <span className="font-mono text-[var(--text-primary)]">{datasetExportProject}</span>
                </>
              )}
            </span>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="px-2 py-0.5 text-xs font-medium rounded border bg-[var(--bg-glass)] text-[var(--text-secondary)] border-[var(--border-default)]">
              Not configured
            </span>
            <span className="text-[var(--text-muted)]">
              An administrator must select a target connection in Plugin settings → “Save Tables as
              Datasets” to enable the toolbar button.
            </span>
          </div>
        )}
      </section>

      <section className="glass-card p-4 space-y-3">
        <div>
          <h3 className="text-lg font-semibold text-[var(--text-primary)]">Deprecated Features</h3>
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

      <UnlockModal isOpen={showUnlock} onClose={() => setShowUnlock(false)} />
    </div>
  );
}
