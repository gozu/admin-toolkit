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

| Live dashboard | Customers | Internal |
|---|---|---|
| All-hands overview | [Open](https://us.posthog.com/project/609487/dashboard/2096875) | [Open](https://us.posthog.com/project/609487/dashboard/2096879) |
| Adoption | [Open](https://us.posthog.com/project/609487/dashboard/2096877) | [Open](https://us.posthog.com/project/609487/dashboard/2096880) |
| Reliability | [Open](https://us.posthog.com/project/609487/dashboard/2096878) | [Open](https://us.posthog.com/project/609487/dashboard/2096882) |

[dashboard-definitions.json](dashboard-definitions.json) prepares **six dashboards**:
three views for customers and the same three for internal installations. They
share 34 saved insights across 38 placements, rather than creating duplicate
charts. Every query carries an explicit audience filter. Only the customer
overview is pinned. Internal usage must not enter the all-hands customer totals.

| Dashboard | Use it for | Charts, in reading order |
|---|---|---|
| **Admin Toolkit — Customers** | Monthly all-hands: reach and repeat use | Active installations over 30 days with previous-period comparison; active users over 30 days with comparison; monthly active installations; weekly active users; module adoption; monthly retention; exports and comparisons |
| **Admin Toolkit — Customers — Adoption** | Choosing what to improve or explain better | Module adoption; observed plugin versions by installation; module opened → results viewed; weekly retention; monthly retention |
| **Admin Toolkit — Customers — Reliability** | Finding scan friction and investigating releases | Scan outcomes; observed scan completion; scan started → completed → results viewed; p95 successful duration by scan source; completed scan sample counts; failures by plugin version; scan trigger mix |

The internal dashboards have the same names with **Internal** instead of
**Customers**, and the same definitions. Compare patterns rather than expecting
internal testing volumes to look like customer behavior.

### How to use these as a product team

- **At the all-hands:** report active customer installations and users, the most
  visited modules, and repeat usage once cohorts mature. State the date window
  and reporting coverage. The overview defaults to rolling 30 days; for a
  calendar-month report select the completed month and label it accordingly.
  Retention has its own longer cohort window; do not shorten it to a single month.
- **Weekly product review:** look at module adoption and the per-module results
  funnel together. A widely used module with weak results visibility is a
  candidate for investigation. Low module traffic alone does not prove low value
  in an occasional-use admin tool. Read module rows separately: Settings/help
  pages have no scan results to convert to.
- **Weekly engineering review:** use the scan completion funnel to find sources
  worth investigating, then inspect latency, sample counts, trigger and version.
  Compare the same sources/triggers across versions. Pick a concrete issue to
  investigate rather than treating every movement as a regression.

### Interpretation rules

- Active installations are distinct `installation_id` values on `adtk_activity`.
  This is observed installation reach, **not customer companies, installed base,
  seats, or rollout coverage**. One customer can run multiple installations.
  No Group Analytics configuration is needed for this distinct-property count.
- Active users and retention use only app opens/module navigation. Background
  scans cannot inflate them. Users are pseudonymous and installation-scoped;
  browser fallback can split one person across browsers. Do not sum weekly
  unique users or module rows to obtain monthly unique users.
- The plugin-version table measures versions seen during the window. An
  installation that upgrades appears under both versions; it is not a current
  inventory, and its rows cannot be summed as unique installations.
- Separate funnels are deliberate: background scans can start before a module
  opens, and cached pages need no new scan. Module funnels match `visit_id`;
  scan funnels match `scan_id`. Scan funnels require an actually observed start,
  excluding starts inferred after a scan had finished. Their conversion window
  is one hour. A closed tab, host switch, long-running scan or lost event can
  leave an incomplete funnel; that is not automatically a product error.
- `trigger=automatic` covers background work. `manual` means an explicit forced
  refresh; `on_demand` means a page requested data, not necessarily a button
  click. Inspecting every background scan result is not expected.
- Scan outcome charts show event counts. Do not divide completions by starts
  from a time bucket and call that a success rate; events cross time boundaries.
  Use the matched scan funnel. Failure-by-version counts also need exposure
  context before being called a release regression.
- P95 duration covers successful runs only and is in milliseconds. Use the
  adjacent successful-run sample counts; a percentile from a handful of runs is
  unstable. A scan source can serve multiple modules. Failing/stalled runs do
  not appear in successful-duration percentiles.
- Retention uses first-ever **observed** activity, calendar weeks/months and exact
  return periods. Collection starts at rollout, with no historical backfill.
  Inspect cohort sizes and exclude incomplete periods; they are not zero
  retention. This is user retention, not company retention.
- Analytics opt-outs, unavailable networking and older versions are absent.
  Empty charts at rollout are expected. We cannot infer satisfaction, time
  saved, company churn or non-reporting installations from these events.

No automated alerts or email/Slack subscriptions are created. First establish a
baseline with representative customer usage, then choose thresholds and recipients.

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
`POSTHOG_PERSONAL_API_KEY` from the environment or `~/.posthog-api.key`
(the saved key location; `~/.posthog-api-key` is also accepted for compatibility). It updates only matching names
with its own `admin-toolkit-product-analytics-v1` tag, preserves other dashboard
memberships, shares the same saved insight across dashboards, and leaves unrelated reports alone. Re-running is safe; same-named user-created charts without the management tag are not overwritten. The public `phc_` token cannot
perform this step. The dashboards were published to project 609487 on 2026-09-14.
The personal API key stays outside the repository and is not bundled with the plugin.

References: [capture API](https://posthog.com/docs/api/capture),
[insights API](https://posthog.com/docs/api/insights),
[dashboards API](https://posthog.com/docs/api/dashboards),
[retention](https://posthog.com/docs/product-analytics/retention).

Dashboard definitions were checked against the official PostHog query schema.
On 2026-09-14, all 34 saved insight queries executed successfully against the live project;
all six dashboard memberships (38 placements) and customer/internal query filters were verified.
At verification, recorded product events were internal only. Customer dashboards are ready
to populate as customer installations report activity. Browser rendering was not separately inspected.

Design references: [trends aggregations](https://posthog.com/docs/product-analytics/trends/aggregations),
[funnels](https://posthog.com/docs/product-analytics/funnels), and
[retention](https://posthog.com/docs/product-analytics/retention).
