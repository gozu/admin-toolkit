import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Modal } from './Modal';
import { Spinner } from './common/Spinner';
import { formatInterpreter } from '../utils/formatters';
import type { AdviceEntry } from '../state/codeEnvAdviceStore';
import type { BrokenEnvRow } from '../types';

interface CodeEnvAdviceModalProps {
  row: BrokenEnvRow;
  entry: AdviceEntry | undefined;
  onClose: () => void;
  onRetry: () => void;
}

// Keep model output passive: links, images, and raw HTML must never create
// browser-initiated requests from untrusted remediation text.
const SAFE_ADVICE_MARKDOWN_ELEMENTS = [
  'p',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'ul',
  'ol',
  'li',
  'strong',
  'em',
  'del',
  'blockquote',
  'code',
  'pre',
  'hr',
  'br',
  'table',
  'thead',
  'tbody',
  'tr',
  'th',
  'td',
] as const;

export function CodeEnvAdviceModal({ row, entry, onClose, onRetry }: CodeEnvAdviceModalProps) {
  const waiting = !entry || (entry.status === 'streaming' && !entry.text);

  return (
    <Modal isOpen onClose={onClose} title={`Remediation — ${row.name}`} sizePreset="full">
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="badge badge-critical">{row.failureLabel || 'Build failed'}</span>
          <span className="font-mono text-[var(--text-muted)]">
            {row.lang.toLowerCase()}
            {row.pythonVersion ? ` · ${formatInterpreter(row.pythonVersion)}` : ''}
          </span>
          {entry?.llmLabel && (
            <span className="ml-auto text-[var(--text-tertiary)]">via {entry.llmLabel}</span>
          )}
        </div>

        <details className="rounded-lg border border-[var(--border-glass)] bg-[var(--bg-surface)]">
          <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-[var(--text-secondary)]">
            What the log said{row.logName ? ` (${row.logName})` : ''}
          </summary>
          <pre className="max-h-72 overflow-auto border-t border-[var(--border-glass)] px-3 py-2 font-mono text-xs whitespace-pre-wrap text-[var(--text-secondary)]">
            {row.errorExcerpt || '(no detail)'}
          </pre>
        </details>

        {entry?.status === 'error' && (
          <div className="flex items-center gap-3 rounded-lg border border-[var(--neon-red)]/30 bg-[var(--neon-red)]/10 px-4 py-3 text-sm text-[var(--neon-red)]">
            <span className="flex-1">{entry.error}</span>
            <button
              type="button"
              onClick={onRetry}
              className="rounded px-2 py-1 text-xs font-medium text-[var(--accent)] hover:underline"
            >
              Retry
            </button>
          </div>
        )}

        {entry?.status === 'stopped' && (
          <div className="flex items-center gap-3 rounded-lg border border-[var(--neon-yellow)]/30 bg-[var(--neon-yellow)]/10 px-4 py-3 text-sm text-[var(--neon-yellow)]">
            <span className="flex-1">Stopped mid-answer — this advice is incomplete.</span>
            <button
              type="button"
              onClick={onRetry}
              className="rounded px-2 py-1 text-xs font-medium text-[var(--accent)] hover:underline"
            >
              Ask again
            </button>
          </div>
        )}

        {waiting ? (
          <div className="flex items-center gap-3 px-1 py-6 text-sm text-[var(--text-secondary)]">
            <Spinner size="h-4 w-4" color="border-[var(--neon-cyan)]" />
            Consulting {entry?.llmLabel || 'the LLM'}…
          </div>
        ) : (
          <div className="ai-analysis-markdown">
            <ReactMarkdown
              allowedElements={SAFE_ADVICE_MARKDOWN_ELEMENTS}
              remarkPlugins={[remarkGfm]}
              skipHtml
              unwrapDisallowed
            >
              {entry.text}
            </ReactMarkdown>
          </div>
        )}
      </div>
    </Modal>
  );
}
