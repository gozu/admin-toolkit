import { useState, useRef, useCallback, useEffect } from 'react';
import { useDiag } from '../../context/DiagContext';
import { useReportGenerator } from '../../hooks/useReportGenerator';
import { ModelPicker } from '../ModelPicker';
import { ReportOverlay } from '../ReportOverlay';
import { exportReportAsHtml } from '../../utils/exportReport';
import { extractAllCSS } from '../../utils/extractCSS';
import { prepareReportData } from '../../utils/prepareReportData';
import { useTheme } from '../../hooks/useTheme';
import { Spinner } from '../common/Spinner';

export function ReportPage() {
  const { state } = useDiag();
  const { parsedData } = state;
  const { theme } = useTheme();

  const {
    status,
    phase,
    llms,
    isLoadingLlms,
    selectedLlmId,
    setSelectedLlmId,
    generate,
    reportData,
    error,
    retry,
    isOverlayOpen,
    openOverlay,
    closeOverlay,
    openSelector,
    closeSelector,
  } = useReportGenerator();

  // Verbose status tracking
  const [elapsedMs, setElapsedMs] = useState(0);
  const [payloadSize, setPayloadSize] = useState(0);
  const [logs, setLogs] = useState<string[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startRef = useRef(0);
  const logPanelRef = useRef<HTMLDivElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);

  // Fetch LLMs on mount by triggering the selector open/close cycle
  const mountedRef = useRef(false);
  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true;
      openSelector();
      closeSelector();
    }
  }, [openSelector, closeSelector]);

  const addLog = useCallback((msg: string) => {
    const ts = new Date().toLocaleTimeString('en-US', { hour12: false });
    setLogs((prev) => [...prev, `[${ts}] ${msg}`]);
  }, []);

  // Auto-scroll log panel
  useEffect(() => {
    if (logPanelRef.current) {
      logPanelRef.current.scrollTop = logPanelRef.current.scrollHeight;
    }
  }, [logs]);

  // Track phase changes in verbose log (update last line in-place for chunk progress)
  useEffect(() => {
    if (phase) {
      const ts = new Date().toLocaleTimeString('en-US', { hour12: false });
      const line = `[${ts}] Phase: ${phase}`;
      if (phase.startsWith('Generating report')) {
        // Update the last log line in-place instead of appending
        // Accumulating a verbose log from streamed phases is genuine effect work.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setLogs((prev) => {
          if (prev.length > 0 && prev[prev.length - 1].includes('Generating report')) {
            return [...prev.slice(0, -1), line];
          }
          return [...prev, line];
        });
      } else {
        addLog(`Phase: ${phase}`);
      }
    }
  }, [phase, addLog]);

  // Track error in verbose log
  useEffect(() => {
    if (error) {
      // Append the error to the verbose log — accumulating a log is effect work.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      addLog(`ERROR: ${error}`);
    }
  }, [error, addLog]);

  // Elapsed timer during generation
  useEffect(() => {
    if (status === 'generating') {
      startRef.current = Date.now();
      // Drive an elapsed-time timer — timers are canonical effect territory.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setElapsedMs(0);
      timerRef.current = setInterval(() => {
        setElapsedMs(Date.now() - startRef.current);
      }, 200);
    } else {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      if (status === 'ready' && startRef.current > 0) {
        setElapsedMs(Date.now() - startRef.current);
      }
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [status]);

  const handleGenerate = useCallback(() => {
    if (!selectedLlmId) return;
    setLogs([]);
    const payload = prepareReportData(parsedData);
    const size = JSON.stringify(payload).length;
    setPayloadSize(size);
    addLog(`Selected LLM: ${selectedLlmId}`);
    addLog(`Data payload: ${size.toLocaleString()} chars`);
    addLog('Starting generation…');
    generate(parsedData);
  }, [selectedLlmId, parsedData, generate, addLog]);

  const handleOpenNewTab = useCallback(() => {
    if (!reportData) return;
    openOverlay();
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const overlay = document.querySelector('.report-overlay') as HTMLElement;
        if (overlay) {
          const css = extractAllCSS();

          const clone = overlay.cloneNode(true) as HTMLElement;
          // Convert images to base64 for the blob context
          const clonedImgs = clone.querySelectorAll('img');
          const originalImgs = overlay.querySelectorAll('img');
          for (let i = 0; i < clonedImgs.length; i++) {
            const orig = originalImgs[i] as HTMLImageElement | undefined;
            if (orig && orig.complete && orig.naturalWidth > 0) {
              try {
                const c = document.createElement('canvas');
                c.width = orig.naturalWidth;
                c.height = orig.naturalHeight;
                const ctx = c.getContext('2d');
                if (ctx) {
                  ctx.drawImage(orig, 0, 0);
                  clonedImgs[i].src = c.toDataURL('image/png');
                }
              } catch {
                /* CORS */
              }
            }
          }
          const slides = clone.querySelectorAll('[data-slide-index]');
          slides.forEach((s, i) => {
            (s as HTMLElement).classList.remove('active');
            if (i === 0) (s as HTMLElement).classList.add('active');
          });

          const navScript = `(function(){var c=0,s=document.querySelectorAll('[data-slide-index]'),t=s.length,ct=document.getElementById('slide-counter');function show(i){s.forEach(function(e){e.classList.remove('active')});if(s[i])s[i].classList.add('active');if(ct)ct.textContent=(i+1)+' / '+t;var pb=document.querySelector('.report-progress-bar');if(pb)pb.style.width=((i+1)/t*100)+'%';}document.addEventListener('keydown',function(e){if(e.key==='ArrowRight'||e.key===' '){e.preventDefault();c=Math.min(c+1,t-1);show(c);}else if(e.key==='ArrowLeft'){e.preventDefault();c=Math.max(c-1,0);show(c);}});var p=document.getElementById('nav-prev'),n=document.getElementById('nav-next');if(p)p.onclick=function(){c=Math.max(c-1,0);show(c);};if(n)n.onclick=function(){c=Math.min(c+1,t-1);show(c);};var dl=document.querySelector('[title="Download as HTML"]');if(dl)dl.parentElement.removeChild(dl);var cl=document.querySelector('[title="Close (Esc)"]');if(cl)cl.parentElement.removeChild(cl);})();`;

          const navBtns = clone.querySelectorAll('.report-nav-btn');
          if (navBtns[0]) navBtns[0].id = 'nav-prev';
          if (navBtns[1]) navBtns[1].id = 'nav-next';
          const counter = clone.querySelector('.report-nav span');
          if (counter) counter.id = 'slide-counter';

          const company = parsedData.company || 'unknown';
          const date = new Date().toISOString().slice(0, 10);
          const html = `<!DOCTYPE html><html data-theme="${theme}"><head><meta charset="utf-8"><title>Health Check - ${company} - ${date}</title><style>@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Roboto:wght@400..700&display=swap');${css}body{margin:0;overflow:hidden;font-family:'Roboto',sans-serif;}</style></head><body>${clone.outerHTML}<script>${navScript}</script></body></html>`;

          const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
          const url = URL.createObjectURL(blob);
          window.open(url, '_blank');
          setTimeout(() => URL.revokeObjectURL(url), 5000);
        }
        closeOverlay();
      });
    });
  }, [reportData, openOverlay, closeOverlay, parsedData.company, theme]);

  const handleDownload = useCallback(() => {
    // Briefly show the overlay to render the portal DOM, then export
    openOverlay();
    setTimeout(() => {
      try {
        const overlay = document.querySelector('.report-overlay') as HTMLElement;
        if (overlay) {
          exportReportAsHtml(overlay, parsedData.company || 'unknown', theme);
        }
      } finally {
        closeOverlay();
      }
    }, 500);
  }, [openOverlay, closeOverlay, parsedData.company, theme]);

  const formatElapsed = (ms: number) => {
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  };

  return (
    <div className="w-full py-4">
      {/* Header */}
      <div className="mb-6 px-6">
        <h1
          className="text-2xl font-bold text-[var(--text-primary)] mb-2"
          style={{ fontFamily: 'var(--font-display)', letterSpacing: 'normal' }}
        >
          Quarterly Health Check Report
        </h1>
        <p className="text-sm text-[var(--text-secondary)] max-w-2xl">
          Generate an AI-powered health check report from the current diagnostic data. The report
          produces a presentation-style slideshow covering system health, project footprint, code
          environments, and actionable recommendations.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left: Controls */}
        <div className="rounded-lg p-6 space-y-5">
          {/* LLM Selector */}
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-tertiary)] mb-2">
              LLM Model
            </label>
            {isLoadingLlms ? (
              <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                <Spinner />
                Loading models...
              </div>
            ) : (
              <ModelPicker
                llms={llms}
                selectedId={selectedLlmId}
                onChange={setSelectedLlmId}
                placeholder="Select an LLM model..."
                className="w-full rounded-md border border-[var(--border-default)] bg-[var(--bg-elevated)] text-[var(--text-primary)] px-3 py-2 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-[var(--accent)]"
                onEnterWithClosed={handleGenerate}
              />
            )}
          </div>

          {/* Generate Button */}
          <button
            type="button"
            onClick={handleGenerate}
            disabled={status === 'generating' || !selectedLlmId}
            className={`w-full flex items-center justify-center gap-2 rounded-md px-4 py-2.5 text-sm font-medium transition-colors ${
              status === 'generating' || !selectedLlmId
                ? 'bg-[var(--bg-hover)] text-[var(--text-tertiary)] cursor-not-allowed'
                : 'bg-[var(--accent)] text-white hover:opacity-90'
            }`}
          >
            {status === 'generating' ? (
              <>
                <span className="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Generating...
              </>
            ) : (
              'Generate Report'
            )}
          </button>

          {/* AI Disclaimer */}
          <p className="text-xs text-[var(--text-tertiary)] leading-relaxed">
            This report is generated by an LLM and may contain inaccuracies. Always verify critical
            findings against the raw diagnostic data before sharing with customers.
          </p>

          {/* Action Buttons (when ready) */}
          {status === 'ready' && reportData && (
            <div className="flex gap-3 pt-2">
              <button
                type="button"
                onClick={handleOpenNewTab}
                className="flex-1 flex items-center justify-center gap-2 rounded-md px-4 py-2 text-sm font-medium bg-[var(--accent)] text-white hover:opacity-90 transition-colors"
              >
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                  <circle cx="12" cy="12" r="3" />
                </svg>
                View Report
              </button>
              <button
                type="button"
                onClick={handleDownload}
                className="flex-1 flex items-center justify-center gap-2 rounded-md px-4 py-2 text-sm font-medium border border-[var(--border-default)] text-[var(--text-primary)] hover:bg-[var(--bg-hover)] transition-colors"
              >
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={2}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="7 10 12 15 17 10" />
                  <line x1="12" y1="15" x2="12" y2="3" />
                </svg>
                Download HTML
              </button>
            </div>
          )}

          {/* Retry on error */}
          {error && status === 'idle' && (
            <button
              type="button"
              onClick={retry}
              className="w-full flex items-center justify-center gap-2 rounded-md px-4 py-2 text-sm font-medium border border-red-500/30 text-red-400 hover:bg-red-500/10 transition-colors"
            >
              Retry
            </button>
          )}
        </div>

        {/* Right: Verbose Status Panel */}
        <div className="rounded-lg flex flex-col">
          <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border-default)]">
            <span className="text-xs font-semibold uppercase tracking-wider text-[var(--text-tertiary)]">
              Status
            </span>
            {status === 'generating' && (
              <span className="inline-block w-2 h-2 rounded-full bg-yellow-400 animate-pulse" />
            )}
            {status === 'ready' && <span className="inline-block w-2 h-2 rounded-full bg-white" />}
            {error && <span className="inline-block w-2 h-2 rounded-full bg-red-400" />}
          </div>

          {/* Status fields */}
          <div className="px-4 py-3 border-b border-[var(--border-default)] grid grid-cols-2 gap-x-4 gap-y-2 text-xs font-mono">
            <div>
              <span className="text-[var(--text-tertiary)]">Phase: </span>
              <span className="text-[var(--text-primary)]">{phase || 'Idle'}</span>
            </div>
            <div>
              <span className="text-[var(--text-tertiary)]">Elapsed: </span>
              <span className="text-[var(--text-primary)]">
                {elapsedMs > 0 ? formatElapsed(elapsedMs) : '--'}
              </span>
            </div>
            <div>
              <span className="text-[var(--text-tertiary)]">Model: </span>
              <span className="text-[var(--text-primary)] break-all">{selectedLlmId || '--'}</span>
            </div>
            <div>
              <span className="text-[var(--text-tertiary)]">Payload: </span>
              <span className="text-[var(--text-primary)]">
                {payloadSize > 0 ? `${payloadSize.toLocaleString()} chars` : '--'}
              </span>
            </div>
          </div>

          {/* Log output */}
          <div
            ref={logPanelRef}
            className="flex-1 min-h-[200px] max-h-[400px] overflow-y-auto px-4 py-3 font-mono text-xs leading-relaxed text-[var(--text-secondary)] bg-[var(--bg-base)]"
          >
            {logs.length === 0 ? (
              <span className="text-[var(--text-tertiary)]">
                Waiting for generation to start...
              </span>
            ) : (
              logs.map((line, i) => (
                <div
                  key={i}
                  className={
                    line.includes('ERROR')
                      ? 'text-red-400'
                      : line.includes('Complete')
                        ? 'text-[var(--success)]'
                        : ''
                  }
                >
                  {line}
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Report Overlay (full-screen portal) */}
      {isOverlayOpen && reportData && (
        <div ref={overlayRef}>
          <ReportOverlay reportData={reportData} parsedData={parsedData} onClose={closeOverlay} />
        </div>
      )}
    </div>
  );
}
