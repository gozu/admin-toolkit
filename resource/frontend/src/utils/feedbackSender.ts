/** Shape of GET /api/feedback/sender — who in-app feedback is sent as. */
export interface FeedbackSender {
  ok: boolean;
  /** Address feedback is actually sent as ('' = the mail channel's own sender). */
  sender: string;
  source: 'override' | 'user' | 'channel';
  /** The configured override ('' when unset — the default is the signed-in admin). */
  override: string;
  currentUser: string;
  currentUserEmail: string;
}

export function describeFeedbackSender(info: FeedbackSender): string {
  if (info.source === 'override') return `${info.sender} (set in Settings)`;
  if (info.source === 'user') return `${info.sender} (your DSS account email)`;
  return "the mail channel's own sender address";
}
