import { useCallback, useEffect, useMemo, useState } from 'react';
import { useDiag } from '../context/DiagContext';

function formatTime(ts: string): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return ts;
  return date.toISOString().slice(11, 19);
}

export function DebugPanel() {
  const { state, clearDebugLogs } = useDiag();
  const { debugLogs } = state;
  const [visible, setVisible] = useState(false);
  const [isOpen, setIsOpen] = useState(false);

  const toggle = useCallback(() => {
    setVisible((v) => {
      if (v) setIsOpen(false);
      return !v;
    });
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Ignore when typing in inputs/textareas
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if ((e.target as HTMLElement)?.isContentEditable) return;
      if (e.key === 'd' && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        toggle();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [toggle]);

  const textDump = useMemo(
    () => debugLogs.map((e) => {
      if (e.message.startsWith('TIMING_TABLE:')) {
        const rows = e.message.slice('TIMING_TABLE:'.length).split(';;').map((r) => {
          const [label, dur] = r.split('|');
          return `  ${label.padEnd(28)} ${dur}`;
        });
        return `${formatTime(e.timestamp)} Endpoint Timing Summary\n${rows.join('\n')}`;
      }
      return `${formatTime(e.timestamp)} ${e.message}`;
    }).join('\n'),
    [debugLogs]
  );

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(textDump || 'No debug logs');
    } catch {
      // Ignore clipboard errors in restricted environments
    }
  };

  if (!visible) return null;

  return (
    <div className="w-full mt-4 px-3 pb-3">
      <div>
        <div className="bg-[rgba(8,12,20,0.95)] border border-[var(--border-glass)] rounded-lg shadow-xl overflow-hidden">
          <div className="flex items-center justify-between px-3 py-2 border-b border-[var(--border-glass)]">
            <button
              type="button"
              onClick={() => setIsOpen((v) => !v)}
              className="text-xs font-mono text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            >
              {isOpen ? 'Hide Debug' : 'Show Debug'} ({debugLogs.length})
            </button>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleCopy}
                className="px-2 py-1 text-xs rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-[var(--text-secondary)]"
              >
                Copy
              </button>
              <button
                type="button"
                onClick={clearDebugLogs}
                className="px-2 py-1 text-xs rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-[var(--text-secondary)]"
              >
                Clear
              </button>
              <button
                type="button"
                onClick={toggle}
                className="px-2 py-1 text-xs rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-[var(--text-secondary)]"
              >
                Hide
              </button>
            </div>
          </div>

          {isOpen && (
            <div className="max-h-56 overflow-y-auto text-xs font-mono px-3 py-2 space-y-1">
              {debugLogs.length === 0 && (
                <div className="text-[var(--text-muted)]">No debug messages yet.</div>
              )}
              {debugLogs.map((entry) => {
                const colorClass =
                  entry.level === 'error'
                    ? 'text-red-300'
                    : entry.level === 'warn'
                      ? 'text-amber-300'
                      : 'text-[var(--text-secondary)]';

                if (entry.message.startsWith('TIMING_TABLE:')) {
                  const rows = entry.message.slice('TIMING_TABLE:'.length).split(';;').map((r) => {
                    const [label, dur] = r.split('|');
                    const isFail = dur.endsWith(' FAIL');
                    const isSkip = dur.endsWith(' SKIP');
                    return { label, duration: dur.replace(/ (FAIL|SKIP)$/, ''), isFail, isSkip };
                  });
                  return (
                    <div key={entry.id} className="mt-2">
                      <div className="text-[var(--text-muted)] mb-1">
                        {formatTime(entry.timestamp)} Endpoint Timing Summary
                      </div>
                      <table className="w-full text-xs font-mono border-collapse">
                        <thead>
                          <tr className="text-[var(--text-muted)]">
                            <th className="text-left pr-4 pb-0.5 border-b border-[var(--border-glass)]">Endpoint</th>
                            <th className="text-right pr-4 pb-0.5 border-b border-[var(--border-glass)]">Duration</th>
                            <th className="text-left pb-0.5 border-b border-[var(--border-glass)]">Status</th>
                          </tr>
                        </thead>
                        <tbody>
                          {rows.map((r) => (
                            <tr key={r.label} className={r.isFail ? 'text-red-300' : r.isSkip ? 'text-[var(--text-muted)]' : 'text-[var(--text-secondary)]'}>
                              <td className="pr-4 py-px">{r.label}</td>
                              <td className="text-right pr-4 py-px">{r.duration}</td>
                              <td className="py-px">{r.isFail ? 'FAIL' : r.isSkip ? 'SKIP' : 'OK'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  );
                }

                return (
                  <div key={entry.id} className={colorClass}>
                    <span className="text-[var(--text-muted)]">{formatTime(entry.timestamp)}</span>
                    {' '}
                    <span>{entry.message}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
