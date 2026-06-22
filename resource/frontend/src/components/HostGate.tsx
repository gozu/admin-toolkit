import { useEffect, useRef, useState } from 'react';
import { fetchJson, fetchRaw } from '../utils/api';
import { parseSseStream } from '../utils/sseStream';
import { hostStore, setHosts, setActiveHost } from '../state/hostStore';
import { ProgressIndicator } from './common/ProgressIndicator';
import type { DssHost, DssHostStatus, Lifecycle } from '../types';
import dkulogo from '../assets/dkulogo.png';

interface HostGateProps {
  onEnter: (hostId: string) => void;
}

type ProbeState = DssHostStatus | 'loading' | undefined;

// ── Install-toolkit flow (one-click bootstrap of a plugin-less remote) ──
type InstallStepKey = 'install' | 'codeenv' | 'project';
type StepStatus = 'queued' | 'active' | 'done' | 'error';
interface StepView {
  status: StepStatus;
  message: string;
}

const INSTALL_STEPS: { key: InstallStepKey; label: string }[] = [
  { key: 'install', label: 'Install plugin' },
  { key: 'codeenv', label: 'Build code env' },
  { key: 'project', label: 'Create project' },
];

// Cosmetic mirror of the backend constants (clients.py) — prefilled into the
// dialog's git fields, which the admin can override per run. Not fetched at
// runtime; the backend falls back to its own constants when these are blank.
type InstallMode = 'git' | 'upload';
const INSTALL_GIT_REPO_DEFAULT = 'git@github.com:gozu/admin-toolkit.git';
const INSTALL_GIT_BRANCH_DEFAULT = 'main';

function initialInstallSteps(): Record<InstallStepKey, StepView> {
  return {
    install: { status: 'queued', message: 'Queued' },
    codeenv: { status: 'queued', message: 'Queued' },
    project: { status: 'queued', message: 'Queued' },
  };
}

// Map a step view onto a Lifecycle so ProgressIndicator derives its own tone
// (grey queued / yellow active / white done / red error) — never a `tone` prop.
const STEP_EPOCH = '1970-01-01T00:00:00.000Z';
function stepLifecycle(step: StepView): Lifecycle {
  switch (step.status) {
    case 'queued':
      return { phase: 'queued' };
    case 'active':
      return { phase: 'running', startedAt: STEP_EPOCH, progressPct: 0, message: step.message, updatedAt: STEP_EPOCH };
    case 'done':
      return { phase: 'done', startedAt: STEP_EPOCH, finishedAt: STEP_EPOCH, isEmpty: false, message: step.message };
    case 'error':
      return { phase: 'error', startedAt: STEP_EPOCH, finishedAt: STEP_EPOCH, error: step.message, progressPct: 0 };
  }
}

function dotColor(s: ProbeState): string {
  if (s === undefined || s === 'loading') return 'bg-[var(--text-tertiary)]';
  if (!s.ok || s.pluginInstalled === false) return 'bg-[var(--neon-red)]';
  if (s.pluginVersion && s.adminToolkitProjectExists === false) return 'bg-[var(--neon-yellow)]';
  return 'bg-[var(--success)]';
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
  const cancelButtonRef = useRef<HTMLButtonElement | null>(null);
  // One-click install flow.
  const [installHost, setInstallHost] = useState<DssHost | null>(null);
  const [installSteps, setInstallSteps] = useState<Record<InstallStepKey, StepView>>(initialInstallSteps);
  const [installRunning, setInstallRunning] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);
  const [installReady, setInstallReady] = useState(false);
  const installButtonRef = useRef<HTMLButtonElement | null>(null);
  // Install source: git (default, repo/branch editable) or admin-uploaded .zip.
  const [installMode, setInstallMode] = useState<InstallMode>('git');
  const [installRepoUrl, setInstallRepoUrl] = useState(INSTALL_GIT_REPO_DEFAULT);
  const [installBranch, setInstallBranch] = useState(INSTALL_GIT_BRANCH_DEFAULT);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [installFallbackHint, setInstallFallbackHint] = useState<string | null>(null);
  const [uploadDragging, setUploadDragging] = useState(false);
  const uploadInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!setupHost) return;
    cancelButtonRef.current?.focus();
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSetupHost(null);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [setupHost]);

  useEffect(() => {
    if (!installHost) return;
    installButtonRef.current?.focus();
    const onKeyDown = (e: KeyboardEvent) => {
      // Don't let Escape interrupt an in-flight install.
      if (e.key === 'Escape' && !installRunning) setInstallHost(null);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [installHost, installRunning]);

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
      status !== undefined
      && status !== 'loading'
      && status.ok
      && status.pluginInstalled === false
    ) {
      openInstall(host);
      return;
    }
    if (
      status !== undefined
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

  function openInstall(host: DssHost) {
    setInstallHost(host);
    setInstallSteps(initialInstallSteps());
    setInstallError(null);
    setInstallReady(false);
    setInstallRunning(false);
    setInstallMode('git');
    setInstallRepoUrl(INSTALL_GIT_REPO_DEFAULT);
    setInstallBranch(INSTALL_GIT_BRANCH_DEFAULT);
    setUploadFile(null);
    setInstallFallbackHint(null);
    setUploadDragging(false);
  }

  function handleUploadDrop(e: React.DragEvent) {
    e.preventDefault();
    setUploadDragging(false);
    const f = e.dataTransfer.files[0];
    if (f?.name.toLowerCase().endsWith('.zip')) setUploadFile(f);
  }

  function handleUploadChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (f?.name.toLowerCase().endsWith('.zip')) setUploadFile(f);
    e.target.value = ''; // allow re-selecting the same file
  }

  async function runInstall() {
    if (!installHost) return;
    const hostId = installHost.id;
    const mode = installMode;
    if (mode === 'upload' && !uploadFile) return;
    setInstallRunning(true);
    setInstallError(null);
    setInstallFallbackHint(null);
    setInstallReady(false);
    setInstallSteps(initialInstallSteps());
    let completed = false;
    try {
      let init: RequestInit;
      if (mode === 'upload') {
        const fd = new FormData();
        fd.append('hostId', hostId);
        fd.append('plugin', uploadFile as File);
        // No Content-Type header — the browser sets the multipart boundary.
        init = { method: 'POST', body: fd };
      } else {
        init = {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            hostId,
            mode: 'git',
            repoUrl: installRepoUrl.trim() || INSTALL_GIT_REPO_DEFAULT,
            branch: installBranch.trim() || INSTALL_GIT_BRANCH_DEFAULT,
          }),
        };
      }
      const response = await fetchRaw('/api/hosts/install-toolkit', init);
      if (!response.ok || !response.body) {
        const body = await response.text();
        let msg = `Install failed: ${response.status} ${response.statusText}`;
        try {
          const parsed = JSON.parse(body) as { error?: string };
          if (parsed.error) msg = parsed.error;
        } catch {
          /* not JSON */
        }
        throw new Error(msg);
      }
      for await (const frame of parseSseStream(response.body)) {
        if (frame.event !== 'step') continue;
        const p = frame.payload as {
          step: InstallStepKey | 'complete';
          status: StepStatus;
          msg?: string;
          error?: string;
        };
        if (p.step === 'complete') {
          completed = true;
          continue;
        }
        const stepKey = p.step;
        const message = p.error || p.msg;
        setInstallSteps((s) => ({
          ...s,
          [stepKey]: { status: p.status, message: message || s[stepKey].message },
        }));
        if (p.status === 'error') {
          throw new Error(p.error || `${stepKey} failed`);
        }
      }
      if (!completed) {
        throw new Error('Install stream ended before completing');
      }
      const status = await probeHost(hostId);
      if (!status.ok || status.pluginInstalled === false || status.adminToolkitProjectExists === false) {
        throw new Error(status.error || 'Host is still not ready after install');
      }
      setInstallReady(true);
    } catch (err) {
      setInstallError(err instanceof Error ? err.message : String(err));
      // Auto-fallback to Option B: a failed git install reveals the upload zone
      // so the admin can retry with the plugin .zip (private / air-gapped repo).
      if (mode === 'git') {
        setInstallMode('upload');
        setInstallFallbackHint('Git install failed — upload the plugin .zip instead.');
      }
    } finally {
      setInstallRunning(false);
    }
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
            <p><strong className="text-[var(--text-primary)]">Beta, best-effort tool</strong> — not officially supported and lightly tested. Results may be wrong, so verify outputs before acting and test against a sandbox before production.</p>
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
              {setupHost.label} has the plugin installed, but it is missing the support project this toolkit
              needs on that host. Create it once and the scan can continue.
            </p>
            {setupError && (
              <div className="mt-3 rounded border border-[var(--neon-red)]/40 bg-[var(--neon-red)]/10 px-3 py-2 text-sm text-[var(--neon-red)]">
                {setupError}
              </div>
            )}
            <div className="mt-5 flex justify-end gap-2">
              <button
                ref={cancelButtonRef}
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

      {installHost && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-lg rounded-lg border border-[var(--border-glass)] bg-[var(--bg-surface)] p-5 shadow-xl">
            <h2 className="text-lg font-semibold text-[var(--text-primary)]">Install Admin Toolkit on this host</h2>
            <p className="mt-2 text-sm leading-relaxed text-[var(--text-secondary)]">
              {installHost.label} is reachable but the Admin Toolkit plugin isn't installed. Install it from git
              (default) or upload the plugin <code className="text-[var(--neon-cyan)]">.zip</code>, then this builds
              its code env and creates the support project on that host.
            </p>

            {!installReady && (
              <div className="mt-4 space-y-3">
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => setInstallMode('git')}
                    disabled={installRunning}
                    className={`rounded border px-3 py-2 text-left transition-colors disabled:opacity-60 ${
                      installMode === 'git'
                        ? 'border-[var(--neon-cyan)] bg-[var(--neon-cyan)]/10 text-[var(--text-primary)]'
                        : 'border-[var(--border-glass)] text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)]'
                    }`}
                  >
                    <div className="text-sm font-medium">From git</div>
                    <div className="text-[11px] text-[var(--text-tertiary)]">Recommended · updatable</div>
                  </button>
                  <button
                    type="button"
                    onClick={() => setInstallMode('upload')}
                    disabled={installRunning}
                    className={`rounded border px-3 py-2 text-left transition-colors disabled:opacity-60 ${
                      installMode === 'upload'
                        ? 'border-[var(--neon-cyan)] bg-[var(--neon-cyan)]/10 text-[var(--text-primary)]'
                        : 'border-[var(--border-glass)] text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)]'
                    }`}
                  >
                    <div className="text-sm font-medium">Upload .zip</div>
                    <div className="text-[11px] text-[var(--text-tertiary)]">Air-gapped fallback</div>
                  </button>
                </div>

                {installMode === 'git' ? (
                  <div className="space-y-2">
                    <label className="block">
                      <span className="text-xs font-medium text-[var(--text-secondary)]">Repository URL</span>
                      <input
                        type="text"
                        value={installRepoUrl}
                        onChange={(e) => setInstallRepoUrl(e.target.value)}
                        disabled={installRunning}
                        spellCheck={false}
                        className="mt-1 w-full rounded border border-[var(--border-glass)] bg-[var(--bg-glass)] px-2 py-1.5 font-mono text-xs text-[var(--text-primary)] focus:border-[var(--neon-cyan)] focus:outline-none disabled:opacity-60"
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs font-medium text-[var(--text-secondary)]">Branch / tag / commit</span>
                      <input
                        type="text"
                        value={installBranch}
                        onChange={(e) => setInstallBranch(e.target.value)}
                        disabled={installRunning}
                        spellCheck={false}
                        className="mt-1 w-full rounded border border-[var(--border-glass)] bg-[var(--bg-glass)] px-2 py-1.5 font-mono text-xs text-[var(--text-primary)] focus:border-[var(--neon-cyan)] focus:outline-none disabled:opacity-60"
                      />
                    </label>
                    <p className="text-[11px] text-[var(--text-tertiary)]">
                      The remote DSS must be able to reach this repo (e.g. a configured deploy key). If it can't,
                      switch to Upload .zip.
                    </p>
                  </div>
                ) : (
                  <div
                    onClick={() => { if (!installRunning) uploadInputRef.current?.click(); }}
                    onDrop={handleUploadDrop}
                    onDragOver={(e) => { e.preventDefault(); setUploadDragging(true); }}
                    onDragLeave={(e) => { e.preventDefault(); setUploadDragging(false); }}
                    className={`rounded border-2 border-dashed px-4 py-6 text-center text-sm transition-colors ${
                      installRunning ? 'pointer-events-none opacity-60' : 'cursor-pointer'
                    } ${
                      uploadDragging
                        ? 'border-[var(--neon-cyan)] bg-[var(--neon-cyan)]/10'
                        : 'border-[var(--border-glass)] hover:bg-[var(--bg-glass-hover)]'
                    }`}
                  >
                    {uploadFile ? (
                      <span className="font-mono break-all text-[var(--text-primary)]">{uploadFile.name}</span>
                    ) : (
                      <span className="text-[var(--text-secondary)]">
                        {uploadDragging ? 'Drop plugin .zip here' : 'Drop the plugin .zip here, or click to browse'}
                      </span>
                    )}
                    <input
                      ref={uploadInputRef}
                      type="file"
                      accept=".zip"
                      className="hidden"
                      onChange={handleUploadChange}
                    />
                  </div>
                )}
              </div>
            )}

            <div className="mt-4 space-y-3">
              {INSTALL_STEPS.map((def) => (
                <div key={def.key}>
                  <div className="text-xs font-medium text-[var(--text-secondary)] mb-1">{def.label}</div>
                  <ProgressIndicator lifecycle={stepLifecycle(installSteps[def.key])} compact />
                </div>
              ))}
            </div>

            {installError && (
              <div className="mt-3 rounded border border-[var(--neon-red)]/40 bg-[var(--neon-red)]/10 px-3 py-2 text-sm text-[var(--neon-red)]">
                {installError}
              </div>
            )}

            {installFallbackHint && !installReady && (
              <div className="mt-2 rounded border border-[var(--status-warning-border)] bg-[var(--status-warning-bg)] px-3 py-2 text-xs text-[var(--text-secondary)]">
                {installFallbackHint}
              </div>
            )}

            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setInstallHost(null)}
                disabled={installRunning}
                className="rounded border border-[var(--border-glass)] px-3 py-2 text-sm text-[var(--text-secondary)] hover:bg-[var(--bg-glass-hover)] disabled:opacity-60"
              >
                {installReady ? 'Close' : 'Cancel'}
              </button>
              {installReady ? (
                <button
                  type="button"
                  onClick={() => { const id = installHost.id; setInstallHost(null); enterHost(id); }}
                  className="rounded bg-[var(--accent)] px-3 py-2 text-sm font-medium text-white hover:opacity-90"
                >
                  Enter host →
                </button>
              ) : (
                <button
                  ref={installButtonRef}
                  type="button"
                  onClick={runInstall}
                  disabled={installRunning || (installMode === 'upload' && !uploadFile)}
                  className="rounded bg-[var(--accent)] px-3 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
                >
                  {installRunning ? 'Installing…' : installError ? 'Retry' : 'Install'}
                </button>
              )}
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
  const probed = status !== undefined && status !== 'loading';
  const needsInstall = probed && status.ok && status.pluginInstalled === false;
  const reachable = probed && status.ok && status.pluginInstalled !== false;
  const needsSetup = reachable && status.adminToolkitProjectExists === false;
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
          {needsInstall ? 'Install →' : needsSetup ? 'Set up →' : reachable ? 'Enter →' : 'Try →'}
        </span>
      </div>
    </button>
  );
}
