import { useEffect, useState } from 'react';
import { fetchJson, ApiRequestError } from '../utils/api';
import { useRedState } from '../state/redUnlockStore';
import { UnlockModal } from './UnlockModal';
import { describeFeedbackSender, type FeedbackSender } from '../utils/feedbackSender';

/**
 * Feedback sender address — the "from" on in-app feedback mail. Defaults to the
 * DSS email of the admin using the toolkit; an override is for instances whose
 * SMTP relay only accepts one envelope sender. Saving writes a plugin param, so
 * it is advanced-gated like every other mutating settings surface.
 */
export function FeedbackSenderField() {
  const [info, setInfo] = useState<FeedbackSender | null>(null);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState(false);

  const { authed: unlocked } = useRedState();
  const [showUnlock, setShowUnlock] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchJson<FeedbackSender>('/api/feedback/sender')
      .then((res) => {
        if (cancelled) return;
        setInfo(res);
        setDraft(res.override);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ApiRequestError ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const dirty = !!info && draft.trim() !== info.override;

  const doSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const res = await fetchJson<FeedbackSender>('/api/feedback/sender', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: draft.trim() }),
      });
      setInfo(res);
      setDraft(res.override);
      setSavedMsg(true);
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const requestSave = () => {
    if (!unlocked) {
      setShowUnlock(true);
      return;
    }
    void doSave();
  };

  return (
    <div className="space-y-1 max-w-sm">
      <span className="text-sm font-medium text-[var(--text-primary)]">
        Feedback sender address
      </span>
      <div className="flex items-center gap-2">
        <input
          type="email"
          className="input-glass w-full font-mono text-sm"
          placeholder={info?.currentUserEmail || 'you@company.com'}
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
            setSavedMsg(false);
          }}
          disabled={!info}
        />
        <button
          type="button"
          onClick={requestSave}
          disabled={!dirty || saving}
          className={`flex-shrink-0 px-3 py-1.5 rounded text-sm transition-colors ${
            !dirty || saving
              ? 'bg-[var(--bg-glass)] text-[var(--text-tertiary)] cursor-not-allowed'
              : 'btn-primary'
          }`}
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
      {error && <p className="text-xs text-[var(--neon-red)]">{error}</p>}
      {info && !error && (
        <p className="text-xs text-[var(--text-muted)]">
          {savedMsg && <span className="text-[var(--text-secondary)]">Saved. </span>}
          Right now feedback is sent as{' '}
          {info.source === 'override' ? info.sender : describeFeedbackSender(info)}. Leave empty
          to send as whoever is signed in; set an address when your SMTP relay only accepts one
          sender.
        </p>
      )}
      <UnlockModal isOpen={showUnlock} onClose={() => setShowUnlock(false)} />
    </div>
  );
}
