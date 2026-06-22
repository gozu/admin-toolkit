import { useEffect, useState } from 'react';
import { Modal } from './Modal';
import { UnlockModal } from './UnlockModal';
import { DataGrid } from './common/DataGrid';
import { ProgressIndicator } from './common/ProgressIndicator';
import { ConfirmDeleteDialog } from './common/ConfirmDeleteDialog';
import { useRedState } from '../state/redUnlockStore';
import { useHostKeyState, forgetHostKey } from '../state/hostKeyUnlockStore';
import {
  useRemoteHosts,
  loadHosts,
  refreshAfterMutation,
  type RemoteHostRow,
  type KeyStatus,
} from '../state/remoteHostsStore';
import { fetchJson, ApiRequestError } from '../utils/api';
import type { ColumnDef } from '../utils/dataGridTypes';
import type { Lifecycle, DssHostStatus } from '../types';

// Where the admin sets the master password (Advanced Actions secret) when the
// host-key encryption gate isn't configured yet.
const SECRET_PAGE_URL = '/plugins/admin-toolkit/resource/hash.html';
const PLUGIN_SETTINGS_URL = '/plugins/admin-toolkit/settings/';

const EPOCH = '1970-01-01T00:00:00.000Z';
const LIST_LOADING_LC: Lifecycle = {
  phase: 'running',
  startedAt: EPOCH,
  progressPct: 0,
  message: 'Loading remote hosts…',
  updatedAt: EPOCH,
};
const runningLc = (message: string): Lifecycle => {
  const t = new Date().toISOString();
  return { phase: 'running', startedAt: t, progressPct: 0, message, updatedAt: t };
};
const doneLc = (message: string): Lifecycle => {
  const t = new Date().toISOString();
  return { phase: 'done', startedAt: t, finishedAt: t, isEmpty: false, message };
};
const errorLc = (error: string): Lifecycle => {
  const t = new Date().toISOString();
  return { phase: 'error', startedAt: t, finishedAt: t, error, progressPct: 0 };
};

const KEY_BADGE: Record<KeyStatus, { cls: string; label: string }> = {
  encrypted: {
    cls: 'bg-[var(--accent)]/20 text-[var(--accent)] border-[var(--accent)]/50',
    label: 'Encrypted',
  },
  plaintext: {
    cls: 'bg-[var(--status-warning-bg)] text-[var(--neon-amber)] border-[var(--status-warning-border)]',
    label: 'Plaintext',
  },
  none: {
    cls: 'bg-[var(--bg-glass)] text-[var(--text-secondary)] border-[var(--border-default)]',
    label: 'No key',
  },
};

interface TestState {
  lifecycle: Lifecycle;
  result?: DssHostStatus;
}

export function RemoteHostsCard() {
  const { authed } = useRedState();
  const { configured: hostKeyConfigured, unlocked: hostKeyUnlocked } = useHostKeyState();
  const { rows, loading, error } = useRemoteHosts();

  const [showUnlock, setShowUnlock] = useState(false);
  const [editHost, setEditHost] = useState<RemoteHostRow | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [deleteHost, setDeleteHost] = useState<RemoteHostRow | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [tests, setTests] = useState<Record<string, TestState>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  // Managing hosts is advanced-gated; only load the list once red-unlocked
  // (the endpoint 403s otherwise). Deps are non-empty, so this is not a
  // bootstrap mount-only effect.
  useEffect(() => {
    if (authed) void loadHosts();
  }, [authed]);

  const openAdd = () => {
    setEditHost(null);
    setEditOpen(true);
  };
  const openEdit = (host: RemoteHostRow) => {
    setEditHost(host);
    setEditOpen(true);
  };

  const testHost = async (name: string) => {
    setExpanded((prev) => new Set(prev).add(name));
    setTests((prev) => ({ ...prev, [name]: { lifecycle: runningLc('Probing host…') } }));
    try {
      const res = await fetchJson<DssHostStatus>('/api/hosts/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hostId: name }),
      });
      const ok = res.ok !== false;
      setTests((prev) => ({
        ...prev,
        [name]: {
          lifecycle: ok ? doneLc('Reachable') : errorLc(res.error || 'Unreachable'),
          result: res,
        },
      }));
    } catch (e) {
      setTests((prev) => ({
        ...prev,
        [name]: { lifecycle: errorLc(e instanceof Error ? e.message : 'Probe failed') },
      }));
    }
  };

  const confirmDelete = async () => {
    if (!deleteHost) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await fetchJson(`/api/hosts/presets/${encodeURIComponent(deleteHost.name)}`, {
        method: 'DELETE',
      });
      await refreshAfterMutation();
      setDeleteHost(null);
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : 'Failed to delete host.');
    } finally {
      setDeleting(false);
    }
  };

  const columns: ColumnDef<RemoteHostRow>[] = [
    {
      id: 'label',
      label: 'Label',
      sortValue: (r) => r.label.toLowerCase(),
      render: (r) => <span className="font-medium text-[var(--text-primary)]">{r.label}</span>,
    },
    {
      id: 'url',
      label: 'URL',
      sortValue: (r) => r.url.toLowerCase(),
      mono: true,
      render: (r) => <span className="text-[var(--text-secondary)] text-xs">{r.url}</span>,
    },
    {
      id: 'keyStatus',
      label: 'Key',
      sortValue: (r) => r.keyStatus,
      render: (r) => {
        const b = KEY_BADGE[r.keyStatus];
        return (
          <span className={`px-2 py-0.5 text-xs font-medium rounded border ${b.cls}`}>{b.label}</span>
        );
      },
    },
    {
      id: 'actions',
      label: 'Actions',
      align: 'right',
      render: (r) => (
        <div className="flex items-center justify-end gap-3 text-xs">
          <button
            type="button"
            onClick={() => void testHost(r.name)}
            className="text-[var(--accent)] hover:underline"
          >
            Test
          </button>
          <button
            type="button"
            onClick={() => openEdit(r)}
            className="text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:underline"
          >
            Edit
          </button>
          <button
            type="button"
            onClick={() => {
              setDeleteError(null);
              setDeleteHost(r);
            }}
            className="text-[var(--neon-red)] hover:underline"
          >
            Delete
          </button>
        </div>
      ),
    },
  ];

  const renderTestRow = (row: RemoteHostRow) => {
    const t = tests[row.name];
    if (!t) return null;
    const res = t.result;
    return (
      <div className="px-4 py-3 space-y-2 bg-[var(--bg-glass)]">
        <ProgressIndicator lifecycle={t.lifecycle} compact />
        {res && res.ok !== false && (
          <div className="flex flex-wrap gap-2 text-xs">
            <ResultPill
              ok={!!res.pluginInstalled}
              label={
                res.pluginInstalled ? `Plugin v${res.pluginVersion ?? '?'}` : 'Plugin not installed'
              }
            />
            <ResultPill
              ok={!!res.adminToolkitProjectExists}
              label={res.adminToolkitProjectExists ? 'ADMINTOOLKIT project' : 'Support project missing'}
            />
          </div>
        )}
      </div>
    );
  };

  return (
    <section className="glass-card p-4 space-y-3">
      <div>
        <h3 className="text-lg font-semibold text-[var(--text-primary)]">Remote Hosts</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Add, edit, or remove the remote DSS instances this toolkit can scan. API keys are
          encrypted at rest server-side — add a host below and the key is sealed into an{' '}
          <code className="text-[var(--accent)]">adkfk1$…</code> blob automatically.
        </p>
      </div>

      {/* Host-key unlock status — a fresh browser must unlock once to decrypt
          keys at runtime, independent of managing the host list. */}
      <div className="flex items-center gap-3">
        <span
          className={`px-2 py-0.5 text-xs font-medium rounded border ${
            !hostKeyConfigured
              ? 'bg-[var(--bg-glass)] text-[var(--text-secondary)] border-[var(--border-default)]'
              : hostKeyUnlocked
                ? 'bg-[var(--accent)]/20 text-[var(--accent)] border-[var(--accent)]/50'
                : 'bg-[var(--status-warning-bg)] text-[var(--neon-amber)] border-[var(--status-warning-border)]'
          }`}
        >
          {!hostKeyConfigured ? 'No encrypted keys' : hostKeyUnlocked ? 'Keys unlocked' : 'Keys locked'}
        </span>
        {hostKeyConfigured && hostKeyUnlocked ? (
          <button
            type="button"
            onClick={() => void forgetHostKey()}
            className="px-3 py-1.5 rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-sm text-[var(--text-secondary)] transition-colors"
          >
            Forget on this device
          </button>
        ) : (
          <button
            type="button"
            onClick={() => setShowUnlock(true)}
            disabled={!hostKeyConfigured}
            className="px-3 py-1.5 rounded bg-[var(--accent)]/20 text-[var(--accent)] hover:bg-[var(--accent)]/30 disabled:opacity-40 text-sm transition-colors"
          >
            Unlock…
          </button>
        )}
      </div>

      {authed ? (
        <>
          <div className="flex items-center justify-between gap-3">
            <h4 className="text-sm font-medium text-[var(--text-secondary)]">Configured hosts</h4>
            <button
              type="button"
              onClick={openAdd}
              className="px-3 py-1.5 rounded bg-[var(--accent)]/20 text-[var(--accent)] hover:bg-[var(--accent)]/30 text-sm transition-colors"
            >
              + Add host
            </button>
          </div>
          <DataGrid<RemoteHostRow>
            rows={rows}
            columns={columns}
            rowKey={(r) => r.name}
            defaultSortColumnId="label"
            defaultSortDir="asc"
            lifecycle={loading && rows.length === 0 ? LIST_LOADING_LC : null}
            emptyMessage="No remote hosts configured yet. Click “Add host” to connect one."
            renderExpandedRow={renderTestRow}
            expandedRowKeys={expanded}
          />
          {error && <div className="text-sm text-[var(--neon-red)]">{error}</div>}
        </>
      ) : (
        <div className="flex flex-wrap items-center gap-3 rounded border border-[var(--border-default)] bg-[var(--bg-glass)] px-3 py-2.5 text-sm">
          <span className="text-[var(--text-secondary)]">
            Managing remote hosts is an advanced action. Unlock to add, edit, or remove hosts.
          </span>
          <button
            type="button"
            onClick={() => setShowUnlock(true)}
            className="px-3 py-1.5 rounded bg-[var(--neon-red)]/20 text-[var(--neon-red)] hover:bg-[var(--neon-red)]/30 text-sm transition-colors"
          >
            Unlock…
          </button>
        </div>
      )}

      <UnlockModal isOpen={showUnlock} onClose={() => setShowUnlock(false)} />
      {editOpen && (
        <HostEditModal
          host={editHost}
          onClose={() => setEditOpen(false)}
          onSaved={() => setEditOpen(false)}
        />
      )}
      <ConfirmDeleteDialog
        isOpen={deleteHost !== null}
        onClose={() => setDeleteHost(null)}
        title="Delete remote host"
        confirmPhrase={deleteHost?.label ?? ''}
        confirmLabel="Delete host"
        loadingLabel="Deleting…"
        loading={deleting}
        error={deleteError}
        onConfirm={() => void confirmDelete()}
      >
        <p className="text-sm text-[var(--text-secondary)]">
          This removes the preset for{' '}
          <span className="font-medium text-[var(--text-primary)]">{deleteHost?.label}</span> and its
          stored API key. The remote DSS instance itself is not affected.
        </p>
      </ConfirmDeleteDialog>
    </section>
  );
}

function ResultPill({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`px-2 py-0.5 rounded border ${
        ok
          ? 'bg-white/10 text-[var(--text-primary)] border-[var(--border-default)]'
          : 'bg-[var(--status-warning-bg)] text-[var(--neon-amber)] border-[var(--status-warning-border)]'
      }`}
    >
      {label}
    </span>
  );
}

interface HostEditModalProps {
  host: RemoteHostRow | null;
  onClose: () => void;
  onSaved: () => void;
}

function HostEditModal({ host, onClose, onSaved }: HostEditModalProps) {
  const editing = host !== null;
  const [label, setLabel] = useState(host?.label ?? '');
  const [url, setUrl] = useState(host?.url ?? '');
  const [apiKey, setApiKey] = useState('');
  const [verifyTls, setVerifyTls] = useState(host?.verifyTls ?? true);
  const [backupProjectKey, setBackupProjectKey] = useState(host?.backupProjectKey ?? '');
  const [password, setPassword] = useState('');
  const [needPassword, setNeedPassword] = useState(false);
  const [advancedNotConfigured, setAdvancedNotConfigured] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [shakeTick, setShakeTick] = useState(0);

  const canSubmit =
    !!label.trim() &&
    !!url.trim() &&
    (editing || !!apiKey.trim()) &&
    (!needPassword || !!password);

  const submit = async () => {
    if (loading || !canSubmit) return;
    setLoading(true);
    setError('');
    setAdvancedNotConfigured(false);

    const body: Record<string, unknown> = {
      label: label.trim(),
      url: url.trim(),
      verifyTls,
      backupProjectKey: backupProjectKey.trim(),
    };
    if (editing) body.name = host!.name;
    if (apiKey.trim()) body.apiKey = apiKey.trim();
    if (needPassword && password) body.password = password;

    try {
      const res = await fetchJson<{ ok: boolean; needPassword?: boolean }>('/api/hosts/presets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      // Path B couldn't find an already-unlocked key → ask for the master
      // password and resubmit as path A.
      if (res.needPassword) {
        setNeedPassword(true);
        setLoading(false);
        return;
      }
      await refreshAfterMutation();
      onSaved();
    } catch (e) {
      setLoading(false);
      if (e instanceof ApiRequestError) {
        const code = (e.body as { error?: string } | undefined)?.error;
        const msg = (e.body as { message?: string } | undefined)?.message;
        if (e.status === 401) {
          setError('Incorrect password.');
          setShakeTick((t) => t + 1);
          return;
        }
        if (e.status === 400 && code === 'advanced-not-configured') {
          setAdvancedNotConfigured(true);
          return;
        }
        setError(msg || e.message);
        return;
      }
      setError(e instanceof Error ? e.message : 'Failed to save host.');
    }
  };

  const submitLabel = loading
    ? 'Saving…'
    : needPassword
      ? 'Encrypt & save'
      : editing
        ? 'Save changes'
        : 'Add host';

  return (
    <Modal
      isOpen
      onClose={onClose}
      title={editing ? `Edit ${host!.label}` : 'Add remote host'}
      footer={
        <div className="flex items-center justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-[var(--text-secondary)]"
          >
            Cancel
          </button>
          <button
            onClick={() => void submit()}
            disabled={loading || !canSubmit}
            className="px-4 py-1.5 rounded bg-[var(--accent)]/20 text-[var(--accent)] hover:bg-[var(--accent)]/30 disabled:opacity-50 transition-colors"
          >
            {submitLabel}
          </button>
        </div>
      }
    >
      <div className="space-y-4">
        <label className="block space-y-1">
          <span className="text-sm font-medium text-[var(--text-primary)]">Label</span>
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g. Production DSS"
            className="w-full input-glass text-sm"
            autoFocus
          />
        </label>
        <label className="block space-y-1">
          <span className="text-sm font-medium text-[var(--text-primary)]">URL</span>
          <input
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://dss.example.com:11200"
            className="w-full input-glass text-sm font-mono"
          />
        </label>
        <label className="block space-y-1">
          <span className="text-sm font-medium text-[var(--text-primary)]">Admin API key</span>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={editing ? 'Leave blank to keep current key' : 'dku-…'}
            autoComplete="off"
            className="w-full input-glass text-sm font-mono"
          />
          <span className="text-xs text-[var(--text-muted)]">
            Sent over HTTPS and encrypted server-side before it is stored. An{' '}
            <code className="text-[var(--accent)]">adkfk1$…</code> blob is also accepted.
          </span>
        </label>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={verifyTls}
              onChange={(e) => setVerifyTls(e.target.checked)}
              className="h-4 w-4 accent-[var(--accent)]"
            />
            <span className="text-sm text-[var(--text-primary)]">Verify TLS certificate</span>
          </label>
          <label className="block space-y-1">
            <span className="text-sm font-medium text-[var(--text-primary)]">
              Backup project key <span className="text-[var(--text-muted)]">(optional)</span>
            </span>
            <input
              type="text"
              value={backupProjectKey}
              onChange={(e) => setBackupProjectKey(e.target.value)}
              placeholder="auto-detect"
              className="w-full input-glass text-sm font-mono"
            />
          </label>
        </div>

        {needPassword && (
          <div
            key={shakeTick}
            className={`space-y-1 ${shakeTick > 0 ? 'fx-shake' : ''}`}
          >
            <span className="text-sm font-medium text-[var(--text-primary)]">Master password</span>
            <p className="text-xs text-[var(--text-muted)]">
              Enter your Advanced Actions password to encrypt this key.
            </p>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  void submit();
                }
              }}
              placeholder="Password"
              autoComplete="current-password"
              className="w-full input-glass text-sm"
              autoFocus
            />
          </div>
        )}

        {advancedNotConfigured && (
          <div className="flex items-start gap-2 rounded border border-[var(--status-warning-border)] bg-[var(--status-warning-bg)] px-3 py-2 text-sm">
            <svg
              className="w-4 h-4 mt-0.5 shrink-0 text-[var(--neon-amber)]"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
            <p className="text-[var(--text-secondary)]">
              No Advanced Actions password is set yet. Generate one with the{' '}
              <a
                href={SECRET_PAGE_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[var(--accent)] hover:underline"
              >
                secret generator
              </a>
              , paste it into{' '}
              <a
                href={PLUGIN_SETTINGS_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[var(--accent)] hover:underline"
              >
                plugin settings → “Advanced Actions secret”
              </a>
              , then add the host.
            </p>
          </div>
        )}

        {error && <div className="text-sm text-[var(--neon-red)]">{error}</div>}
      </div>
    </Modal>
  );
}
