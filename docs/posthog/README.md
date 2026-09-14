# Admin Toolkit product analytics

Project: [Admin Toolkit in PostHog US](https://us.posthog.com/project/609487).

Usage reporting is **enabled by default**. An administrator can opt out in
**Toolkit Settings → Product Usage Analytics**, or in **DSS plugin settings →
Share product usage analytics**. The Toolkit control uses the existing advanced
action unlock. The DSS plugin setting remains available without that unlock.
The preference is saved in local plugin settings and survives backend restarts
and plugin upgrades. It covers everyone using the serving Toolkit installation,
including sessions inspecting remote hosts. Other installations have their own
preference. The Toolkit control clears pending events immediately; external plugin
setting changes are picked up within ten seconds on the next request/delivery.
Already in-flight requests may finish; previously submitted events are not deleted.

## Collection and identity

The frontend sends small, explicit event batches through `utils/api.ts` to
`/api/usage/events`. The backend validates a fixed event/property vocabulary,
adds the plugin version and audience, and sends via a bounded background queue
to `https://us.i.posthog.com/batch/`. There is no SDK autocapture, replay, error
capture, page-URL collection, or tracking of raw DOM clicks. Names, emails,
customer object identifiers, query/filter text, payloads, logs, cookies, and
browser headers are never forwarded to PostHog. GeoIP enrichment is disabled.
The bundled project token is public and capture-only; it cannot read analytics
or manage the account. Analytics failures never block the app and events are
not persisted to disk or retried indefinitely.

- **User:** HMAC of the DSS-authenticated browsing user with an installation-local
  random secret. The backend resolves DSS identity from the browser session,
  rather than using the service account running the webapp.
- **Fallback:** if DSS cannot resolve that identity, HMAC of a random browser ID
  retained in localStorage. These events have `identity_type=browser`, rather
  than `dss_user`. Clearing storage changes this fallback identity; multiple
  browsers are different fallback users. No shared anonymous-user bucket.
- **Installation:** pseudonym derived from the secret and canonical DSS URL.
  The secret is generated in plugin settings and never transmitted. Rotating
  the secret or cloning installations requires care: rotate the clone's
  `usage_analytics_secret`; existing histories are not merged automatically.
  Changing the canonical URL changes the installation identifier.
- **Audience:** canonical `studioExternalUrl` (or API host if unset), with an
  exact first DNS label of `tam-global`, `tamglobal`, or `akaos`, is `internal`.
  Every other installation is `customer`. A customer URL containing one of
  those words elsewhere is not an internal match. The selected managed host
  cannot change audience: internal TAM inspection stays internal.
- **Target:** a separate pseudonymous `target_host_id` identifies the selected
  host within that installation; remote host names and URLs are not sent.

Installation settings must be readable for collection; failures disable sending.
The backend needs outbound HTTPS to the US ingestion endpoint. Instances with
opt-out, blocked networking, old plugin versions, or no active browser session
are absent from the metrics. Installation counts are not customer-company counts.

## Event contract

All events include `schema_version=1`, `plugin_version`, `audience`,
`installation_id`, `identity_type`, `target_host_id`, a pseudonymous distinct ID,
a random page-load session ID, and an event UUID. Browser batches are bounded;
UUIDs are preserved for ingestion deduplication. The backend also suppresses
recent duplicate event UUIDs per pseudonymous user.

| Event | Meaning |
|---|---|
| `adtk_webapp_opened` | One successful live webapp boot per document, including opens before a host is selected. |
| `adtk_module_opened` | A module is mounted for viewing. Repeated renders do not count; navigating away and back produces a new `visit_id`. Module IDs come from the module registry. |
| `adtk_activity` | Emitted alongside each webapp open and module visit. This is the active-user and retention event, matching the requested definition of meaningful usage. |
| `adtk_scan_started` | An underlying lifecycle run starts. `scan_key` is the shared lifecycle field, `scan_id` identifies the run, and `trigger` describes the entry point. |
| `adtk_scan_completed` | The run reaches lifecycle `done`, with `duration_ms` and `is_empty`. Reaching 100% alone is not completion. |
| `adtk_scan_failed` | Lifecycle `error`, without error text or payload. |
| `adtk_scan_cancelled` | An explicitly paused/aborted scan is observed. A closed tab or host switch can leave a started scan without a terminal event. |
| `adtk_results_viewed` | A visible, mounted module has at least one completed data source for 300ms. Once per visit, with `results_state=partial/complete`. This means results were displayed, not proof the user read them. |
| `adtk_scan_results_viewed` | The displayed module includes results from a completed scan. Carries both `scan_id` and `visit_id`; shared scans are not counted as separate scans per page. |
| `adtk_snapshot_created` | The existing diagnostic bundle export is built and handed to the browser download (`format=diagnostic_bundle`). Does not prove a file was saved to disk or archived. |
| `adtk_comparison_completed` | The comparison engine produced its result successfully. No comparison input or output content is sent. |

Scan triggers are `automatic` for loader/background work, `manual` for explicit
forced refreshes, `on_demand` for shared stores requested by a page, and `unknown`
for lifecycles without an instrumented trigger. On-demand does not necessarily
mean a scan button was clicked. Fast completions first observed at a terminal
state receive an inferred start with `start_observed=false`; filter for `true`
when analyzing observed start-to-finish journeys. Per-scan timings use lifecycle
timestamps. Derived readiness fields are lifecycle sources, not necessarily a
distinct network request. No background lifecycle event contributes to retention.

## Dashboards

[dashboard-definitions.json](dashboard-definitions.json) contains two dashboards
with ten insights each, all explicitly filtered by audience. The customer
dashboard is pinned by default:

1. Weekly distinct active users.
2. Module adoption by distinct visitors.
3. Active users by plugin version.
4. Scan starts, completions, failures, and cancellations.
5. P95 successful scan duration by scan source.
6. Diagnostic exports and completed comparisons.
7. Module opened → results viewed, aggregated per `visit_id`.
8. Scan started → completed → results viewed, aggregated per `scan_id`.
9. Weekly first-ever observed activity retention.
10. Monthly first-ever observed activity retention.

Separate funnels are deliberate: background scans can start before a module is
opened, and ready-on-open pages need no new scan. Combining these into a strict
four-step funnel would falsely label useful visits as failures. The scan funnel
includes background work, where inspecting every result is not expected; filter
by `trigger` when investigating deliberate scan workflows. Funnel windows are
one hour. Change this for unusually long scans. Export/compare events are tracked
separately and do not redefine the agreed open/navigation retention metric.

Retention uses calendar weeks/months, first-ever observed activity, and exact
return periods rather than cumulative retention. Collection begins with this
release; there is no historical backfill. Do not treat incomplete recent periods
as zero retention or infer original installation dates from first observed usage.

## Provision or update

Generate definitions without account access:

```bash
python3 scripts/setup_posthog_analytics.py
```

Create/update the dashboards using a PostHog personal API key with dashboard and
insight read/write scopes, restricted to this project where possible:

```bash
python3 scripts/setup_posthog_analytics.py --apply
```

The script prompts without echoing or saving the key, or accepts
`POSTHOG_PERSONAL_API_KEY` from the environment. It updates only matching names
with its own `admin-toolkit-product-analytics-v1` tag, preserves other dashboard
memberships, and leaves unrelated reports alone. The public `phc_` token cannot
perform this step. The generated definitions are prepared, not evidence that
the dashboards already exist in the account.

References: [capture API](https://posthog.com/docs/api/capture),
[insights API](https://posthog.com/docs/api/insights),
[dashboards API](https://posthog.com/docs/api/dashboards),
[retention](https://posthog.com/docs/product-analytics/retention).
