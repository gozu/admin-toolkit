import { useEffect, useState } from 'react';
import { refreshUsageConfig, updateUsageEnabled, usageConfigStore } from '../state/productAnalytics';
import { useRedState } from '../state/redUnlockStore';

export function ProductAnalyticsCard({ onUnlock }: { onUnlock: () => void }) {
  const config = usageConfigStore.use();
  const { authed } = useRedState();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { void refreshUsageConfig(); }, []);
  const change = async () => {
    if (!authed) { onUnlock(); return; }
    if (!config || saving) return;
    setSaving(true);
    setError('');
    try { await updateUsageEnabled(!config.enabled); }
    catch { setError('Could not save the setting. Please retry.'); }
    finally { setSaving(false); }
  };
  return (
    <section className="glass-card p-4 space-y-3">
      <div>
        <h3 className="text-lg font-semibold text-[var(--text-primary)]">Product Usage Analytics</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Help improve Admin Toolkit by sharing feature usage with its maintainers through PostHog.
          This setting applies to everyone using this Toolkit installation, including when viewing remote hosts.
        </p>
      </div>
      <div className="flex items-center gap-3 min-h-9">
        <button type="button" role="switch" aria-checked={config?.enabled ?? false}
          aria-label="Share product usage analytics" disabled={!config || config.available === false || saving}
          onClick={() => void change()}
          className="px-3 py-1.5 rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-sm text-[var(--text-primary)] disabled:opacity-50">
          {saving ? 'Saving…' : !config ? 'Loading…' : config.available === false ? 'Unavailable' : config.enabled ? 'Enabled' : 'Disabled'}
        </button>
        <span className="text-xs text-[var(--text-muted)]">
          {config?.enabled ? 'Enabled by default. Turn off to opt out.' : 'Usage reporting is off.'}
          {!authed && ' Unlock advanced actions to change this setting.'}
        </span>
      </div>
      <p className="text-xs text-[var(--text-muted)]">
        Shares opens, module visits, scan outcomes and durations, exports, comparisons, and plugin version
        with pseudonymous user and installation IDs. Page contents, customer object names, email addresses,
        logs, and session recordings are not collected.
      </p>
      {error && <p role="alert" className="text-sm text-[var(--neon-red)]">{error}</p>}
    </section>
  );
}
