# Admin Toolkit Maintenance Workflow

Use this workflow after feature work, before release/deploy, and whenever README,
screenshots, algorithm review notebooks, navigation, plugin settings, or release
metadata may be stale.

## Guardrails

- Treat `resource/frontend/src/utils/moduleRegistry.ts` as the source of truth for
  page ids, sidebar sections, labels, tool flags, lifecycle, trends, and command
  palette metadata.
- Treat `plugin.json` as the source of truth for plugin version and settings.
- Do not include local credentials, production audit logs, diagnostic dumps, or
  ignored scratch files in docs, screenshots, commits, or generated artifacts.
- Do not change deploy behavior during a maintenance pass unless the user asked
  for release/deploy work.
- Keep edits scoped to stale maintenance artifacts. Do not refactor product code
  unless the audit proves the product code is the stale artifact.

## Fast Audit

Run this from the repository root first:

```bash
node scripts/maintenance_audit.mjs
```

Use the output as the findings list for deterministic drift. Fix errors before a
release. Warnings are review items: either fix them or state why they are
intentional for this branch.

## Workflow

1. Inspect the change scope with `git status --short`, `git diff --stat`, and
   relevant `git diff` hunks.
2. Run `node scripts/maintenance_audit.mjs`.
3. Check whether changed files affect these artifacts:
   - README feature tour, page index, architecture counts, install/config
     sections, and security model.
   - `docs/screenshots/*` references and alt text.
   - `CHANGELOG.md` unreleased/release notes.
   - `plugin.json`, `resource/frontend/package.json`, and
     `resource/frontend/package-lock.json` versions.
   - `python-lib/adk_notebook/*` and `python-lib/adk_notebook/cards/*`
     algorithm review notebooks.
   - `docs/ui-ux-contracts.md`, contract scripts, and verification commands.
   - plugin settings, parameter sets, macros, and host-bound requirements.
4. Update stale artifacts only after checking the current code paths. Prefer
   generated facts from registries/scripts over remembered counts.
5. Re-run the audit script and the verification commands relevant to the files
   changed by the maintenance pass.

## README Checks

- Sync the version badge with `plugin.json`.
- Sync the page count and full page index with `MODULES`.
- Mark every `tool: true` module with the red advanced-action marker in the
  page index and do not mark non-tool pages.
- Update architecture counts from source:
  - route groups: `python-lib/adk_backend/routes/*.py` excluding `__init__.py`
  - runnable macros: `python-runnables/*/runnable.json`
- Keep screenshots inspectable and safe. If a screenshot may expose real
  customer/user/project data, anonymize or recapture it before tracking.

## Notebook Checks

When backend scan logic or frontend card behavior changes, check whether the
algorithm review notebook mirror needs an update:

- Shared notebook libraries: `python-lib/adk_notebook/*.py`
- Notebook cards: `python-lib/adk_notebook/cards/*.py`
- Materialization route: `python-lib/adk_backend/routes/algorithm_review.py`

At minimum, compile the notebook sources:

```bash
python3 -m py_compile python-lib/adk_notebook/*.py python-lib/adk_notebook/cards/*.py python-lib/adk_backend/routes/algorithm_review.py
```

If the webapp algorithm and notebook algorithm intentionally diverge, document
the reason in the notebook card or maintenance summary.

## Screenshot Checks

- Reuse existing screenshot tooling before creating anything new.
- Current tracked browser tooling lives in `resource/frontend/playwright.config.ts`
  and `resource/frontend/tests/*.spec.ts`; use `cd resource/frontend && npx
  playwright test` for the existing smoke/failure-screenshot path.
- For installation docs, prefer these canonical captures when a safe DSS admin
  UI session is available:
  - DSS plugin install page with `https://github.com/gozu/admin-toolkit.git`
    entered.
  - `hash.html` secret-generator page after a dummy password generated a secret
    (never show a real production password or secret).
  - Admin Toolkit Settings -> Remote Hosts add/edit dialog with dummy host
    values.
  - Host picker remote-install dialog showing the git and ZIP install choices.
- Historical capture helpers existed at `resource/frontend/take-screenshots.mjs`
  and `resource/frontend/screenshot-test.ts` in commit `d88ef74` and were
  removed by cleanup commit `b9aa482`. If a canonical capture script is needed,
  recover/adapt those from git history intentionally instead of writing a fresh
  flow from scratch.
- Prefer live app screenshots only from safe demo data.
- Keep stable dimensions and avoid screenshots mid-loading unless documenting a
  loading/progress state.
- Update README image references and alt text when replacing screenshots.
- If the existing tooling cannot capture the changed feature safely, report that
  gap and the exact missing capture path.

## Verification

Use the repo rules from `AGENTS.md` and `CLAUDE.md`:

- Frontend UI/docs-adjacent changes: `cd resource/frontend && npm run typecheck`
- UI build-impacting changes: `cd resource/frontend && npm run build`
- Navigation/progress/trends contracts:
  `cd resource/frontend && npm run check:contracts`
- Python changes: `pytest tests/backend`
- Maintenance drift: `node scripts/maintenance_audit.mjs`

Report commands that were not run and why.

## Output

Lead with remaining findings, if any. Then summarize:

- maintenance artifacts updated
- verification run
- known gaps, especially missing screenshots, stale external data, or notebook
  parity that needs live DSS validation
