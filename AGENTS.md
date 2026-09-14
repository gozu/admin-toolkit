# Admin Toolkit Agent Rules

## Product Soul

Build a polished, fast, dense admin experience. Interactions should feel smooth and immediate, with stable dimensions, no avoidable layout shifts, and no silent long waits. Favor progressive disclosure through expandable rows, popovers, panels, and compact controls over extra permanent chrome.

## UI Contracts

- Prefer functional React components and hooks; class components are allowed for error boundaries.
- Use the module registry for page ids, nav sections, command-palette metadata, experimental flags, and availability policies.
- Use the shared `ProgressIndicator` for async module work. New code passes a `Lifecycle`; the component derives its tone, so never pass a `tone` prop.
- Progress colors: grey for queued/loading/unavailable, yellow for active/partial/waiting/stalled, green for successful completion, red for failure. White remains available for neutral ready/current status outside progress bars.
- Keep rendering cheap: memoize derived rows, keep list/table dimensions stable, avoid expensive work during render, and prefer GPU-friendly transforms/opacity for animation.

## Data Contracts

- New trends tables must be added through the typed trends registry.
- Snapshot and compare participation must be validated by tests, not remembered manually.

## Shared Utilities and State

Frontend paths below are relative to `resource/frontend/src/`.

- Parse SSE with `utils/sseStream.parseSseStream`. Inline buffer-splitting parsers are banned by the contract checker.
- Build module-scoped singleton stores with `state/createSyncStore`, or streaming scan stores with `state/createModuleScanStore`.
- Set `{ sessionScoped: true }` on stores holding host/session data so they reset on session changes. `createModuleScanStore` already enables this and registers scan lifecycle participation. Plain `createSyncStore` does neither automatically without the appropriate configuration/registration.

## Adding a Module

- Add the `PageId` literal in `types/core.ts` (re-exported through `types/index.ts`) and the `ModuleDefinition` in `utils/moduleRegistry.ts`.
- Add a `Lifecycle` field to `ParsedData` and declare it in the module's `lifecycle: { fields: [...] }`. New code uses `queued`, `running`, `done`, and `error`; `LoadingProgressState` is legacy. Completion follows lifecycle phases, not a percentage alone.
- Preserve the distinction between core readiness (`SHARED_LOADING_FIELDS`) and full-session scan completion (`FULL_SCAN_LOADING_FIELDS`). Declare background work and automatic scan policies through the existing registry instead of maintaining separate completion lists.
- When adding a `ModuleAvailabilityPolicy`, implement its case in `utils/pageAvailability.ts`; preserve the exhaustive `never` check.
- See `docs/ui-ux-contracts.md` for the full contract.

## Multi-Instance Rules

- Route frontend HTTP calls through `utils/api.ts`, which injects `X-DSS-Host-Id`. Direct `fetch(` calls outside that module are banned by the contract checker.
- Request handlers use `g.client`. Helpers outside request context use `_active_dss_client()` or `_thread_client()` from `adk_backend.clients`.
- Propagate the selected host into background work: use the host-aware `ThreadPoolExecutor` from `adk_backend.clients`, or explicitly carry the intended client/host context. Calling a client helper in an arbitrary thread does not establish the intended host.
- Operations on a managed DSS host's filesystem, shell/subprocess, `/proc`, or `<DIP_HOME>` must run through a `python-runnables/<name>/` macro on that host. Reuse `adk_backend.macros` helpers or resolve the target project with `_resolve_macro_project(client)` before invoking the macro. Pure DSS API operations stay on the client. This rule concerns managed-host operations, not local repository development commands.
- The macro invocation project is `ADMINTOOLKIT`. Reuse the `409 macro-project-missing` → `HostSelector` modal → `POST /api/hosts/macro-project` bootstrap flow.
- Return SSE responses through `adk_backend.utils._sse_response(generate)`, which supplies Flask request context, streaming headers, and the background budget. Generators accessing `g.client` must retain request context.

## Verification

- Run frontend typecheck/build after UI changes and `pytest tests/backend` after Python changes.
- Run `npm run check:contracts` when changing navigation, progress, or trends contracts.
- Before every deployment, run `npm run typecheck` and `npm run check:contracts` from `resource/frontend/`.

## Build and Deploy Commands

- Run frontend npm commands from `resource/frontend/`; run Make targets from the repository root.
- Use `make build-frontend` for the packaged DSS frontend. It runs `MODE=production npm run build`, which sets the DSS plugin asset paths. Plain `npm run build` uses the default asset base.
- Use a Node version supported by the installed Vite/Rolldown engines: currently `^20.19.0 || >=22.12.0`. Check dependency engine requirements when upgrading.
- Deploy with `make deploy COMMIT_MSG="single-line message"`. `COMMIT_MSG` must be one line because the Makefile embeds it in the commit command.
- The API deployment target reads `.dss-url` and `.dss-api-key` from the repository or home directory unless overridden. The TAMGLOBAL target uses its configured secure wrappers.
- `make plugin` builds the plugin ZIP without deploying; its packaged frontend must be committed first.

## Workflow

- After completing and verifying a webapp/plugin change, run `make deploy`. Tooling-only changes (scripts, Makefile, documentation, or agent instructions) get a plain git commit, never a deploy.
- `TAMGLOBAL` is the internal TAM node (`make deploy-tamglobal-secure`). Customer instances are the real production environments. Routine `make deploy` targets DEV and TAMGLOBAL.
- Push to GitHub through `make secure-push` (security-gated), never plain `git push`.

### Oversized Security Reviews

- On a review size failure, immediately investigate the largest file contributions using `scripts/secure-push.sh --inspect`. Start with the hypothesis that reports, duplicate exports, generated output, or vendor data dominate; verify it against the files before acting. Do not just retry, raise the cap, or send truncated input to reviewers.
- Reference reports (including the Headless assessments) do not need LLM content review. Put future non-runtime report output in `docs/reports/`, or add exact legacy report paths to `scripts/security-review-report-excludes.txt`. All changed files retain the local secret pre-scan.
- Exclude by purpose and path, not blanket extensions or all documentation. Application source, report generators, runtime configuration, dependency manifests/lockfiles, agent instructions, and skills remain in review. Do not move these into the reports directory to reduce size.
- Rerun `--inspect` after correcting scope, then `make secure-push`. If actual source still exceeds the budget, split it into complete bounded reviews. Explain the measured cause and correction to the user.

## Key Paths

- Frontend: `resource/frontend/src/`.
- Backend: `python-lib/adk_backend/`; `webapps/admin-toolkit/backend.py` is the Flask entry shim.
- Plugin manifest: `plugin.json`.
