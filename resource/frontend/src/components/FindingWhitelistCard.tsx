import { useEffect, useState } from 'react';
import { useRedState } from '../state/redUnlockStore';
import {
  whitelistStore,
  loadWhitelist,
  removeWhitelistEntry,
} from '../state/whitelistStore';
import type { FindingWhitelistEntry } from '../hooks/useHealthScore';

/** Settings → Finding whitelist: the per-item false-positive suppression list.
 *  Entries are added from Summary issue rows ("Whitelist" buttons); this card
 *  lists and removes them. Mutations are advanced-gated server-side. */
export function FindingWhitelistCard() {
  const { entries, loaded, error } = whitelistStore.use();
  const { authed } = useRedState();
  const [busy, setBusy] = useState<FindingWhitelistEntry | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);

  useEffect(() => {
    if (!loaded) void loadWhitelist();
  }, [loaded]);

  const remove = async (entry: FindingWhitelistEntry) => {
    setBusy(entry);
    setRemoveError(null);
    try {
      await removeWhitelistEntry(entry);
    } catch (e) {
      setRemoveError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="glass-card p-4 space-y-3">
      <div>
        <h3 className="text-lg font-semibold text-[var(--text-primary)]">Finding Whitelist</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Per-item suppression of thresholded findings (large code envs, oversized projects,
          deprecated-Python cleanup, disk usage). Whitelisted items are silently skipped by the
          health score, issue lists and agent findings. Add items from an issue&apos;s
          &ldquo;Whitelist&rdquo; buttons on the Summary page; remove them here.
        </p>
      </div>
      {entries.length === 0 ? (
        <p className="text-sm text-[var(--text-muted)] italic">No whitelisted items.</p>
      ) : (
        <div className="space-y-1">
          {entries.map((entry) => {
            const key = `${entry.rule}:${entry.item}:${entry.host || 'local'}`;
            return (
              <div
                key={key}
                className="flex flex-wrap items-center gap-2 rounded border border-[var(--border-glass)] px-2 py-1 text-sm"
              >
                <span className="rounded bg-[var(--bg-glass)] px-1.5 py-0.5 text-xs font-mono text-[var(--text-secondary)]">
                  {entry.rule}
                </span>
                <span className="font-mono text-[var(--text-primary)]">{entry.item}</span>
                <span className="text-xs text-[var(--text-muted)]">host {entry.host || 'local'}</span>
                {entry.note && <span className="text-xs text-[var(--text-muted)]">— {entry.note}</span>}
                {entry.addedAt && (
                  <span className="text-xs text-[var(--text-muted)]">added {entry.addedAt.slice(0, 10)}</span>
                )}
                {authed && (
                  <button
                    type="button"
                    onClick={() => void remove(entry)}
                    disabled={busy !== null}
                    className="ml-auto rounded px-2 py-0.5 text-xs text-[var(--neon-red)] transition-colors hover:bg-[var(--neon-red)]/10 disabled:opacity-50"
                  >
                    {busy === entry ? 'Removing…' : 'Remove'}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
      {!authed && entries.length > 0 && (
        <p className="text-xs text-[var(--text-muted)]">
          Unlock Advanced Actions above to remove entries.
        </p>
      )}
      {(error || removeError) && (
        <p className="text-xs text-[var(--neon-red)]">{removeError || error}</p>
      )}
    </section>
  );
}
