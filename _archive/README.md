# _archive/

Disabled 2026-05-21. Mirrors original paths to make reintegration straightforward.

Excluded from the deploy ZIP via `.gitattributes` (`_archive/ export-ignore`), so
nothing here ships with `make plugin` / `make deploy`. Kept in the repo as
reference code for retired features:

- **Tetris minigame** — `resource/frontend/src/components/TetrisGame.tsx`
- **Tracking (in-memory + SQL)** — `python-lib/tracking.py`, `python-lib/sql_tracking.py`, `resource/frontend/src/components/TrackingView.tsx`
- **Compare registry** — `python-lib/compare_registry.py` (used only by tracking + trends)
- **Trends** — `python-lib/trends_registry.py`, `resource/frontend/src/components/pages/TrendsPage.tsx`
- **Outreach (campaigns)** — `resource/frontend/src/components/ToolsView.tsx`
- **Tests** — `tests/backend/test_compare.py`, `tests/backend/test_trends_registry.py`

The email-send primitive itself (`/api/tools/email/preview`,
`/api/tools/email/send`, `/api/mail-channels`, plus the
`_list_mail_channels`/`_get_mail_channel`/`_get_configured_mail_channel`
helpers in `webapps/admin-toolkit/backend.py`) stays in the active source tree
because the FS Migration page calls it to email local-filesystem owners.

## Re-enabling

1. Move the file(s) back to their original paths (the directory layout under
   `_archive/` mirrors the live tree).
2. Restore the matching `PageId` literal in `resource/frontend/src/types/index.ts`
   and the `ModuleDefinition` in `resource/frontend/src/utils/moduleRegistry.ts`.
3. Restore the lazy `case` arms in `resource/frontend/src/components/layout/PageRouter.tsx`.
4. Restore the deleted backend routes in `webapps/admin-toolkit/backend.py`.
5. For trends specifically, also un-neutralize `scripts/check_trends_contract.py`.
