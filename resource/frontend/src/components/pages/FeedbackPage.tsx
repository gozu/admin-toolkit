import { useCallback, useMemo, useRef, useState } from 'react';
import { fetchRaw } from '../../utils/api';
import { getActiveHost } from '../../state/hostStore';
import { feedbackFromPageStore } from '../../state/feedbackFromPage';
import { getModuleLabel } from '../../utils/moduleRegistry';
import { formatBytes } from '../../utils/formatters';
import { useDiag } from '../../context/DiagContext';
import { buildDiagBundle, type BundleManifest } from '../../utils/diagBundle';
import { loadFromStorage, saveToStorage } from '../../utils/storage';
import { SELECTED_MAIL_CHANNEL_STORAGE_KEY } from './SettingsPage';
import { Spinner } from '../common/Spinner';

type FeedbackType = 'bug' | 'idea' | 'other';
type SubmitStatus = 'idle' | 'submitting' | 'success' | 'error';

const TYPE_LABELS: Record<FeedbackType, string> = {
  bug: 'Bug',
  idea: 'Idea',
  other: 'Other',
};

const MAX_FILES = 5;
// Mirror of the backend feedback caps (python-lib/adk_backend/routes/feedback.py):
// 25 MB per file and .zip allowed (for the auto-generated diagnostic bundle).
const MAX_FILE_MB = 25;
const MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024;
const ALLOWED_EXT = new Set([
  '.png',
  '.jpg',
  '.jpeg',
  '.gif',
  '.webp',
  '.bmp',
  '.svg',
  '.pdf',
  '.txt',
  '.log',
  '.zip',
]);
const ACCEPT_ATTR = 'image/*,.pdf,.txt,.log,.zip';

function extOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot >= 0 ? name.slice(dot).toLowerCase() : '';
}

export function FeedbackPage() {
  const { state: diagState } = useDiag();
  const [type, setType] = useState<FeedbackType>('bug');
  const [message, setMessage] = useState('');
  const [email, setEmail] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [website, setWebsite] = useState(''); // honeypot — must stay empty
  const [status, setStatus] = useState<SubmitStatus>('idle');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // The generated diagnostic bundle lives in its own slot (separate from the
  // user's `files`) so it can be pinned in the UI and appended to the submit.
  // `bundleFile` is the generated file (always set on success, for download);
  // `bundle` is the attach slot (null when the file is too large to email).
  const [bundleFile, setBundleFile] = useState<File | null>(null);
  const [bundle, setBundle] = useState<File | null>(null);
  const [bundleManifest, setBundleManifest] = useState<BundleManifest | null>(null);
  const [bundleStatus, setBundleStatus] = useState<'idle' | 'generating' | 'error'>('idle');
  const [bundleNote, setBundleNote] = useState<string | null>(null);

  // The page the user came from is captured once, at mount, from the stash the
  // header button set just before navigating here.
  const [fromPageId] = useState(() => feedbackFromPageStore.get());

  // Mail-channel picker — which DSS channel sends this feedback. Shares the
  // localStorage key with Settings so a choice carries across both. The feedback
  // route honours the explicit choice (else configured, else first channel).
  const mailChannels = diagState.parsedData.mailChannels ?? [];
  const configuredChannel = diagState.parsedData.configuredMailChannel || '';
  const [storedChannel, setStoredChannel] = useState<string>(() =>
    loadFromStorage<string>(SELECTED_MAIL_CHANNEL_STORAGE_KEY, ''),
  );
  const isStoredValid = !!storedChannel && mailChannels.some((c) => c.id === storedChannel);
  const selectedChannel = isStoredValid
    ? storedChannel
    : mailChannels.some((c) => c.id === configuredChannel)
      ? configuredChannel
      : mailChannels[0]?.id ?? '';
  const handleChannelChange = useCallback((id: string) => {
    setStoredChannel(id);
    saveToStorage(SELECTED_MAIL_CHANNEL_STORAGE_KEY, id);
  }, []);

  const diagnosticsText = useMemo(() => {
    const host = getActiveHost();
    const rows: Array<[string, string]> = [
      ['Version', __APP_VERSION__],
      ['Host', `${host.label} (${host.id})`],
      ['From page', fromPageId ? getModuleLabel(fromPageId) : 'direct'],
      ['User agent', typeof navigator !== 'undefined' ? navigator.userAgent : ''],
      [
        'Viewport',
        typeof window !== 'undefined' ? `${window.innerWidth}x${window.innerHeight}` : '',
      ],
      ['Time', new Date().toISOString()],
    ];
    return { rows, text: rows.map(([k, v]) => `${k}: ${v}`).join('\n') };
  }, [fromPageId]);

  const addFiles = useCallback(
    (incoming: File[]) => {
      const errs: string[] = [];
      const next = [...files];
      for (const f of incoming) {
        if (next.length >= MAX_FILES) {
          errs.push(`You can attach at most ${MAX_FILES} files.`);
          break;
        }
        if (!ALLOWED_EXT.has(extOf(f.name))) {
          errs.push(`${f.name}: unsupported file type.`);
          continue;
        }
        if (f.size > MAX_FILE_BYTES) {
          errs.push(`${f.name}: larger than ${MAX_FILE_MB} MB.`);
          continue;
        }
        if (next.some((x) => x.name === f.name && x.size === f.size)) continue;
        next.push(f);
      }
      setFiles(next);
      setErrorMsg(errs.length ? errs.join(' ') : null);
    },
    [files],
  );

  const removeFile = useCallback((idx: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== idx));
  }, []);

  const removeBundle = useCallback(() => {
    setBundleFile(null);
    setBundle(null);
    setBundleManifest(null);
    setBundleNote(null);
    setBundleStatus('idle');
  }, []);

  const downloadBundle = useCallback(() => {
    if (!bundleFile) return;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(bundleFile);
    a.download = bundleFile.name;
    a.click();
    URL.revokeObjectURL(a.href);
  }, [bundleFile]);

  const generateBundle = useCallback(async () => {
    if (bundleStatus === 'generating') return;
    setBundleStatus('generating');
    setBundleNote(null);
    try {
      const { blob, filename, manifest } = await buildDiagBundle({
        report: {
          type,
          message: message.trim(),
          email: email.trim(),
          diagnosticsText: diagnosticsText.text,
        },
        state: {
          parsedData: diagState.parsedData,
          debugLogs: diagState.debugLogs,
          mode: diagState.mode,
          activePage: diagState.activePage,
          layoutMode: diagState.layoutMode,
          activeFilter: diagState.activeFilter,
          focusedConnectionFilter: diagState.focusedConnectionFilter,
          focusedUserFilter: diagState.focusedUserFilter,
          comparison: diagState.comparison,
        },
      });

      // No auto-download — the bundle is primarily an email attachment. The user
      // can download it on demand with the Download button below.
      setBundleManifest(manifest);
      const file = new File([blob], filename, { type: 'application/zip' });
      setBundleFile(file);
      if (file.size > MAX_FILE_BYTES) {
        // Too large to email: skip auto-attach; download stays available.
        setBundle(null);
        setBundleNote(
          `Bundle too large to email (${formatBytes(file.size)}) — not attached. ` +
            'Use Download to save it and send it to the toolkit author directly.',
        );
      } else {
        setBundle(file);
      }
      setBundleStatus('idle');
    } catch (err) {
      setBundleStatus('error');
      setBundleNote(
        `Could not generate the diagnostic bundle: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    }
  }, [
    bundleStatus,
    type,
    message,
    email,
    diagnosticsText.text,
    diagState,
  ]);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      addFiles(Array.from(e.dataTransfer.files));
    },
    [addFiles],
  );

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (status === 'submitting') return;
      const trimmed = message.trim();
      if (!trimmed) {
        setStatus('idle');
        setErrorMsg('Please enter a message.');
        return;
      }

      setStatus('submitting');
      setErrorMsg(null);
      try {
        const fd = new FormData();
        fd.append('type', type);
        fd.append('message', trimmed);
        if (email.trim()) fd.append('email', email.trim());
        if (selectedChannel) fd.append('mailChannel', selectedChannel);
        fd.append('diagnostics', diagnosticsText.text);
        fd.append('website', website);
        files.forEach((f) => fd.append('attachments', f, f.name));
        if (bundle) fd.append('attachments', bundle, bundle.name);

        const res = await fetchRaw('/api/feedback', { method: 'POST', body: fd });
        if (res.ok) {
          setStatus('success');
          setMessage('');
          setEmail('');
          setFiles([]);
          removeBundle();
          return;
        }

        let msg =
          res.status === 429
            ? 'You are sending feedback too quickly — please wait a moment and try again.'
            : 'Something went wrong sending your feedback. Please try again.';
        try {
          const body = await res.json();
          if (body && typeof body === 'object' && (body.message || body.error)) {
            msg = String(body.message || body.error);
          }
        } catch {
          /* non-JSON body */
        }
        setStatus('error');
        setErrorMsg(msg);
      } catch {
        setStatus('error');
        setErrorMsg('Could not reach the server. Please check your connection and try again.');
      }
    },
    [
      status,
      message,
      type,
      email,
      website,
      files,
      bundle,
      removeBundle,
      selectedChannel,
      diagnosticsText.text,
    ],
  );

  // Typing again after a send clears the stale success/error banner.
  const handleMessageChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      setMessage(e.target.value);
      if (status === 'success' || status === 'error') {
        setStatus('idle');
        setErrorMsg(null);
      }
    },
    [status],
  );

  return (
    <div className="w-full py-4">
      <div className="mb-6 px-6">
        <h1 className="text-2xl font-bold text-[var(--text-primary)] mb-2">Send Feedback</h1>
        <p className="text-sm text-[var(--text-secondary)] max-w-2xl">
          Found a bug, have an idea, or anything else? This goes straight to the toolkit author.
          While the Admin Toolkit is in Early Access Preview, your feedback is the #1 way it gets
          better.
        </p>
      </div>

      <div className="px-6 max-w-2xl">
        <form onSubmit={handleSubmit} className="space-y-5">
          {/* Type chips */}
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-tertiary)] mb-2">
              Type
            </label>
            <div className="flex gap-2">
              {(['bug', 'idea', 'other'] as const).map((t) => {
                const active = type === t;
                return (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setType(t)}
                    aria-pressed={active}
                    className={
                      active
                        ? 'btn-primary px-4 py-1.5 rounded-lg text-sm'
                        : 'px-4 py-1.5 rounded-lg text-sm border border-[var(--neon-cyan)]/40 text-[var(--neon-cyan)] hover:bg-[var(--neon-cyan)]/10 transition-colors'
                    }
                  >
                    {TYPE_LABELS[t]}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Message */}
          <div>
            <label
              htmlFor="feedback-message"
              className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-tertiary)] mb-2"
            >
              Message
            </label>
            <textarea
              id="feedback-message"
              value={message}
              onChange={handleMessageChange}
              rows={6}
              placeholder="What happened, what you expected, or your idea…"
              className="w-full rounded-md border border-[var(--border-default)] bg-[var(--bg-elevated)] text-[var(--text-primary)] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[var(--accent)] resize-y"
            />
          </div>

          {/* Optional reply email */}
          <div>
            <label
              htmlFor="feedback-email"
              className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-tertiary)] mb-2"
            >
              Your email{' '}
              <span className="font-normal normal-case text-[var(--text-tertiary)]">
                (optional — for a reply)
              </span>
            </label>
            <input
              id="feedback-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              autoComplete="email"
              className="w-full rounded-md border border-[var(--border-default)] bg-[var(--bg-elevated)] text-[var(--text-primary)] px-3 py-2 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-[var(--accent)]"
            />
          </div>

          {/* Mail channel — which DSS channel sends this feedback */}
          {mailChannels.length > 0 && (
            <div>
              <label
                htmlFor="feedback-mail-channel"
                className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-tertiary)] mb-2"
              >
                Send via{' '}
                <span className="font-normal normal-case text-[var(--text-tertiary)]">
                  (DSS mail channel — shared with Settings)
                </span>
              </label>
              <select
                id="feedback-mail-channel"
                value={selectedChannel}
                onChange={(e) => handleChannelChange(e.target.value)}
                className="w-full rounded-md border border-[var(--border-default)] bg-[var(--bg-elevated)] text-[var(--text-primary)] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[var(--accent)]"
              >
                {mailChannels.map((channel) => (
                  <option key={channel.id} value={channel.id}>
                    {channel.label}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Diagnostic bundle */}
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-tertiary)] mb-2">
              Diagnostic bundle{' '}
              <span className="font-normal normal-case text-[var(--text-tertiary)]">
                (recommended for bugs — one .zip of everything support needs)
              </span>
            </label>
            <p className="text-xs text-[var(--text-secondary)] mb-2 max-w-prose">
              Captures the toolkit's in-memory state plus a snapshot of cheap, read-only host
              reads. It's attached to this report; you can also download a copy on demand. No
              scans are triggered.
            </p>
            <button
              type="button"
              onClick={generateBundle}
              disabled={bundleStatus === 'generating'}
              className={
                bundleStatus === 'generating'
                  ? 'flex items-center gap-2 px-4 py-2 rounded-lg text-sm border border-[var(--border-glass)] bg-[var(--bg-glass)] text-[var(--text-tertiary)] cursor-not-allowed'
                  : 'flex items-center gap-2 px-4 py-2 rounded-lg text-sm border border-[var(--neon-cyan)]/40 text-[var(--neon-cyan)] hover:bg-[var(--neon-cyan)]/10 transition-colors'
              }
            >
              {bundleStatus === 'generating' ? (
                <>
                  <Spinner />
                  Generating…
                </>
              ) : bundleFile ? (
                'Regenerate diagnostic bundle'
              ) : (
                'Generate diagnostic bundle'
              )}
            </button>

            {bundleFile && (
              <ul className="mt-3 space-y-2">
                <li className="flex items-center justify-between gap-3 rounded-lg border border-[var(--neon-cyan)]/30 bg-[var(--neon-cyan)]/5 px-3 py-2">
                  <span
                    className="min-w-0 flex-1 truncate text-sm text-[var(--text-primary)]"
                    title={bundleFile.name}
                  >
                    {bundleFile.name}
                  </span>
                  <span className="flex-shrink-0 rounded bg-[var(--neon-cyan)]/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-[var(--neon-cyan)]">
                    {bundle ? 'will attach' : 'download only'}
                  </span>
                  <span className="flex-shrink-0 text-xs text-[var(--text-muted)]">
                    {formatBytes(bundleFile.size)}
                  </span>
                  <button
                    type="button"
                    onClick={downloadBundle}
                    className="flex-shrink-0 px-2 py-1 rounded text-xs border border-[var(--neon-cyan)]/40 text-[var(--neon-cyan)] hover:bg-[var(--neon-cyan)]/10 transition-colors"
                  >
                    Download
                  </button>
                  <button
                    type="button"
                    onClick={removeBundle}
                    className="flex-shrink-0 p-1 rounded text-[var(--text-muted)] hover:text-red-400 hover:bg-red-400/10 transition-colors"
                    aria-label="Remove diagnostic bundle"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M6 18L18 6M6 6l12 12"
                      />
                    </svg>
                  </button>
                </li>
              </ul>
            )}

            {bundleNote && (
              <p
                className={`mt-2 text-xs ${
                  bundleStatus === 'error'
                    ? 'text-[var(--neon-red)]'
                    : 'text-[var(--text-secondary)]'
                }`}
              >
                {bundleNote}
              </p>
            )}

            {bundleManifest && (
              <details className="mt-3 rounded-lg border border-[var(--border-glass)] bg-[var(--bg-surface)] px-3 py-2">
                <summary className="text-xs font-medium text-[var(--text-tertiary)] cursor-pointer select-none">
                  Bundle contents ({bundleManifest.files.length} files)
                </summary>
                <ul className="mt-2 space-y-0.5 text-xs font-mono text-[var(--text-secondary)] max-h-48 overflow-auto">
                  {bundleManifest.files.map((f) => (
                    <li key={f} className="break-all">
                      {f}
                    </li>
                  ))}
                </ul>
                <dl className="mt-2 grid grid-cols-[1fr_max-content] gap-x-3 gap-y-1 text-xs font-mono text-[var(--text-secondary)]">
                  {Object.entries(bundleManifest.backendFetches).map(([path, r]) => (
                    <div key={path} className="contents">
                      <dt className="text-[var(--text-tertiary)] break-all">{path}</dt>
                      <dd className={r.ok ? 'text-[var(--text-secondary)]' : 'text-[var(--neon-red)]'}>
                        {r.ok ? `ok ${r.status ?? ''}`.trim() : r.error || `failed ${r.status ?? ''}`.trim()}
                      </dd>
                    </div>
                  ))}
                </dl>
              </details>
            )}
          </div>

          {/* Attachments */}
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-tertiary)] mb-2">
              Attachments{' '}
              <span className="font-normal normal-case text-[var(--text-tertiary)]">
                (optional — images, pdf, txt, log, zip · up to {MAX_FILES} · {MAX_FILE_MB} MB each)
              </span>
            </label>
            <div
              onDrop={handleDrop}
              onDragOver={(e) => {
                e.preventDefault();
                setIsDragging(true);
              }}
              onDragLeave={(e) => {
                e.preventDefault();
                setIsDragging(false);
              }}
              onClick={() => fileInputRef.current?.click()}
              className={`rounded-lg border-2 border-dashed px-4 py-6 text-center cursor-pointer transition-colors ${
                isDragging
                  ? 'border-[var(--neon-cyan)] bg-[var(--neon-cyan)]/10'
                  : 'border-[var(--border-glass)] bg-[var(--bg-surface)] hover:border-[var(--neon-cyan)]/50'
              }`}
            >
              <p className="text-sm text-[var(--text-secondary)]">
                {isDragging ? 'Drop files here' : 'Drop files here, or click to select'}
              </p>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept={ACCEPT_ATTR}
                className="hidden"
                onChange={(e) => {
                  addFiles(Array.from(e.target.files || []));
                  e.target.value = '';
                }}
              />
            </div>

            {files.length > 0 && (
              <ul className="mt-3 space-y-2">
                {files.map((f, i) => (
                  <li
                    key={`${f.name}-${f.size}-${i}`}
                    className="flex items-center justify-between gap-3 rounded-lg border border-[var(--border-glass)] bg-[var(--bg-surface)] px-3 py-2"
                  >
                    <span
                      className="min-w-0 flex-1 truncate text-sm text-[var(--text-primary)]"
                      title={f.name}
                    >
                      {f.name}
                    </span>
                    <span className="flex-shrink-0 text-xs text-[var(--text-muted)]">
                      {formatBytes(f.size)}
                    </span>
                    <button
                      type="button"
                      onClick={() => removeFile(i)}
                      className="flex-shrink-0 p-1 rounded text-[var(--text-muted)] hover:text-red-400 hover:bg-red-400/10 transition-colors"
                      aria-label={`Remove ${f.name}`}
                    >
                      <svg
                        className="w-4 h-4"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M6 18L18 6M6 6l12 12"
                        />
                      </svg>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Diagnostics preview — transparent about what travels with the message */}
          <details className="rounded-lg border border-[var(--border-glass)] bg-[var(--bg-surface)] px-3 py-2">
            <summary className="text-xs font-medium text-[var(--text-tertiary)] cursor-pointer select-none">
              Included diagnostics (sent with your message)
            </summary>
            <dl className="mt-2 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-xs font-mono text-[var(--text-secondary)]">
              {diagnosticsText.rows.map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-[var(--text-tertiary)]">{k}</dt>
                  <dd className="break-all">{v}</dd>
                </div>
              ))}
            </dl>
          </details>

          {/* Honeypot — hidden from real users; bots fill it and get silently dropped */}
          <input
            type="text"
            name="website"
            value={website}
            onChange={(e) => setWebsite(e.target.value)}
            tabIndex={-1}
            autoComplete="off"
            aria-hidden="true"
            className="absolute left-[-9999px] top-[-9999px] h-px w-px opacity-0"
          />

          {/* Submit + status */}
          <div className="flex items-center gap-4 pt-1">
            <button
              type="submit"
              disabled={status === 'submitting' || !message.trim()}
              className={`flex items-center justify-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                status === 'submitting' || !message.trim()
                  ? 'bg-[var(--bg-glass)] border border-[var(--border-glass)] text-[var(--text-tertiary)] cursor-not-allowed'
                  : 'btn-primary'
              }`}
            >
              {status === 'submitting' ? (
                <>
                  <Spinner />
                  Sending…
                </>
              ) : (
                'Send Feedback'
              )}
            </button>

            {status === 'success' && (
              <p className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                <span className="inline-block w-2 h-2 rounded-full bg-white" />
                Feedback sent — thank you!
              </p>
            )}
            {errorMsg && status !== 'success' && (
              <p className="flex items-center gap-2 text-sm text-[var(--neon-red)]">
                {status === 'error' && (
                  <span className="inline-block w-2 h-2 rounded-full bg-[var(--neon-red)]" />
                )}
                {errorMsg}
              </p>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}
