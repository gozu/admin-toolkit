import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import { fetchJson, fetchRaw } from '../utils/api';
import { parseSseStream } from '../utils/sseStream';
import { loadFromStorage } from '../utils/storage';
import { SearchableCombobox } from './SearchableCombobox';
import type { LlmOption, LogError } from '../types';

export const DEFAULT_AI_SYSTEM_PROMPT = `You are an expert Dataiku DSS administrator and backend engineer analyzing error logs from a DSS instance's backend.log file.

IMPORTANT RULES:
- Only analyze lines with log4j level WARN, ERROR, FATAL, or SEVERE. Completely ignore INFO, DEBUG, and TRACE lines.
- For severity, use the EXACT log4j level from the log line (e.g. [WARN], [ERROR], [FATAL]). Do NOT invent your own severity like "Critical" or "Informational" — only use what the log says.
- Do NOT mention things that are working correctly or look normal. Do NOT speculate about potential issues not evidenced in the log data.
- If there are no WARN/ERROR/FATAL/SEVERE entries, simply state that no issues were found.

Before answering, think step-by-step through each error carefully. For each error pattern:
- Reason through what component, subsystem, or configuration could cause it.
- Search the web for the specific error message, Java exception, or stack trace to find known issues, Dataiku Knowledge Base articles, community posts, or release notes.
- Cross-reference with official Dataiku documentation (doc.dataiku.com) for configuration guidance, known limitations, and recommended fixes.
- Only after researching, provide your diagnosis and remediation.

Your task:
1. Identify the root cause of each distinct error or warning pattern.
2. Tag each issue with its log4j level exactly as it appears in the log (e.g. ERROR, WARN, FATAL).
3. Provide specific, actionable remediation steps, including links to relevant documentation or KB articles when available.
4. Group related errors sharing a root cause.
5. Highlight data loss risk, security issues, or service outage indicators.
6. Do not write anything about INFO level logs, only WARN and above.

Format: markdown with headings per issue (include the log4j level in the heading, e.g. "## [ERROR] FileNotFoundException in saved model lookup"), bullet points for remediation. Start with a 2-3 sentence Executive Summary.`;

export const AI_PROMPT_STORAGE_KEY = 'aiLogAnalysisPrompt';

interface AiLogAnalysisProps {
  rawLogErrors?: LogError[];
}

interface AnalysisState {
  phase: string;
  text: string;
  llmId: string;
  logCharsAnalyzed: number;
  done: boolean;
}

type LogMode = 'curated' | 'raw';

function buildCuratedLogData(rawLogErrors: LogError[]): string {
  return rawLogErrors.map((block) => block.data.join('\n')).join('\n---\n');
}

export function AiLogAnalysis({ rawLogErrors }: AiLogAnalysisProps) {
  const [llms, setLlms] = useState<LlmOption[]>([]);
  const [selectedLlmLabel, setSelectedLlmLabel] = useState('');
  const [isLoadingLlms, setIsLoadingLlms] = useState(true);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysis, setAnalysis] = useState<AnalysisState | null>(null);
  const [error, setError] = useState('');
  const [showDisclaimer, setShowDisclaimer] = useState(false);
  const [unlocked, setUnlocked] = useState(false);
  const konamiRef = useRef('');
  const abortRef = useRef<AbortController | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const timedOutRef = useRef(false);
  const [llmTimeout, setLlmTimeout] = useState(120000);

  // Log mode: curated errors vs raw full log
  const [logMode, setLogMode] = useState<LogMode>('raw');
  const [rawLogTail, setRawLogTail] = useState<string | null>(null);
  const [isLoadingRawLog, setIsLoadingRawLog] = useState(true);

  // System prompt
  const initialSystemPrompt = useMemo(() => {
    const stored = loadFromStorage<string>(AI_PROMPT_STORAGE_KEY, '');
    return stored.trim() || DEFAULT_AI_SYSTEM_PROMPT;
  }, []);
  const [editableSystemPrompt] = useState(initialSystemPrompt);

  // Curated log data
  const curatedLogData = useMemo(
    () => (rawLogErrors?.length ? buildCuratedLogData(rawLogErrors) : ''),
    [rawLogErrors],
  );

  // Editable content — single textarea combining system prompt + log data
  const buildFullMessage = useCallback((systemPrompt: string, logData: string) => {
    return `${systemPrompt}\n\n---\n\n${logData}`;
  }, []);

  const [editableContent, setEditableContent] = useState(() =>
    buildFullMessage(initialSystemPrompt, 'Loading raw log...'),
  );

  // Fetch raw log eagerly on mount — populate textarea since raw is default mode
  useEffect(() => {
    fetchJson<{ text: string; chars: number }>('/api/logs/raw-tail')
      .then((data) => {
        setRawLogTail(data.text);
        setEditableContent(buildFullMessage(initialSystemPrompt, data.text));
      })
      .catch((err) => setError(`Failed to load raw log: ${err}`))
      .finally(() => setIsLoadingRawLog(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // When log mode changes, rebuild the content
  const switchLogMode = useCallback(
    (mode: LogMode) => {
      setLogMode(mode);
      if (mode === 'curated') {
        setEditableContent(buildFullMessage(editableSystemPrompt, curatedLogData));
      } else if (mode === 'raw' && rawLogTail !== null) {
        setEditableContent(buildFullMessage(editableSystemPrompt, rawLogTail));
      }
    },
    [editableSystemPrompt, curatedLogData, rawLogTail, buildFullMessage],
  );

  // Update content when rawLogErrors changes (for curated mode)
  const isFirstRender = useRef(true);
  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    if (logMode === 'curated' && rawLogErrors?.length) {
      const newLogData = buildCuratedLogData(rawLogErrors);
      setEditableContent(buildFullMessage(editableSystemPrompt, newLogData));
    }
  }, [rawLogErrors, logMode, editableSystemPrompt, buildFullMessage]);

  const filteredLlms = useMemo(() => {
    if (unlocked) return llms;
    return llms.filter((l) => l.type === 'HUGGINGFACE_TRANSFORMER_LOCAL');
  }, [llms, unlocked]);

  const llmLabelToId = useMemo(() => {
    const map = new Map<string, string>();
    for (const llm of filteredLlms) map.set(llm.label, llm.id);
    return map;
  }, [filteredLlms]);

  const llmLabels = useMemo(() => filteredLlms.map((l) => l.label), [filteredLlms]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      konamiRef.current += e.key.toLowerCase();
      if (konamiRef.current.length > 20) konamiRef.current = konamiRef.current.slice(-20);
      if (konamiRef.current.includes('kaos')) {
        setUnlocked(true);
        window.removeEventListener('keydown', handler);
      }
    };
    if (!unlocked) window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [unlocked]);

  useEffect(() => {
    fetchJson<{ current: Record<string, number>; defaults: Record<string, number> }>(
      '/api/settings',
    )
      .then((data) => {
        if (data.current.fe_timeout_llm_analysis)
          setLlmTimeout(data.current.fe_timeout_llm_analysis);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetchJson<{ llms: LlmOption[]; error?: string }>('/api/llms')
      .then((data) => {
        setLlms(data.llms);
        const hfLlms = data.llms.filter((l) => l.type === 'HUGGINGFACE_TRANSFORMER_LOCAL');
        const initial = hfLlms.length > 0 ? hfLlms[0] : data.llms[0];
        if (initial) setSelectedLlmLabel(initial.label);
        if (data.error) setError(data.error);
      })
      .catch((err) => setError(String(err)))
      .finally(() => setIsLoadingLlms(false));
  }, []);

  const runAnalysis = useCallback(async () => {
    const llmId = llmLabelToId.get(selectedLlmLabel);
    if (!llmId) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setIsAnalyzing(true);
    setError('');
    setAnalysis({ phase: 'Starting', text: '', llmId: '', logCharsAnalyzed: 0, done: false });

    // Split editable content back into system prompt + user message at the --- separator
    const separatorIdx = editableContent.indexOf('\n\n---\n\n');
    let systemPrompt: string;
    let userMessage: string;
    if (separatorIdx !== -1) {
      systemPrompt = editableContent.slice(0, separatorIdx);
      userMessage = editableContent.slice(separatorIdx + 7); // length of '\n\n---\n\n'
    } else {
      // No separator found — send everything as user message
      systemPrompt = '';
      userMessage = editableContent;
    }

    // Start LLM timeout timer
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timedOutRef.current = false;
    timeoutRef.current = setTimeout(() => {
      timedOutRef.current = true;
      controller.abort();
    }, llmTimeout);

    try {
      const response = await fetchRaw('/api/logs/ai-analysis', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ llmId, systemPrompt, userMessage }),
        signal: controller.signal,
      });

      if (!response.ok || !response.body) {
        const body = await response.text();
        throw new Error(
          `Request failed: ${response.status} ${response.statusText} - ${body.slice(0, 240)}`,
        );
      }

      for await (const frame of parseSseStream(response.body)) {
        const eventType = frame.event;
        const payload = frame.payload as Record<string, unknown>;

        if (eventType === 'phase') {
          setAnalysis((prev) => (prev ? { ...prev, phase: String(payload.phase || '') } : prev));
        } else if (eventType === 'chunk') {
          setAnalysis((prev) =>
            prev
              ? {
                  ...prev,
                  text: prev.text + String(payload.text || ''),
                  phase: 'Generating analysis',
                }
              : prev,
          );
        } else if (eventType === 'done') {
          setAnalysis((prev) =>
            prev
              ? {
                  ...prev,
                  done: true,
                  phase: 'Complete',
                  llmId: String(payload.llmId || prev.llmId),
                  logCharsAnalyzed: Number(payload.logCharsAnalyzed) || prev.logCharsAnalyzed,
                  ...(payload.analysis ? { text: String(payload.analysis) } : {}),
                }
              : prev,
          );
        } else if (eventType === 'error') {
          setError(String(payload.error || 'Unknown error'));
        }
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') {
        setError(
          timedOutRef.current
            ? 'LLM response timed out. You can increase the timeout in Settings > Backend Settings > Frontend API Timeouts.'
            : 'Analysis aborted.',
        );
      } else {
        setError(String(err));
      }
    } finally {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      setIsAnalyzing(false);
    }
  }, [selectedLlmLabel, llmLabelToId, llmTimeout, editableContent]);

  const showResult = analysis && (analysis.text || analysis.done);
  const phaseLabel = analysis && !analysis.done ? analysis.phase : null;
  const hasValidSelection = llmLabelToId.has(selectedLlmLabel);

  return (
    <div className="mb-4">
      <div className="ai-analysis-toolbar">
        {isLoadingLlms ? (
          <span className="text-sm text-[var(--text-secondary)]">Loading LLMs...</span>
        ) : llms.length === 0 ? (
          <span className="text-sm text-[var(--text-secondary)]">
            No LLMs available — configure an LLM connection in this project to enable AI analysis.
          </span>
        ) : (
          <>
            <div className="w-64">
              <SearchableCombobox
                value={selectedLlmLabel}
                onChange={setSelectedLlmLabel}
                options={llmLabels}
                placeholder="Search LLMs..."
                className="w-full px-3 py-1.5 text-sm rounded-lg bg-[var(--bg-surface)] border border-[var(--border-default)]
                           text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)]
                           disabled:opacity-50"
              />
            </div>
            {isAnalyzing ? (
              <button
                onClick={() => abortRef.current?.abort()}
                className="flex items-center gap-2 px-4 py-1.5 text-sm font-medium rounded-lg
                           bg-[var(--neon-red)] text-white hover:opacity-90 transition-opacity"
              >
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                  <rect x="6" y="6" width="12" height="12" rx="1" />
                </svg>
                Abort
              </button>
            ) : (
              <button
                onClick={() => setShowDisclaimer(true)}
                disabled={!hasValidSelection}
                className="flex items-center gap-2 px-4 py-1.5 text-sm font-medium rounded-lg
                           bg-[var(--accent)] text-white hover:opacity-90 transition-opacity
                           disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
                  />
                </svg>
                AI Log Analysis
              </button>
            )}
            {phaseLabel && !showResult && (
              <span className="text-sm text-[var(--text-secondary)] italic">{phaseLabel}...</span>
            )}
          </>
        )}
      </div>

      {rawLogErrors && rawLogErrors.length > 0 && (
        <div className="mt-3 p-3 rounded-lg bg-[var(--bg-surface)] border border-[var(--border-default)] space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <label className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wide">
                LLM prompt preview
              </label>
              <div className="flex rounded-md overflow-hidden border border-[var(--border-default)]">
                <button
                  onClick={() => switchLogMode('curated')}
                  className={`px-2.5 py-0.5 text-xs font-medium transition-colors ${
                    logMode === 'curated'
                      ? 'bg-[var(--accent)] text-white'
                      : 'bg-[var(--bg-primary)] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]'
                  }`}
                >
                  Curated errors
                </button>
                <button
                  onClick={() => switchLogMode('raw')}
                  disabled={isLoadingRawLog}
                  className={`px-2.5 py-0.5 text-xs font-medium transition-colors border-l border-[var(--border-default)] ${
                    logMode === 'raw'
                      ? 'bg-[var(--accent)] text-white'
                      : 'bg-[var(--bg-primary)] text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]'
                  } disabled:opacity-50`}
                >
                  {isLoadingRawLog ? 'Loading...' : 'Raw log (last 100K)'}
                </button>
              </div>
            </div>
            <span className="text-xs text-[var(--text-tertiary)]">
              {editableContent.length.toLocaleString()} chars
            </span>
          </div>
          <textarea
            value={editableContent}
            onChange={(e) => setEditableContent(e.target.value)}
            rows={16}
            className="w-full p-2 text-xs font-mono rounded-lg bg-[var(--bg-primary)] border border-[var(--border-default)]
                       text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)] resize-y"
          />
        </div>
      )}

      {error && (
        <div className="mt-3 p-3 rounded-lg bg-[var(--status-danger-bg)] border border-[var(--status-danger-border)] text-sm text-[var(--danger)]">
          {error}
        </div>
      )}

      {showResult && (
        <div className="ai-analysis-result mt-3">
          <div className="flex items-center justify-between mb-3 pb-2 border-b border-[var(--border-default)]">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-[var(--accent)]">AI Analysis</span>
              {!analysis.done && (
                <svg
                  className="w-3.5 h-3.5 animate-spin text-[var(--accent)]"
                  viewBox="0 0 24 24"
                  fill="none"
                >
                  <circle
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="3"
                    strokeDasharray="32"
                    strokeLinecap="round"
                  />
                </svg>
              )}
            </div>
            {analysis.done && analysis.llmId && (
              <span className="text-xs text-[var(--text-tertiary)]">
                {analysis.llmId} &middot; {(analysis.logCharsAnalyzed / 1000).toFixed(1)}K chars
                analyzed
              </span>
            )}
          </div>
          <div className="ai-analysis-markdown">
            <ReactMarkdown>{analysis.text}</ReactMarkdown>
          </div>
        </div>
      )}
      {showDisclaimer && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          onClick={() => setShowDisclaimer(false)}
        >
          <div
            className="mx-4 max-w-md rounded-xl bg-[var(--bg-elevated)] border border-[var(--border-default)] shadow-2xl p-6 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3">
              <div className="flex-shrink-0 w-10 h-10 rounded-full bg-[var(--status-warning-bg)] border border-[var(--status-warning-border)] flex items-center justify-center">
                <svg
                  className="w-5 h-5 text-[var(--neon-amber)]"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z"
                  />
                </svg>
              </div>
              <h3 className="text-lg font-semibold text-[var(--text-primary)]">AI Disclaimer</h3>
            </div>
            <p className="text-sm text-[var(--text-secondary)] leading-relaxed">
              AI-generated analysis may be{' '}
              <strong className="text-[var(--text-primary)]">
                inaccurate, incomplete, or misleading
              </strong>
              . LLMs can hallucinate error causes and suggest incorrect remediation steps.
            </p>
            <p className="text-sm text-[var(--text-secondary)] leading-relaxed">
              Always verify findings against official Dataiku documentation and your own system
              knowledge before taking any action.
            </p>
            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                onClick={() => setShowDisclaimer(false)}
                className="px-4 py-2 text-sm rounded-lg text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  setShowDisclaimer(false);
                  runAnalysis();
                }}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-[var(--accent)] text-white hover:opacity-90 transition-opacity"
              >
                I understand, proceed
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
