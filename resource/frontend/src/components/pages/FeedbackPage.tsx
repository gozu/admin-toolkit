import { useCallback, useMemo, useRef, useState } from 'react';
import { fetchRaw } from '../../utils/api';
import { getActiveHost } from '../../state/hostStore';
import { feedbackFromPageStore } from '../../state/feedbackFromPage';
import { getModuleLabel } from '../../utils/moduleRegistry';
import { formatBytes } from '../../utils/formatters';
import { Spinner } from '../common/Spinner';

type FeedbackType = 'bug' | 'idea' | 'other';
type SubmitStatus = 'idle' | 'submitting' | 'success' | 'error';

const TYPE_LABELS: Record<FeedbackType, string> = {
  bug: 'Bug',
  idea: 'Idea',
  other: 'Other',
};

const MAX_FILES = 5;
const MAX_FILE_MB = 8;
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
]);
const ACCEPT_ATTR = 'image/*,.pdf,.txt,.log';

function extOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot >= 0 ? name.slice(dot).toLowerCase() : '';
}

export function FeedbackPage() {
  const [type, setType] = useState<FeedbackType>('bug');
  const [message, setMessage] = useState('');
  const [email, setEmail] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [website, setWebsite] = useState(''); // honeypot — must stay empty
  const [status, setStatus] = useState<SubmitStatus>('idle');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // The page the user came from is captured once, at mount, from the stash the
  // header button set just before navigating here.
  const [fromPageId] = useState(() => feedbackFromPageStore.get());

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
        fd.append('diagnostics', diagnosticsText.text);
        fd.append('website', website);
        files.forEach((f) => fd.append('attachments', f, f.name));

        const res = await fetchRaw('/api/feedback', { method: 'POST', body: fd });
        if (res.ok) {
          setStatus('success');
          setMessage('');
          setEmail('');
          setFiles([]);
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
    [status, message, type, email, website, files, diagnosticsText.text],
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

          {/* Attachments */}
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-tertiary)] mb-2">
              Attachments{' '}
              <span className="font-normal normal-case text-[var(--text-tertiary)]">
                (optional — images, pdf, txt, log · up to {MAX_FILES} · {MAX_FILE_MB} MB each)
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
