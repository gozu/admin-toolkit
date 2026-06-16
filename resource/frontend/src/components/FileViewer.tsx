import { useEffect, useRef, useCallback, useMemo } from 'react';
import hljs from 'highlight.js';
import 'highlight.js/styles/monokai-sublime.css';
import { Modal } from './Modal';
import { PacmanLoader } from './PacmanLoader';
import { formatBytes } from '../utils/formatters';

interface FileViewerProps {
  isOpen: boolean;
  onClose: () => void;
  filename: string;
  content: string;
  onDownload: (filename: string, content: string) => void;
}

// Read configurable limits from localStorage thresholds
function getFileViewerLimits() {
  try {
    const raw = window.localStorage.getItem('diagparser.thresholds');
    if (raw) {
      const t = JSON.parse(raw);
      return {
        maxLogLines: typeof t.fileViewerMaxLines === 'number' ? t.fileViewerMaxLines : 10000,
        maxHighlightSize: (typeof t.syntaxHighlightMaxKB === 'number' ? t.syntaxHighlightMaxKB : 500) * 1024,
      };
    }
  } catch { /* use defaults */ }
  return { maxLogLines: 10000, maxHighlightSize: 500 * 1024 };
}

const { maxLogLines: MAX_LOG_LINES, maxHighlightSize: MAX_HIGHLIGHT_SIZE } = getFileViewerLimits();

// Check if file is a log file
const isLogFile = (name: string): boolean => {
  return name.endsWith('.log') ||
         name.includes('backend.log') ||
         name.includes('fmmain.log') ||
         name.includes('ipython.log') ||
         name.includes('nginx') ||
         name.includes('supervisord');
};

// Get log level color class for log4j format
const getLogLevelClass = (line: string): string => {
  // Common log4j patterns: [ERROR], [WARN], [INFO], [DEBUG], [TRACE]
  // Also check for standalone level keywords
  if (/\[ERROR\]|ERROR\s|\.ERROR\s/i.test(line)) return 'log-error';
  if (/\[WARN\]|WARN\s|\.WARN\s|WARNING/i.test(line)) return 'log-warn';
  if (/\[INFO\]|INFO\s|\.INFO\s/i.test(line)) return 'log-info';
  if (/\[DEBUG\]|DEBUG\s|\.DEBUG\s/i.test(line)) return 'log-debug';
  if (/\[TRACE\]|TRACE\s|\.TRACE\s/i.test(line)) return 'log-trace';
  if (/Exception|Error:|Caused by:|at\s+[\w.$]+\(/i.test(line)) return 'log-error';
  return 'log-default';
};

export function FileViewer({
  isOpen,
  onClose,
  filename,
  content,
  onDownload,
}: FileViewerProps) {
  const preRef = useRef<HTMLPreElement>(null);

  // Check if this is a log file
  const isLog = useMemo(() => isLogFile(filename), [filename]);

  // Determine language for syntax highlighting
  const getLanguage = useCallback((name: string): string => {
    if (name.endsWith('.json')) return 'json';
    if (name.endsWith('.sh') || name.endsWith('.bash')) return 'bash';
    if (name.endsWith('.py')) return 'python';
    if (name.endsWith('.yml') || name.endsWith('.yaml')) return 'yaml';
    if (name.endsWith('.xml')) return 'xml';
    if (name.endsWith('.ini') || name.endsWith('.properties')) return 'ini';
    return 'plaintext';
  }, []);

  // Derive the displayed content synchronously from props. For log files we
  // keep only the last MAX_LOG_LINES; everything else is shown in full.
  const { displayedContent, totalLines, showingLastLines } = useMemo(() => {
    if (!isOpen || !content) {
      return { displayedContent: '', totalLines: 0, showingLastLines: false };
    }
    const lines = content.split('\n');
    if (isLog && lines.length > MAX_LOG_LINES) {
      return {
        displayedContent: lines.slice(-MAX_LOG_LINES).join('\n'),
        totalLines: lines.length,
        showingLastLines: true,
      };
    }
    return {
      displayedContent: content,
      totalLines: lines.length,
      showingLastLines: false,
    };
  }, [isOpen, content, isLog]);

  // Apply syntax highlighting for non-log files
  useEffect(() => {
    if (!isOpen || !preRef.current || !displayedContent || isLog) return;
    if (content.length > MAX_HIGHLIGHT_SIZE) return; // Skip highlighting for very large files

    const lang = getLanguage(filename);
    if (lang === 'plaintext') return; // No highlighting needed for plaintext

    requestAnimationFrame(() => {
      if (preRef.current) {
        const codeEl = document.createElement('code');
        codeEl.className = `language-${lang} hljs`;
        codeEl.textContent = displayedContent;
        try {
          hljs.highlightElement(codeEl);
          if (preRef.current) {
            preRef.current.innerHTML = '';
            preRef.current.appendChild(codeEl);
          }
        } catch (e) {
          console.error('Highlighting failed:', e);
        }
      }
    });
  }, [isOpen, displayedContent, content?.length, filename, getLanguage, isLog]);

  // Render log content with line-by-line coloring
  const renderLogContent = useMemo(() => {
    if (!isLog || !displayedContent) return null;

    const lines = displayedContent.split('\n');
    return lines.map((line, index) => (
      <div key={index} className={getLogLevelClass(line)}>
        {line || '\u00A0'}
      </div>
    ));
  }, [isLog, displayedContent]);

  const displayedLineCount = displayedContent?.split('\n').length || 0;

  const footer = (
    <div className="flex items-center justify-between">
      <span className="text-sm text-[var(--text-tertiary)]">
        {showingLastLines ? (
          <>Showing last {displayedLineCount.toLocaleString()} of {totalLines.toLocaleString()} lines | {formatBytes(content?.length || 0)}</>
        ) : (
          <>{totalLines.toLocaleString()} lines | {formatBytes(content?.length || 0)}</>
        )}
      </span>
      <button
        onClick={() => onDownload(filename, content)}
        className="flex items-center gap-2 px-4 py-2 bg-[#00b5aa] text-white rounded-lg
                   hover:bg-[#009990] transition-colors font-medium text-sm"
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
            d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
          />
        </svg>
        Download File
      </button>
    </div>
  );

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={filename} footer={footer}>
      <div className="file-viewer-content rounded-lg overflow-auto max-h-[70vh]">
        {!displayedContent ? (
          <div className="flex flex-col items-center justify-center p-8 bg-[#23241f]">
            <PacmanLoader />
            <p className="text-white mt-4">Loading {filename}...</p>
          </div>
        ) : isLog ? (
          // Log files with line-by-line coloring
          <div
            className="text-sm font-mono whitespace-pre p-4 m-0 bg-[#23241f] log-viewer"
            style={{ minHeight: '200px' }}
          >
            {showingLastLines && (
              <div className="text-center text-[var(--text-tertiary)] mb-2 pb-2 border-b border-[var(--border-default)]">
                ... {(totalLines - MAX_LOG_LINES).toLocaleString()} earlier lines truncated ...
              </div>
            )}
            {renderLogContent}
          </div>
        ) : (
          // Non-log files with syntax highlighting
          <pre
            ref={preRef}
            className="text-sm font-mono whitespace-pre-wrap break-words p-4 m-0 bg-[#23241f] text-[#f8f8f2]"
            style={{ minHeight: '200px' }}
          >
            {displayedContent}
          </pre>
        )}
      </div>
    </Modal>
  );
}
