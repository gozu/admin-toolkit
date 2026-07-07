# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.668] - 2026-07-07

### Fixed
- `project-export` (and every project-row lookup in the new domains) matches the backend's `/api/projects` rows by `key` — the planner looked for `projectKey` and refused every real project (akaos live catch).
- `config_inspect` domain=api-keys and the api-key-delete impl enumerate ALL users' personal keys via `list_all_personal_api_keys` — the plain variant returns only the caller's keys, which is empty for a global-key backend identity (akaos live catch).

## [0.4.667] - 2026-07-07

### Added
- **Actuator catalog grows from 18 to 43 actions** (runtime + lifecycle long tail, all B-api on the per-host client — fleet-routable). Clusters: `cluster-stop` (managed-only; `terminate=true` named IRREVERSIBLE in the plan), `cluster-start`, `cluster-pods-cleanup` (finished pods/jobs only). Lifecycle: `code-env-update`, `plugin-update` (pre-update zip backup), `plugin-code-env-rebuild`. Projects: `project-export` (⨯N), `project-set-cluster` (fixes WARN_CLUSTERS_NONE_SELECTED_PROJECT), `project-change-owner`, `project-variables-set` — all drift-guarded with restorable history. Runtime: `job-kill` (⨯N), `scenario-disable`/`scenario-enable` (⨯N, drift-guarded toggle, each the other's revert), `scenario-kill`, `scenario-run`, `continuous-activity-stop`, `webapp-backend-stop`/`webapp-backend-restart` (⨯N), `notebook-kernels-shutdown` (DSS-level, files untouched), `notebook-clear-outputs` (⨯N). Security: `user-disable` (⨯N, red, never deletes, refuses the toolkit's own identity)/`user-enable`, `api-key-delete` (red, IRREVERSIBLE, refuses the caller's own personal key). Plus `connection-index` and `variables-set` (GLOBAL variables; secret paths AND `admin_toolkit_finding_whitelist` blocked — agents never edit their own suppression list).
- **6 new `config_inspect` domains**: `users`, `api-keys` (secrets never shown), and per-project `scenarios`, `webapps`, `notebooks`, `jobs` (`name_filter` = project key), backed by new open inventory GETs on the backend (`/api/tools/admin-actions/inventory`, `…/project-setting`, `…/global-variable`).
- Remediation map: long-running-kernel sanity warnings → `notebook-kernels-shutdown`, WARN_CLUSTERS_NONE_SELECTED_PROJECT → `project-set-cluster`, scenario failure storms → `scenario-disable`, departed users → `user-disable`. ACTION_SAFETY rubric names the irreversible pair (api-key-delete, cluster-stop terminate) and the reversible-by-design account hygiene doctrine.

### Fixed
- `cluster-detach` (and the new cluster actions) can now plan against **unavailable** clusters — the Phase-1 lookup searched only the available list, refusing exactly the stale attachments the action exists for.

## [0.4.666] - 2026-07-07

### Fixed
- `config.resolve()` (the kernel-side settings whitelist) now carries `agents_audit_postgres_connection` and the legacy `story_postgres_connection` through to agent/tool kernels — without them the 0.4.665 audit fallback chain had nothing to resolve (its input was pre-filtered; akaos live catch #2).

## [0.4.665] - 2026-07-07

### Fixed
- **Actuator audit rows now resolve the audit DB through the full fallback chain** (`agents_audit_postgres_connection` → legacy `story_postgres_connection` → `triage_connection`) via the new `audit.resolve_connection()`, matching the backend's read side (`db_adapter`). The actuator previously passed `triage_connection` only, so instances configured through the dedicated audit param (akaos) silently skipped every audit row and settings-history write — executes still ran but carried `auditWarning` (live-acceptance catch).

## [0.4.664] - 2026-07-07

### Fixed
- `connection-test` no longer reports a *failing connection* as a *failed action*: the backend impl mapped `connectionOK: false` to `ok: false`, so the route returned 409 and the executor said "backend-error" for exactly the population the probe exists to check (live-acceptance catch). A completed test now returns `ok: true` with `connectionOK` in the result; only exceptions (unknown connection, API failure) fail the action.

## [0.4.663] - 2026-07-07

### Fixed
- **Enriched findings actually reach the agents** (live-acceptance follow-up to 0.4.662). `instance_health`'s score relay was still shaping issues with the old 5-key pick, silently dropping the new `id`/`items`/`details` enrichment and the suppressed-findings count; it now uses the same `health.ISSUE_PICK_KEYS` as the triage sweep (single-sourced) and forwards `whitelistSuppressed`. The sweep rows relay `whitelistSuppressed` too, so digests can state "N findings suppressed by admin whitelist" instead of guessing.
- `config_inspect` domain=clusters now names the unavailable clusters (`id`/`state`/`type`) instead of only counting them — these stale attachments are exactly the `cluster-detach` candidates.
- Actuator megaprompt in the prompt library no longer says "skipping anything whitelist-suppressed" (suppression happens upstream; the instruction only taught the model to hedge live findings).

## [0.4.662] - 2026-07-07

### Added
- **Actuator catalog grows from 12 to 18 actions** (first tranche of the actionable-agents expansion). New package `atk_agent_common/actions/` (per-domain SPECS merged into a registry that also *generates* the target-shape prose quoted by all three tool-description sites — catalog and docs can no longer drift). New actions: `connection-test` (green read-only probe), `connection-update` (dot-path into the connection definition, e.g. `params.host`; secret-material paths blocked, drift-guarded, restorable history), `connection-delete` (definition-JSON backup first, usage warning), `cluster-detach` (definition backup, removes the DSS attachment only; k8s_health now marks DNS-dead clusters `suggestedAction: cluster-detach`), `plugin-uninstall` (zip backup, refused while any usage exists, never the toolkit itself), `project-clear-webapp-runs` (new fs-cleanup macro + `fs_paths` policy: only `run_*` dirs under `webappruns/`, keep-newest-N per webapp, running-backend exclusion fetched inside the macro). All new pure-DSS-API actions run as backend red routes on the per-host client (`/api/tools/admin-actions/*`) — fleet-routable by construction.
- **Batched targets**: batchable actions (`connection-test`, `connection-delete`, `plugin-uninstall`, `project-clear-webapp-runs`, `code-env-delete`, `settings-set`) accept `targets: [dict, ...]` (max 20) — ONE plan, ONE confirm token over a deterministically-sorted combined canonical, per-target execution with continue-on-error (`ok/partial/error`), ONE audit row. `propose_action_items` items carry `targets`/`targetCount` (six dead code envs = one checklist item, not six); the UI shows a ×N chip, a per-target plan list, and per-target execution results.
- New `config_inspect` domain `clusters` (id/name/state; `detail='health'` adds the reachability sweep); `storage_footprint` sizeBreakdown rows carry `bucketKey` (e.g. `webApps`) so webapp-run findings map mechanically; disabled-feature findings carry `details[].settingsPath` + `proposedValue` (exact `settings-set` targets, impersonation marked `sensitive`); broken-connection findings carry the failing `items` names.
- Remediation map: broken connections → `connection-update`/`connection-test`/`connection-delete`, DNS-dead clusters → `cluster-detach`, deprecated/unused plugins → `plugin-uninstall`, unreferenced deprecated envs → batched `code-env-delete`, oversized projects with webapp-run bloat → `project-clear-webapp-runs`, feature flags → batched `settings-set` citing `details[].settingsPath`.

### Fixed
- **Whitelist semantics are now suppress-entirely, everywhere.** The triage sweep no longer forwards `whitelistRule`/`whitelistItems` annotations on LIVE findings to the model (whitelisted findings were already suppressed upstream — the keys only taught the model to hedge), and every prompt/doc that said "never resurface a whitelisted item" now says the truth: nothing an agent sees is whitelisted — propose without hedging, relay suppressed counts only. The flip side, by design: a whitelisted finding produces NO action item at all until the admin removes the whitelist entry (e.g. a whitelisted exec-config-resources finding means no `k8s-exec-config-tune` item appears).

## [0.4.660] - 2026-07-07

### Fixed
- **Legacy secret params survive the upgrade install.** 0.4.659 removed the `red_actions_secret` / `red_actions_password` / `host_keys_password` declarations, and DSS prunes undeclared config keys during the plugin update itself — before the fallback/migration code could ever read them (observed live on both instances). The three legacy params are now kept declared but hidden (`visibilityCondition: "false"`), so upgrading from any pre-0.4.659 version preserves the values and the first backend read migrates them into `master_password`. Installs that already upgraded through 0.4.659 must re-enter the master password once in plugin settings.

## [0.4.659] - 2026-07-07

### Changed
- **One master password** replaces the three overlapping secrets. The new `master_password` plugin setting (a normal PASSWORD field — typed once, no more hash generation) now covers everything: the browser Advanced Actions unlock (verified and token-signed server-side, PBKDF2-strengthened signing key), remote-host API-key encryption (keys typed into Settings → Remote Hosts encrypt server-side with zero prompts), and the headless agents (triage sweep, actuator confirm tokens, host-key unlock). The `red_actions_secret` hash + `hash.html` generator roundtrip and the duplicate `red_actions_password` / `host_keys_password` agent params are gone.
- **Encrypted host keys now auto-unlock server-side**: the backend derives the Fernet key from the master password on demand, so the host-keys unlock modal only appears on legacy installs (hash-only config, or blobs encrypted under a different password). A successful Advanced Actions unlock also opens the host-key gate in the same response.
- **Upgrade is automatic**: a pre-0.4.659 config keeps working untouched — the backend falls back to `red_actions_password` / `host_keys_password` and migrates the value into `master_password` on first use (so it survives DSS pruning undeclared config keys on the next settings save); a legacy `red_actions_secret` PBKDF2 hash still verifies browser unlocks (and existing unlock cookies stay valid) until a master password is set.

### Removed
- `resource/hash.html` (offline secret generator / host-key encryptor). Blobs it produced (`adkfk1$…`) remain fully supported — KDF params, salt tag and framing are unchanged and now locked by tests.

## [0.4.658] - 2026-07-07

### Changed
- **Agents now live in the `ADMINTOOLKIT` project** — the separate `AGENTOPS` project is gone, completing the agents-plugin absorption at the project level. `AGENTS_PROJECT_KEY`, Trace Explorer provisioning, `provision_prod.py` / `interaction_logging.py` defaults, the Agents-page empty state, and the docs all point at `ADMINTOOLKIT` (the toolkit's macro project, guaranteed to exist on every configured host — so a fresh install no longer surfaces `UnauthorizedException: Failed to read project permissions` from probing a nonexistent `AGENTOPS`). Existing installs: re-run `scripts/agents/provision_prod.py` against the host (idempotent) to recreate the tool/agent instances + interaction logging in `ADMINTOOLKIT`; an old `AGENTOPS` project can then be deleted.
- `provision_prod.py` no longer hardcodes instance specifics: the plugin `backend_url` is **auto-discovered** via the DSS API (finds the deployed `webapp_admin-toolkit_admin-toolkit` webapp → `…/web-apps-backends/<project>/<id>`; `--backend-url` still overrides), and the managed code env is resolved from the existing family (`plugin_admin-toolkit_managed[_N]`, preferring what plugin settings already point at) instead of assuming the base name — a fresh install with a DSS-auto-renamed `_1` env no longer gets its `codeEnvName` clobbered or a duplicate env created.

## [0.4.657] - 2026-07-07

### Removed
- **`adoption_metrics` agent tool** — the last remaining piece of the Adoption feature (the UI subpage was removed in 0.4.638-640). Gone full-stack: the `python-agent-tools/adoption-metrics/` plugin tool, `tools_impl.adoption_metrics`, the `GET /api/adoption` route (`routes/adoption.py`), `_adoption_git_aggregate` and its now-orphaned git-log helpers (`_git_commit_month`, `_fill_month_range`, `_classify_git_author`, `_continue_git_log`, `_fetch_all_git_logs`) in `clients.py`, the Scoping Architect's tool binding, the adoption golden question (set is now 9), and the scripts/docs references (`test_tools.py`, `verify_endpoints.py`, `golden_check.py`, `agents-reference.md`). The severity-rubric line excluding adoption/QBR metrics from digests stays — that's editorial policy, not a tool reference.

## [0.4.655] - 2026-07-06

### Added
- **Agent Tuning subpage** (AGENTS → Tuning): edit every agent prompt — the three specialist system prompts plus the severity and action-safety rubrics — with the built-in defaults as the baseline. Every save appends one row to a managed Dataiku dataset `agent_prompt_versions` in the toolkit's project (one **column per prompt type**, one **row per save**, with `saved_at`/`author`/`note` metadata; a cell equal to the default is stored empty). The newest row is the active version; "Load" pulls any older version into the editors and saving it again restores it — history is immutable. New routes: `GET /api/agents/tuning` (state), `POST /api/agents/tuning/save` (advanced-gated), `GET /api/agents/tuning/prompts` (the agents' runtime read). Placeholders (`{severity_rubric}`, `{allowed_actions}`, …) are documented per editor and substituted at turn time, so overrides compose with live rubrics/catalogs.
- Agents fetch tuned prompts at turn start via `atk_agent_common/prompt_overrides.py` (60s kernel cache, hard fallback to built-ins on any failure) — prompt changes apply within ~1 minute, no restart. Default templates extracted to `atk_agent_common/prompts.py` (single source for agents, backend and the tuning UI).

### Changed
- **The Agents page now presents ONE agent** ("Admin Toolkit Agent"): the specialist picker is gone; the three provisioned agents keep running behind the curtain. Prompt-library prompts route by their group (Health & Triage / Scoping & Architecture / Admin Actions → the matching specialist), free-form messages continue the visible thread (fresh chats start on the triage generalist), and action-item/restore handoffs still route to the actuator internally. Specialist names no longer surface anywhere ("Plan N selected actions" replaces "Send N to Ops Actuator").
- Prompt library reorganized into the two headline groups (Triage, Scoping) plus Admin Actions, each with its megaprompt hero; the empty state now shows ~7 sample prompts per group plus a browse-all count instead of 3 shortcuts.
- Chat history is always reachable: the History drawer lists server-persisted conversations when chat storage is enabled and falls back to the browser-cached ones when it is not (reload + resume works either way); rows no longer leak per-specialist agent names.

## [0.4.648] - 2026-07-06

### Added
- **Server-side chat persistence** (opt-in, Agent Hub storage model): new `chat_storage` plugin setting — Off (browser-only, the default) / Built-in SQLite (webapp workload folder) / Remote SQL (PostgreSQL or SQL Server DSS connection via `chat_db_connection`, `chat_tables_prefix`). Every settled agent turn is auto-persisted server-side (`python-lib/adk_backend/chat/`: Flask-SQLAlchemy models as a bare declarative base, JSON-as-TEXT segments, zlib-compressed per-message dku-traces, idempotent `create_all` — no alembic), scoped per user (best-effort DSS browser-header identity, anonymous fallback) and per fleet host (`host_id` column) — **conversations now survive hard refreshes, backend restarts, and host switches**. New `/api/chat/*` routes (config/list/get/create/rename/soft-delete/turn/message-trace) and a compact History slide-over drawer on the Agents page (reopen/rename/delete past conversations). localStorage stays a cache only (`STORAGE_VERSION` 2).
- **One-click agent traces**: the Trace Explorer plugin webapp is now **auto-provisioned** — `POST /api/agents/trace-explorer/provision` (advanced-gated; "Set up Trace Explorer" header CTA) creates the `traces-explorer` plugin webapp in AGENTOPS via raw REST (`create_webapp()` rejects plugin types), points it at the `agent_interaction_logs` dataset's `trace` column, and starts its backend (steps trail in the UI). On the local hub, the per-turn "copy trace" chip becomes **"open trace ↗"**: the turn's trace is handed to Trace Explorer via its native `ls.llm.traceExplorer.trace` localStorage flow (`?readTraceFromLS=true`) and opens directly on the trace; persisted message traces serve as the durable fallback once the in-memory ring rotates. Remote hosts keep the copy-chip (localStorage is per-origin). New `GET /api/agents/trace-explorer/status` moves discovery off the per-turn hot path.
- Plugin code env: added `Flask-SQLAlchemy==3.1.1`, `SQLAlchemy>=2.0.16`, `pymssql==2.3.13`.

### Changed
- `scripts/agents/interaction_logging.py` is now a thin wrapper over `python-lib/adk_backend/trace_explorer.py` (shared by the webapp backend); `--webapp` flag provisions the Trace Explorer webapp from the CLI. The "manual webapp step" epilogue now points at the automated paths.
- Agents chat store rekeyed from one-conversation-per-agent to conversation ids (`activeConvIdByAgent` tracks the visible one per agent); "New conversation" starts a fresh thread instead of erasing history when persistence is on.

## [0.4.646] - 2026-07-06

### Added
- **Gated remediation suite** — five new Ops Actuator actions, every gate enforced *below the model* in shared policy engines (`atk_agent_common/policies/`, imported by both planners and the privileged macros; 162 unit tests):
  - `log-cleanup`: deletes ROTATED logs only (`*.log.<n>`, compressed, dated rotations — a live `*.log` can never match), min-age (default 3d) + whitelisted DIP_HOME roots + size cap enforced inside the new `log-cleaner` macro; two-pass delete re-validates every file at unlink time.
  - `docker-prune`: builder/image cache pruning with FIXED argv (no shell, no `--all`, docker group only, no sudo) via the new `docker-governor` macro; detects docker storage sharing the DSS data filesystem; daemon.json cache limits are emitted as a display-only idempotent sudo script, never executed.
  - `k8s-apply-fix`: policy-validated kubectl mutations (verb/kind whitelists, secrets + cluster-scoped kinds + `--all`/`--force` forbidden, kube-system restricted) via the new `k8s-apply` macro with read-only previews + server dry-runs; optional exec-config patch and post-fix `verifyRule` re-audit that reports whether the finding still fires.
  - `code-env-consolidate`: repoints all usages of a code env onto a target (dry-run usage table is the plan evidence), optional backup-first retirement of the source.
  - `settings-set`: generic DSS general-settings mutator with a security/auth/licensing + secret-material path blacklist (admin-extendable via `settings_set_blocked_extra`), current→proposed diff at approval, the observed current value HMAC-bound into the confirm token (drift refuses), and restorable settings history.
- **Auto-remediation tier** (`auto_remediate_actions`, default OFF): admins can opt log-cleanup/docker-prune into autonomous execution during the daily triage sweep, under cumulative GB/object caps (`auto_remediate_max_gb`/`auto_remediate_max_objects`); still passes the kill-switch and every policy gate, audits as `triage-auto`, and reports executions *and* skips (with reasons) in the digest.
- Finding→remediation registry (`remediation_map.py`) so Health Triage proposes mapped, ready-to-plan fixes for scored findings; documented gaps stay explicit.
- **Trace Explorer wiring** (DSS ≥ 14.5 Agent Interaction Logging, verified on the 14.7 API): provisioning creates a DAY-partitioned `agent_interaction_logs` dataset and enables FULL-content logging on all 3 agents; the chat `done` event carries `traceAvailable` + a Trace Explorer link, and each turn's trace JSON is copyable via `GET /api/agents/last-trace` (in-memory ring, never streamed over SSE). The Trace Explorer visual webapp itself is a documented one-time manual step (the public API cannot create visual webapps).
- All runnable macros now declare `macroRoles` (visible in DSS project macro menus).
- `scripts/agents/remediation_check.py`: live gate check — plans for every new action, policy refusals, kill-switch refusal with a valid token; `--red-on` safe execute subset.

### Changed
- `finding_whitelist` storage migrated from the plugin-param pruning hack to DSS instance variables (`admin_toolkit_finding_whitelist`), with one-time automatic migration; the plugin param remains only as a legacy fallback.
- Plugin code env: added `pyyaml` (k8s manifest policy validation fails closed without it).

## [0.4.643] - 2026-07-06

### Added
- **Archive Storage** plugin setting (`archive_folder_connection`, dropdown of folder-capable connections): the toolkit find-or-creates an `admin-toolkit-archive` managed folder on it in the active support project. The Projects/Code-env cleaner backup pickers default to that folder, and the toolbar "Export all" zips (JSON + CSV) are additionally stored into it via the new `POST /api/archive/store` (browser download unchanged; no-op when the setting is empty).

### Changed
- `default_llm_id` is now a dropdown (LLM Mesh models, same enumeration as the report LLM picker) instead of a free-text id; the stored value stays selectable even if enumeration misses it.
- `triage_connection` is now a PostgreSQL connection dropdown (was free text), matching the Agents Audit setting.

## [0.4.641] - 2026-07-06

### Changed
- Merged the companion `admin-toolkit-agents` plugin (v0.1.013) into `admin-toolkit`: `python-agents/`, `python-agent-tools/`, `python-lib/atk_agent_common/`, and the `agent-triage-sweep` runnable now ship in this plugin. Component ids are re-derived from the parent plugin id (`agent_admin-toolkit_<c>`, `Custom_agent_tool_admin-toolkit_<c>`, `pyrunnable_admin-toolkit_agent-triage-sweep`); the 17 agents settings params moved onto this plugin's settings page unchanged. Live instances migrate via `scripts/agents/migrate_merge.py` (recreate saved agents/tools, repoint triage scenario, copy settings, decommission the old plugin).
- Plugin code env: added `requests`, `langchain`, `langchain-core`; interpreters narrowed to Python 3.10–3.13 (langchain requirement).

## [1.0.5] - 2026-06-05

### Changed
- Pre-release polish: terminology and copy consistency, progress/color-semantics fixes, accessibility (keyboard operability, ARIA labels), and packaging cleanup.

## [1.0.4] - 2026-02-04

### Fixed
- Memory analysis layout refinements
- Removed unused status badge from Memory Analysis card

## [1.0.3] - 2026-02-04

### Fixed
- Fixed memory calculation formula (CGroup Limit - JEK × Max Activities)

## [1.0.2] - 2026-02-04

### Fixed
- Fixed CGroup memory limit parsing (accepts any key name, not just `memory.limit_in_bytes`)

## [1.0.1] - 2026-02-04

### Changed
- Memory analysis refinements and improvements

## [1.0.0] - 2026-02-04

### Added
- **Memory Analysis Card** (`MemoryAnalysisCard.tsx`)
  - CGroup Limit Check: Validates configured limit against recommended max based on VM size
  - JEK Allocation Check: Verifies JEK × Max Running Activities fits within cgroup limit
  - Color-coded status (green/yellow/red) for memory health
  - Shows "Available for Backend & Misc." calculation

### Changed
- Cluster improvements and debug screenshots

## [0.9.x] - 2026-02-04

### Added
- Memory analysis feature initial implementation
- Cluster table improvements

### Changed
- Matched K8s cluster card heights to minimize empty space
- Better error handling for malformed cluster data
- Improved node pool display

## [0.8.x] - 2026-02-04

### Added
- **Comparative Analysis Mode**
  - Upload and compare two diagnostic files side-by-side
  - Visual delta badges showing increases/decreases between environments
  - Comparison sections:
    - Health scores
    - System information
    - Configuration settings
    - Charts (filesystem, memory, connections)
    - Collections (users, projects, plugins, code envs, clusters)
  - Drag-and-drop upload for "Before" and "After" files

### New Files
- `ComparisonUpload.tsx` - Upload interface for two files
- `ComparisonResultsView.tsx` - Main comparison results display
- `ComparisonHealthSection.tsx` - Health score delta visualization
- `ComparisonSystemSection.tsx` - System metric changes
- `ComparisonSettingsSection.tsx` - Config/settings diffs
- `ComparisonCollectionsSection.tsx` - User/project/cluster/plugin changes
- `ComparisonChartsSection.tsx` - Chart comparisons
- `DeltaBadge.tsx` - Change indicators
- `compareData.ts` - Comparison utility functions

## [0.7.3] - 2026-02-03

### Changed
- K8s cluster card heights optimization
- Deployment documentation updates

## [0.7.0] - 2026-02-03

### Added
- **Landing Page with Navigation** (`LandingPage.tsx`)
  - New landing page with clear navigation options
  - Debug Mode: Single file analysis
  - Compare Mode: Two-file comparison
  - Improved onboarding experience

- **Install.ini Parser** (`InstallIniParser.ts`)
  - New parser for extracting install.ini configuration
  - Extracts node ID, install ID, and instance URL

### Changed
- **Cluster Table** (`ClustersTable.tsx`, `ClustersParser.ts`)
  - Better error handling for malformed cluster data
  - Improved node pool display

- **Connections Parser** (`ConnectionsParser.ts`)
  - Enhanced connection type detection
  - Better handling of connection details
  - Improved connection count parsing

- **Code Environments Table** (`CodeEnvsTable.tsx`, `CodeEnvsParser.ts`)
  - Expanded display with more details
  - Better categorization of code environments

- **Connections Chart** (`ConnectionsChart.tsx`)
  - Improved chart rendering
  - Better color coding for connection types

- **Health Score System** (`useHealthScore.ts`)
  - Added memory over-provisioning detection
  - CGroup limit validation against recommended max
  - JEK allocation check within cgroup
  - More accurate health scoring

- **Issue Detection** (`useIssueDetection.ts`)
  - Added memory over-provisioning detection
  - Improved issue categorization

### Fixed
- Fixed bird logo path for DSS plugin context

### UI/UX Improvements
- Dark theme refinements
- Improved table styling
- Better responsive layout
- Updated color variables

### Infrastructure
- Added plugin structure documentation to README
- Deployment process explanation
- DSS API endpoint examples
- Troubleshooting table for common errors
- Updated `bump_version.py` to sync frontend package.json version
- Added dummy Python backend for SSO settings (`backend.py`)
- Webapp configuration updates (`webapp.json`)
- Added automated screenshot testing (`screenshot-test.ts`)

---

## Pre-Plugin Development (Standalone React App)

The following versions were developed in the standalone React app before conversion to a DSS plugin.

## [0.6.0] - 2026-02-03

### Added
- **Directory Tree Visualization** (`useDirTreeLoader.ts`, `DirTreeSection.tsx`)
  - Async chunked parsing for large `datadir_listing.txt` files
  - Depth-limited tree building with byte-offset indexing
  - Fast drill-down navigation
  - Extraction progress bar for large files

### Changed
- Defer directory tree parsing until after initial UI render
- Stream `datadir_listing.txt` directly from zip (no blob in memory)

### Fixed
- Path normalization in `useDirTreeLoader` - trailing slashes causing parent lookup failures
- Drill-down in streaming mode - re-extract from zip when needed

### Performance
- Optimize dir tree parsing: removed expensive TextEncoder calls, simplified byte tracking
- Added entries/sec metric for parsing performance

## [0.5.0] - 2026-02-03

### Added
- **Light Mode Theme** with proper status colors
- Theme toggle support

### Changed
- Use solid backgrounds for cards/tables (removed backdrop-filter for better light mode support)
- Improved light mode contrast and visibility

### Fixed
- Python version badge logic

## [0.4.0] - 2026-02-03

### Added
- Bird logo branding
- Roboto Condensed font
- Version display in UI
- **Code splitting** for faster initial page load
- Auto-increment version on `make commit`

### Changed
- Add usage percentages to filesystem chart labels

## [0.3.0] - 2026-02-03

### Added
- **Visual redesign**: Enterprise professional styling
- Dark mode theme

### Changed
- Move Plugins/CodeEnvs into 3-column grid layout
- Improve hover contrast on Key Files buttons
- UI layout and label improvements

### Fixed
- Dark mode color issues
- Layout improvements

## [0.2.0] - 2026-02-02

### Added
- **Issue Detection System** with `AlertBanner` component
- **Resizable Modals** using react-rnd for drag/resize

### Changed
- Modal UX improvements: movable, no backdrop close, minimal margins

### Fixed
- Filesystem parsing issues
- Header styling
- Makefile: support multiline commits via file (`mf=`) or editor (`commit-i`)

## [0.1.0] - 2026-02-02

### Added
- **Initial React Migration** of Dataiku Diag Parser
  - Full rewrite from vanilla JS to React + TypeScript + Vite
  - Component-based architecture
  - Type-safe parsing system
  - Modern build tooling

---

## Version History Summary

| Version | Date | Highlights |
|---------|------|------------|
| 0.1.0 | Feb 2, 2026 | Initial React migration |
| 0.2.0 | Feb 2, 2026 | Issue detection, resizable modals |
| 0.3.0 | Feb 3, 2026 | Visual redesign, dark mode |
| 0.4.0 | Feb 3, 2026 | Branding, code splitting |
| 0.5.0 | Feb 3, 2026 | Light mode theme |
| 0.6.0 | Feb 3, 2026 | Directory tree visualization |
| 0.7.0 | Feb 3, 2026 | **DSS Plugin conversion**, landing page |
| 0.7.3 | Feb 3, 2026 | K8s cluster card heights, deployment docs |
| 0.8.x | Feb 4, 2026 | Comparative analysis feature |
| 0.9.x | Feb 4, 2026 | Memory analysis, cluster improvements |
| 1.0.x | Feb 4, 2026 | Memory analysis refinements, layout fixes |
