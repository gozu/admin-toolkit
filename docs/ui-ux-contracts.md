# UI/UX Contracts

Admin Toolkit should feel polished, fast, and dense. The preferred interaction model is compact surfaces with expandable detail, popovers, and panels rather than extra permanent controls.

## Progress Semantics

- Grey: queued, loading, unavailable.
- Yellow: active, partial, waiting, stalled.
- White: ready, current, completed-neutral.
- Red: failure.

New asynchronous module work must use `ProgressIndicator` (in `components/common/`). The component derives its tone from the lifecycle state — there is **no `tone` prop**. Pass a `loading: LoadingProgressState | null` (or `active`/`pct`/`message`/`phase` overrides). Errors are expressed by setting `error` on the loading state, not by manually picking a color. The frontend contract checker will fail the build if a `tone` prop reappears or if any tone-token color class drifts.

## Sidebar Item Status

Sidebar nav rows do **not** encode load state via label color or opacity. Every row renders at full opacity in the default text color regardless of `getPageAvailability(...)`. Per-row load state is surfaced by a single trailing glyph rendered by `SidebarItemStatus` (co-located in `Sidebar.tsx`):

| Availability                         | Glyph                                                        |
| ------------------------------------ | ------------------------------------------------------------ |
| `independent`                        | *(no glyph — module is always available)*                    |
| `loading`, idle (no fetch running)   | `○` empty circle, in tertiary text color                     |
| `loading` with active fetch, or `partial` | spinner (Tailwind `animate-spin`)                       |
| `ready`, just transitioned from non-ready | `✓` in `var(--success)`, fades out over 2.5s             |
| `ready`, steady state                | *(no glyph)*                                                 |

"Active fetch" is `module.loadingField`'s `active` flag, falling back to `parsedData.analysisLoading.active`. Transition detection uses a `useRef` of previous availability per page; the check glyph is suppressed on first paint and on remount so navigating back to a cached page does not flash a spurious `✓`.

This sidebar scheme is **scoped to the sidebar**. The `ProgressIndicator` grey/yellow/white/red tone contract above is unchanged everywhere else (progress bars, scan dots, lifecycle indicators).

## Module Contract

Every page is registered in `resource/frontend/src/utils/moduleRegistry.ts`. The registry is the single source of truth for nav placement, command-palette entries, experimental flags, availability policy, lifecycle participation, trends participation, and the streaming endpoint name.

A `ModuleDefinition` may declare:

- `loadingField?: keyof ParsedData & '${string}Loading'` — the typed pointer to a `LoadingProgressState` field on `ParsedData`. The string template plus `keyof ParsedData` constraint means a typo is a compile error. Modules that opt in this way **automatically participate** in the global `analysisLoading` aggregator: the global indicator stays active until every module with a `loadingField` reaches `progressPct: 100` and `active: false`.
- `trends?: true` — convention: snapshot key equals the module id with `-` replaced by `_`. The Python contract test (`scripts/check_trends_contract.py`) enforces that a matching `TrendSnapshotTable(...)` exists in `python-lib/trends_registry.py`.
- `streamEndpoint?: string` — the SSE endpoint, used by the scan-store factory and referenced by contract checks.

The `ModuleAvailabilityPolicy` enum is **exhaustive**: `pageAvailability.ts` has no `default:` branch and ends in a `never` assertion. Adding a new policy without handling its case is a TypeScript error.

Availability semantics (`useModuleAvailability`): a module is hidden **only on a settled, definitive absence signal** (no clusters registered, no registry provider, no runtime-DB connection, LLM audit settled empty, no container-exec configs). Unknown / loading / errored signals keep the module visible. Hiding applies to the nav surfaces only — Sidebar and ⌘K — never to `PageRouter`, so an open page is never yanked away and deep links keep working. The settled verdict is persisted per host (`admin-toolkit:hiddenModules:<hostId>`) so the next session's sidebar starts correct instead of popping modules out mid-startup; a settled signal always overrides the seed in both directions.

## Adding a New Module — Checklist

1. Add a `PageId` literal in `types/index.ts`.
2. Add a `ModuleDefinition` entry to `MODULES` in `moduleRegistry.ts` and place its id under the right nav section.
3. If it opts into the global lifecycle, add a `LoadingProgressState` field to `ParsedData` and reference it as `loadingField`.
4. If it persists history, set `trends: true` and add the corresponding `TrendSnapshotTable` to `trends_registry.py`.
5. If its availability needs a new policy, add it to `ModuleAvailabilityPolicy` *and* the `switch` in `pageAvailability.ts` (the `never` exhaustiveness will tell you).
6. Run `node scripts/check_frontend_contracts.mjs` and `python3 scripts/check_trends_contract.py` — both must pass.

## Streaming + State

There is exactly one SSE parser, `parseSseStream` in `utils/sseStream.ts`. Anything that consumes a `text/event-stream` response **must** use it. Re-implementing `buffer.split('\n\n')` or `^event:` regex anywhere else fails the frontend contract check.

Module-scoped singleton stores must be built on `state/createSyncStore.ts` (or, for streaming scans, `state/createModuleScanStore.ts`). The primitives provide:

- A clean `get / set / patch / subscribe / use` surface.
- Optional `sessionScoped: true` so the global Refresh button (which bumps the session epoch) automatically clears the store. Streaming scan stores get this for free via the factory; do not rely on stale state surviving a refresh.
- For scan stores: a singleton inflight promise so navigating away and back reattaches instead of restarting, automatic registration with the loading-state mirror so the module participates in the global aggregator without component-level wiring.

## Trends Contract

Modules that should snapshot to the history database declare `trends: true`. A matching `TrendSnapshotTable(<id-with-underscores>, ...)` must exist in `python-lib/trends_registry.py`. Orphan tables (populated by the bulk tracking-ingest endpoint rather than a per-page scan) are tolerated as warnings — do not add new orphans without a clear reason.

## Rendering Performance

Keep dimensions stable, memoize derived rows, avoid expensive render-time work, and animate with opacity/transform where possible. A short preparation delay is acceptable when it prevents visible stutter during interaction.

## Container Execs Navigation Contract

Container Execs scan ownership lives in `state/containerExecsStore.ts` (built on the scan-store factory) — not in the routed page component. Navigating away and back must reattach to the current in-flight or completed scan, not start over. Only explicit Rescan, post-replacement refresh, or backend cache refresh may start a new scan. UI selection state (source, target, dry-run, expanded rows) is per-page `useState` and resets on navigation by design.

## Module Bootstrap Contract

**Hard rule, no exceptions:** a module page must not refire its bootstrap fetches on remount. Once data is loaded in the current session, revisiting the page reattaches to the cached store. The session is bounded by the global Refresh button (`bumpSessionEpoch()` in `state/sessionCache.ts`), which clears every `sessionScoped` store.

Concretely:

- Bootstrap fetches live in `state/<module>Store.ts`, not in the routed page component. The store is built on `createSyncStore({ sessionScoped: true })` (lazy stores) or `createModuleScanStore({ loadingField, fallbackEndpoint })` (eager stores that join the global lifecycle aggregator).
- The page calls `<store>.load()` from a single mount effect — `useEffect(() => { void store.load(); }, [])` for lazy, or `useEffect(() => { if (!scanStarted) void store.load(); }, [scanStarted])` for eager (where `scanStarted` reads from the store via `use()`). The `[scanStarted]` idiom re-fires on session-epoch reset without component remount.
- The page reads data from `<store>.use()` for both data and UI lifecycle fields (loading / error / progress). No local `useState` for fetch results.
- **User-action or user-input-keyed refetches are exempt** (per-connection drilldowns in DB Health, per-provider release date in Image Cleaner, the Rescan button in Container Execs). These fire on user state changes, not on mount.

The frontend contract checker (`scripts/check_frontend_contracts.mjs`) enforces this for every entry in `MODULE_PAGES` — a mount-only `useEffect(..., [])` that references an `/api/` literal not wrapped in a `*.load(` call fails the build.

## Health Score Contract

Health factor toggles are defined exactly once in `useHealthScore.ts` as `HEALTH_FACTOR_CONTROLS`. The `HealthFactorKey` union and `DEFAULT_HEALTH_FACTOR_TOGGLES` are derived from that array. UI components import the canonical list rather than maintaining a parallel one. Toggles persist to localStorage under a versioned key (`health-factors:v<N>`) and reset on the global Refresh button via the session-epoch hook.

## Multi-Instance Host Contract

The toolkit scans either the DSS it is installed on (`'local'`) or any remote DSS configured as a `remote-dss-host` plugin preset. Routing is invisible to module code:

- **All frontend HTTP calls** must go through `utils/api.ts` (`fetchJson` / `fetchText` / `fetchSse`). These inject the `X-DSS-Host-Id` header from `hostStore.getActiveHostId()` on every request. The frontend contract checker fails the build if a new `fetch(` call lands outside `utils/api.ts`.
- **All backend request handlers** read `g.client` (set by `@before_request → _resolve_client`). Background threads, loaders that may run outside a request context, and helpers shared with non-request callers use `_active_dss_client()` (falls back to the local thread-pooled client). The `threading.Thread` target for tracking-ingest uses `_thread_client()` explicitly — it always reports against local.
- **Operations that need filesystem/shell access on the target host** must NOT call `subprocess.run`, read `/proc`, or touch `<DIP_HOME>` directly from the webapp. Instead, add a `python-runnables/<name>/` macro and invoke it via `_resolve_macro_project(g.client).get_macro(...).run(params, wait=True)`. The macro runs as the `dataiku` service account on whichever host the active client points at, and that's the only code path that works for both local and remote.
- **The `ADMINTOOLKIT` project key is the canonical macro-invocation home**. On first macro use against a remote that doesn't have it, the backend responds with `409 {error: 'macro-project-missing', projectKey: 'ADMINTOOLKIT', defaultName: 'Admin Toolkit'}`. The frontend `HostSelector` listens for this event and opens a confirm-create modal that calls `POST /api/hosts/macro-project`.
- **SSE generators that touch `g.client` must run inside a request context.** Every `return Response(generate(), …)` is wrapped with `stream_with_context(generate())`. Adding a new SSE endpoint? Wrap it.
- **Tracking history is per-host.** Every `runs` row stores `dss_host_id` (default `'local'`). Trends queries can filter by host preset name in addition to the natural `instance_id` of the scanned DSS.
