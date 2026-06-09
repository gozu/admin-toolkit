import { createSyncStore } from './createSyncStore';
import type { PageId } from '../types';

// The page the user was viewing when they opened Feedback. Captured by the
// header button right before it navigates to the Feedback page, so the form's
// diagnostics block can report "page you came from". Null when the page was
// opened directly (e.g. via the MISC sidebar entry).
export const feedbackFromPageStore = createSyncStore<PageId | null>(null);
