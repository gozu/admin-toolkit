import { useState } from 'react';
import { fetchRaw } from '../utils/api';

/**
 * Settings action: download a single zip aggregating the read-only debug
 * endpoints (perf snapshot, backend.log tail, parsed log errors) so a customer
 * can hand over one file for offline diagnosis. Triggers no scans.
 */
export function SupportBundleCard() {
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const download = async () => {
    setDownloading(true);
    setError(null);
    try {
      const res = await fetchRaw('/api/debug/support-bundle');
      if (!res.ok) {
        throw new Error(`Bundle request failed: ${res.status} ${res.statusText}`);
      }
      const blob = await res.blob();
      const disposition = res.headers.get('Content-Disposition') ?? '';
      const name =
        /filename="([^"]+)"/.exec(disposition)?.[1] ?? 'admin-toolkit-support-bundle.zip';
      const url = URL.createObjectURL(blob);
      Object.assign(document.createElement('a'), { href: url, download: name }).click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDownloading(false);
    }
  };

  return (
    <section className="glass-card p-4 space-y-3">
      <div>
        <h3 className="text-lg font-semibold text-[var(--text-primary)]">Support Bundle</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Downloads a zip with the backend&apos;s diagnostic snapshot: performance/cache state,
          backend settings, the last 1&thinsp;MB of backend.log and the parsed log errors.
          Read-only and safe to run anytime — share the file when reporting a problem.
        </p>
      </div>

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => void download()}
          disabled={downloading}
          className="px-3 py-1.5 rounded bg-[var(--bg-glass)] hover:bg-[var(--bg-glass-hover)] text-sm text-[var(--text-primary)] transition-colors disabled:opacity-50"
        >
          {downloading ? 'Collecting…' : 'Download support bundle'}
        </button>
      </div>

      {error && <p className="text-sm text-[var(--neon-red)]">{error}</p>}
    </section>
  );
}
