import { useEffect, useState } from 'react';
import { fetchJson } from '../utils/api';
import { hostStore, setHosts, setActiveHost } from '../state/hostStore';
import type { DssHost, DssHostStatus } from '../types';
import dkulogo from '../assets/dkulogo.png';

interface HostGateProps {
  onEnter: (hostId: string) => void;
}

type ProbeState = DssHostStatus | 'loading' | undefined;

function dotColor(s: ProbeState): string {
  if (s === undefined || s === 'loading') return 'bg-[var(--text-tertiary)]';
  if (!s.ok || s.pluginInstalled === false) return 'bg-[var(--neon-red)]';
  if (s.pluginVersion && s.adminToolkitProjectExists === false) return 'bg-[var(--neon-yellow)]';
  return 'bg-emerald-400';
}

function dotLabel(s: ProbeState): string {
  if (s === undefined || s === 'loading') return 'Probing…';
  if (!s.ok) return s.error || 'Unreachable';
  if (s.pluginInstalled === false) return 'Plugin not installed';
  if (s.pluginVersion && s.adminToolkitProjectExists === false) return `Plugin v${s.pluginVersion} · ADMINTOOLKIT project missing`;
  return s.pluginVersion ? `Plugin v${s.pluginVersion} · ready` : 'Ready';
}

export function HostGate({ onEnter }: HostGateProps) {
  const { hosts } = hostStore.use();
  const [statuses, setStatuses] = useState<Record<string, ProbeState>>({});
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [setupHost, setSetupHost] = useState<DssHost | null>(null);
  const [setupLoading, setSetupLoading] = useState(false);
  const [setupError, setSetupError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await fetchJson<DssHost[]>('/api/hosts');
        if (!cancelled) setHosts(list);
      } catch (err) {
        if (!cancelled) setFetchError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    hosts.forEach(async (h) => {
      setStatuses((s) => (s[h.id] ? s : { ...s, [h.id]: 'loading' }));
      try {
        const result = await fetchJson<DssHostStatus>('/api/hosts/check', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ hostId: h.id }),
        });
        if (!cancelled) setStatuses((s) => ({ ...s, [h.id]: result }));
      } catch (err) {
        if (!cancelled) setStatuses((s) => ({ ...s, [h.id]: { ok: false, error: String(err) } }));
      }
    });
    return () => { cancelled = true; };
  }, [hosts]);

  async function probeHost(hostId: string): Promise<DssHostStatus> {
    const result = await fetchJson<DssHostStatus>('/api/hosts/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hostId }),
    });
    setStatuses((s) => ({ ...s, [hostId]: result }));
    return result;
  }

  function enterHost(id: string) {
    setActiveHost(id);
    onEnter(id);
  }

  function handlePick(host: DssHost) {
    const status = statuses[host.id];
    if (
      host.id !== 'local'
      && status !== undefined
      && status !== 'loading'
      && status.ok
      && status.pluginInstalled !== false
      && status.adminToolkitProjectExists === false
    ) {
      setSetupHost(host);
      setSetupError(null);
      return;
    }
    enterHost(host.id);
  }

  async function confirmSetup() {
    if (!setupHost) return;
    setSetupLoading(true);
    setSetupError(null);
    try {
      const created = await fetchJson<{ ok: boolean; error?: string }>('/api/hosts/macro-project', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hostId: setupHost.id }),
      });
      if (!created.ok) {
        throw new Error(created.error || 'Failed to create ADMINTOOLKIT project');
      }
      const status = await probeHost(setupHost.id);
      if (!status.ok || status.pluginInstalled === false || status.adminToolkitProjectExists === false) {
        throw new Error(status.error || 'Remote support project is still not ready');
      }
      const hostId = setupHost.id;
      setSetupHost(null);
      enterHost(hostId);
    } catch (err) {
      setSetupError(err instanceof Error ? err.message : String(err));
    } finally {
      setSetupLoading(false);
    }
  }

  const local = hosts.find((h) => h.id === 'local');
  const remotes = hosts.filter((h) => h.id !== 'local');

  return (
    <div className="min-h-screen flex flex-col bg-[var(--bg-app)] text-[var(--text-primary)]">
      <header className="px-6 py-5 flex items-center justify-center">
        <div className="flex items-center gap-3">
          <img src={dkulogo} alt="Dataiku" className="h-9 w-9 shrink-0" />
          <div className="flex items-baseline gap-3">
            <span className="text-2xl font-bold tracking-tight">
              ADMIN
            </span>
            <span className="text-2xl font-bold tracking-tight text-[#2AB1AC]">
              TOOLKIT
            </span>
            <span className="px-2 py-0.5 text-[10px] font-mono font-medium rounded
                            bg-[var(--neon-cyan)]/10 text-[var(--neon-cyan)]
                            border border-[var(--neon-cyan)]/30">
              BETA
            </span>
          </div>
        </div>
      </header>

      <main className="flex-1 px-6 pb-10 w-full">
        <h1 className="text-2xl font-bold mb-2 text-center">Pick a host to scan</h1>
        <p className="text-sm text-[var(--text-secondary)] mb-6 text-center">
          Local DSS is always available. To add remote hosts, configure them as{' '}
          <code className="px-1 py-0.5 rounded bg-[var(--bg-glass)] text-[var(--neon-cyan)]">Remote DSS Hosts</code>{' '}
          presets in plugin settings.
        </p>

        <div className="mb-6 mx-auto max-w-3xl rounded-lg border border-[var(--status-warning-border)] bg-[var(--status-warning-bg)] px-4 py-3 text-sm text-[var(--text-secondary)] space-y-2">
          <div className="flex items-start gap-2">
            <svg className="w-4 h-4 mt-0.5 shrink-0 text-[var(--neon-amber)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <p>This tool is <strong className="text-[var(--text-primary)]">not officially supported</strong> and has had limited testing — it's provided on a best-effort basis and is in beta. Results may be incorrect; verify outputs before acting on them, and try it against a sandbox instance before production.</p>
          </div>
          <div className="flex items-start gap-2">
            <svg className="w-4 h-4 mt-0.5 shrink-0 text-[var(--neon-cyan)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <p><strong className="text-[var(--text-primary)]">Platinum support only.</strong> Do not contact <span className="font-mono">support@dataiku.com</span>. Route all requests through your TAM or <a href="mailto:alex.kaos@dataiku.com" className="text-[var(--accent)] hover:underline">alex.kaos@dataiku.com</a>.</p>
          </div>
        </div>

        {fetchError && (
          <div className="mb-6 p-3 rounded-lg border border-[var(--neon-red)]/40 bg-[var(--neon-red)]/10 text-sm text-[var(--neon-red)]">
            Couldn't load host list: {fetchError}
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {local && (
            <HostCard
              host={local}
              status={statuses[local.id]}
              accent="local"
              onClick={() => handlePick(local)}
            />
          )}
          {remotes.length === 0 && (
            <div className="px-5 py-6 rounded-lg border border-dashed border-[var(--border-glass)] text-sm text-[var(--text-tertiary)] text-center">
              No remote DSS hosts configured. Add one in <span className="font-mono">Plugins → Admin Toolkit → Define instances of Remote DSS Hosts</span>.
            </div>
          )}
          {remotes.map((h) => (
            <HostCard
              key={h.id}
              host={h}
              status={statuses[h.id]}
              accent="remote"
              onClick={() => handlePick(h)}
            />
          ))}
        </div>
      </main>

      {setupHost && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-lg rounded-lg border border-[var(--border-glass)] bg-[var(--bg-surface)] p-5 shadow-xl">
            <h2 className="text-lg font-semibold text-[var(--text-primary)]">Create remote support project</h2>
            <p className="mt-2 text-sm leading-relaxed text-[var(--text-secondary)]">
              {setupHost.label} has the plugin installed, but it is missing the ADMINTOOLKIT project used for
              target-host macros and backups. Create it once on that host, then the scan can continue.
            </p>
            {setupError && (
              <div className="mt-3 rounded border border-[var(--neon-red)]/40 bg-[var(--neon-red)]/10 px-3 py-2 text-sm text-[var(--neon-red)]">
                {setupError}
              </div>
            )}
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setSetupHost(null)}
                disabled={setupLoading}
                className="rounded border border-[var(--border-glass)] px-3 py-2 text-sm text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] disabled:opacity-60"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={confirmSetup}
                disabled={setupLoading}
                className="rounded bg-[var(--accent)] px-3 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
              >
                {setupLoading ? 'Creating...' : 'Create and scan'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

interface HostCardProps {
  host: DssHost;
  status: ProbeState;
  accent: 'local' | 'remote';
  onClick: () => void;
}

function HostCard({ host, status, accent, onClick }: HostCardProps) {
  const accentBorder = accent === 'local' ? 'border-[var(--neon-cyan)]/40' : 'border-[var(--border-glass)]';
  const reachable = status !== undefined && status !== 'loading' && status.ok && status.pluginInstalled !== false;
  const needsSetup = reachable && host.id !== 'local' && status.adminToolkitProjectExists === false;
  return (
    <button
      onClick={onClick}
      className={`w-full min-h-[88px] text-left px-4 py-3 rounded-lg border ${accentBorder}
                  bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)]
                  hover:border-[var(--neon-cyan)] transition-colors group`}
    >
      <div className="flex items-center gap-3">
        <span className={`inline-block w-2.5 h-2.5 rounded-full shrink-0 ${dotColor(status)}`} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-base font-semibold truncate">{host.label}</span>
            {accent === 'local' && (
              <span className="px-1.5 py-0.5 text-[10px] font-mono rounded
                              bg-[var(--neon-cyan)]/10 text-[var(--neon-cyan)]
                              border border-[var(--neon-cyan)]/30">
                LOCAL
              </span>
            )}
          </div>
          {host.url && (
            <div className="text-xs text-[var(--text-tertiary)] font-mono break-all leading-snug">{host.url}</div>
          )}
          <div className="text-xs text-[var(--text-secondary)] mt-1">{dotLabel(status)}</div>
        </div>
        <span className="text-xs text-[var(--text-tertiary)] group-hover:text-[var(--neon-cyan)] transition-colors shrink-0">
          {needsSetup ? 'Set up →' : reachable ? 'Enter →' : 'Try →'}
        </span>
      </div>
    </button>
  );
}
