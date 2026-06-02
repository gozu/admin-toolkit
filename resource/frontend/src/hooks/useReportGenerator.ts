import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { fetchRaw } from '../utils/api';
import { prepareReportData, type ReportData } from '../utils/prepareReportData';
import { useDiag } from '../context/DiagContext';
import { reportLlmsStore } from '../state/reportLlmsStore';
import type { ParsedData, LlmOption, Lifecycle } from '../types';

export type ReportStatus = 'idle' | 'selecting-llm' | 'generating' | 'ready' | 'viewing';

interface UseReportGeneratorReturn {
  status: ReportStatus;
  phase: string;
  llms: LlmOption[];
  isLoadingLlms: boolean;
  selectedLlmLabel: string;
  setSelectedLlmLabel: (label: string) => void;
  generate: (parsedData: ParsedData) => void;
  reportData: ReportData | null;
  error: string;
  retry: () => void;
  isOverlayOpen: boolean;
  openOverlay: () => void;
  closeOverlay: () => void;
  openSelector: () => void;
  closeSelector: () => void;
}

const REPORT_TIMEOUT_MS = 600_000;

function stripMarkdownFences(text: string): string {
  return text.replace(/^```(?:json)?\s*\n?/, '').replace(/\n?```\s*$/, '').trim();
}

export function useReportGenerator(): UseReportGeneratorReturn {
  const { dispatch } = useDiag();
  const setLifecycle = useCallback(
    (lc: Lifecycle) => {
      dispatch({ type: 'SET_PARSED_DATA', payload: { reportLoading: lc } });
    },
    [dispatch],
  );

  const [status, setStatus] = useState<ReportStatus>('idle');
  const [phase, setPhase] = useState('');
  const [selectedLlmLabel, setSelectedLlmLabel] = useState('');
  const [reportData, setReportData] = useState<ReportData | null>(null);
  const [error, setError] = useState('');
  const [isOverlayOpen, setIsOverlayOpen] = useState(false);
  const {
    llms,
    loading: isLoadingLlms,
    error: llmsError,
  } = reportLlmsStore.use();

  const abortRef = useRef<AbortController | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const timedOutRef = useRef(false);
  const lastParsedDataRef = useRef<ParsedData | null>(null);

  // Label → ID mapping
  const llmMap = useMemo(() => new Map(llms.map(l => [l.label, l.id])), [llms]);

  // Fetch LLMs on first selector open
  const fetchLlms = useCallback(async () => {
    await reportLlmsStore.load();
  }, []);

  useEffect(() => {
    if (!selectedLlmLabel && llms.length > 0) {
      setSelectedLlmLabel(llms[0].label);
    }
  }, [llms, selectedLlmLabel]);

  useEffect(() => {
    if (llmsError) setError(llmsError);
  }, [llmsError]);

  const openSelector = useCallback(() => {
    setStatus('selecting-llm');
    setError('');
    fetchLlms();
  }, [fetchLlms]);

  const closeSelector = useCallback(() => {
    if (status === 'selecting-llm') {
      setStatus(reportData ? 'ready' : 'idle');
    }
  }, [status, reportData]);

  const generate = useCallback((parsedData: ParsedData) => {
    const llmId = llmMap.get(selectedLlmLabel);
    if (!llmId) return;

    lastParsedDataRef.current = parsedData;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setStatus('generating');
    setPhase('Preparing data');
    setError('');
    setReportData(null);

    const reportStartedAt = new Date().toISOString();
    setLifecycle({
      phase: 'running',
      startedAt: reportStartedAt,
      progressPct: 5,
      message: 'Preparing data',
      updatedAt: reportStartedAt,
    });

    // Timeout
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timedOutRef.current = false;
    timeoutRef.current = setTimeout(() => {
      timedOutRef.current = true;
      controller.abort();
    }, REPORT_TIMEOUT_MS);

    const diagnosticData = prepareReportData(parsedData);

    (async () => {
      try {
        const response = await fetchRaw('/api/report/generate', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ llmId, diagnosticData }),
          signal: controller.signal,
        });

        if (!response.ok || !response.body) {
          const body = await response.text();
          throw new Error(`Request failed: ${response.status} ${response.statusText} - ${body.slice(0, 240)}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const parts = buffer.split('\n\n');
          buffer = parts.pop() || '';

          for (const part of parts) {
            const eventMatch = part.match(/^event:\s*(\S+)/m);
            const dataMatch = part.match(/^data:\s*(.*)/m);
            if (!eventMatch || !dataMatch) continue;

            const eventType = eventMatch[1];
            let payload: Record<string, unknown>;
            try {
              payload = JSON.parse(dataMatch[1]);
            } catch {
              continue;
            }

            if (eventType === 'phase') {
              const phaseLabel = String(payload.phase || '');
              setPhase(phaseLabel);
              setLifecycle({
                phase: 'running',
                startedAt: reportStartedAt,
                progressPct: 30,
                message: phaseLabel,
                subPhase: phaseLabel,
                updatedAt: new Date().toISOString(),
              });
            } else if (eventType === 'chunk') {
              const totalChars = (payload.totalChars as number) || 0;
              setPhase(`Generating report\u2026 (${totalChars.toLocaleString()} chars)`);
              setLifecycle({
                phase: 'running',
                startedAt: reportStartedAt,
                progressPct: 60,
                message: `Generating report (${totalChars.toLocaleString()} chars)`,
                subPhase: 'streaming',
                updatedAt: new Date().toISOString(),
              });
            } else if (eventType === 'done') {
              const jsonStr = stripMarkdownFences(String(payload.report || '{}'));
              try {
                const parsed = JSON.parse(jsonStr) as ReportData;
                setReportData(parsed);
                setStatus('ready');
                setPhase('Complete');
                setLifecycle({
                  phase: 'done',
                  startedAt: reportStartedAt,
                  finishedAt: new Date().toISOString(),
                  isEmpty: false,
                  message: 'Report ready',
                });
              } catch {
                const msg = 'Failed to parse report data from LLM. The model may have returned invalid JSON. Try again or select a different model.';
                setError(msg);
                setStatus('idle');
                setLifecycle({
                  phase: 'error',
                  startedAt: reportStartedAt,
                  finishedAt: new Date().toISOString(),
                  error: msg,
                  progressPct: 90,
                });
              }
            } else if (eventType === 'error') {
              const msg = String(payload.error || 'Unknown error');
              setError(msg);
              setStatus('idle');
              setLifecycle({
                phase: 'error',
                startedAt: reportStartedAt,
                finishedAt: new Date().toISOString(),
                error: msg,
                progressPct: 0,
              });
            }
          }
        }
      } catch (err) {
        let msg: string;
        if ((err as Error).name === 'AbortError') {
          msg = timedOutRef.current
            ? 'Report generation timed out (10 min). Try selecting a faster model.'
            : 'Report generation cancelled.';
        } else {
          msg = String(err);
        }
        setError(msg);
        setStatus('idle');
        setLifecycle({
          phase: 'error',
          startedAt: reportStartedAt,
          finishedAt: new Date().toISOString(),
          error: msg,
          progressPct: 0,
        });
      } finally {
        if (timeoutRef.current) {
          clearTimeout(timeoutRef.current);
          timeoutRef.current = null;
        }
      }
    })();
  }, [selectedLlmLabel, llmMap, setLifecycle]);

  const retry = useCallback(() => {
    if (lastParsedDataRef.current) {
      generate(lastParsedDataRef.current);
    }
  }, [generate]);

  const openOverlay = useCallback(() => {
    setIsOverlayOpen(true);
    setStatus('viewing');
  }, []);

  const closeOverlay = useCallback(() => {
    setIsOverlayOpen(false);
    setStatus('ready');
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  return {
    status, phase, llms: llms, isLoadingLlms,
    selectedLlmLabel, setSelectedLlmLabel,
    generate, reportData, error, retry,
    isOverlayOpen, openOverlay, closeOverlay,
    openSelector, closeSelector,
  };
}
