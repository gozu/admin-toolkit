import { useState, useEffect } from 'react';
import { timer, type TimingEntry } from '../utils/timing';

export function PerformanceMetrics() {
  const [metrics, setMetrics] = useState<{ total: number; entries: TimingEntry[] } | null>(null);
  const [isExpanded, setIsExpanded] = useState(false);

  useEffect(() => {
    // Get metrics after a brief delay to ensure all parsing is done
    const timeout = setTimeout(() => {
      const summary = timer.getSummary();
      if (summary.entries.length > 0) {
        setMetrics(summary);
      }
    }, 100);
    return () => clearTimeout(timeout);
  }, []);

  if (!metrics) return null;

  // Group entries by phase (extract: vs parse:)
  const extractEntries = metrics.entries.filter(e => e.label.startsWith('extract:'));
  const parseEntries = metrics.entries.filter(e => e.label.startsWith('parse:'));

  // Get totals for each phase
  const extractTotal = extractEntries.find(e => e.label === 'extract:total')?.duration || 0;
  const parseTotal = parseEntries.find(e => e.label === 'parse:total')?.duration || 0;

  // Friendly label names
  const labelNames: Record<string, string> = {
    'getEntries': 'ZIP index',
    'detectType': 'Detect type',
    'localconfig': 'Localconfig',
    'categorize': 'Categorize',
    'mainFiles': 'Config files',
    'parallel': 'Data files (parallel)',
    'outputLog': 'Output log',
    'projectFiles': 'Projects',
    'pluginsCodeEnvs': 'Plugins & envs',
    'clusters': 'Clusters',
    'rootFiles': 'Root files',
  };

  const formatLabel = (label: string) => {
    const key = label.replace('extract:', '').replace('parse:', '');
    return labelNames[key] || key;
  };

  return (
    <footer className="mt-8 border-t border-[var(--border-glass)] pt-4 pb-6">
      <div className="max-w-7xl mx-auto px-4">
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="flex items-center gap-2 text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors text-sm font-mono"
        >
          <svg
            className={`w-4 h-4 transition-transform ${isExpanded ? 'rotate-90' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
          <span>Performance: {metrics.total.toFixed(0)}ms total</span>
          <span className="text-[var(--text-muted)]">
            (extract: {extractTotal.toFixed(0)}ms, parse: {parseTotal.toFixed(0)}ms)
          </span>
        </button>

        {isExpanded && (
          <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-6 text-sm font-mono">
            {/* Extract Phase */}
            <div>
              <h4 className="text-[var(--neon-cyan)] mb-2 text-xs uppercase tracking-wider">
                Extraction Phase
              </h4>
              <div className="space-y-1">
                {extractEntries
                  .filter(e => e.label !== 'extract:total')
                  .map((entry) => (
                    <div key={entry.label} className="flex justify-between text-[var(--text-secondary)]">
                      <span>{formatLabel(entry.label)}</span>
                      <span className="text-[var(--text-muted)]">{entry.duration.toFixed(1)}ms</span>
                    </div>
                  ))}
                <div className="flex justify-between text-[var(--text-primary)] border-t border-[var(--border-glass)] pt-1 mt-2">
                  <span>Total</span>
                  <span>{extractTotal.toFixed(1)}ms</span>
                </div>
              </div>
            </div>

            {/* Parse Phase */}
            <div>
              <h4 className="text-[var(--neon-magenta)] mb-2 text-xs uppercase tracking-wider">
                Parsing Phase
              </h4>
              <div className="space-y-1">
                {parseEntries
                  .filter(e => e.label !== 'parse:total')
                  .map((entry) => (
                    <div key={entry.label} className="flex justify-between text-[var(--text-secondary)]">
                      <span>{formatLabel(entry.label)}</span>
                      <span className="text-[var(--text-muted)]">{entry.duration.toFixed(1)}ms</span>
                    </div>
                  ))}
                <div className="flex justify-between text-[var(--text-primary)] border-t border-[var(--border-glass)] pt-1 mt-2">
                  <span>Total</span>
                  <span>{parseTotal.toFixed(1)}ms</span>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </footer>
  );
}
