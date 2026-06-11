---
name: live-instance-api-access-pattern
description: "Read-only debug endpoints exposed by the deployed admin-toolkit webapp backend — fetch logs, perf state and a support bundle from any instance"
metadata:
  type: reference
---

## Admin-toolkit webapp backend — live API access

Backend base URL shape: `https://<dss-host>/web-apps-backends/<PROJECT_KEY>/<webappId>`.
The `<PROJECT_KEY>/<webappId>` tail can be extracted from the webapp init page; the
public UI lives at `https://<dss-host>/public-webapps/<PROJECT_KEY>/<webappId>/`.

### Read-only debug/diagnostic endpoints

| Endpoint | Purpose |
|---|---|
| `/api/logs/raw-tail` | Last 100K chars of backend.log as `{text, chars}` |
| `/api/logs/errors` | Parsed log errors with stats |
| `/api/mail-channels` | DSS mail channels + `configuredMailChannel` (used by FS Migration) |
| `/api/project-footprint/progress` | Live progress of project-footprint scan |
| `/api/code-envs/progress` | Live progress of code-envs scan |
| `/api/settings` | Current backend settings |
| `/api/mode` | Live vs diag mode detection |
| `/api/debug/perf` | Cache keys, sdk_cache stats (hits_mem/hits_sql/misses/writes/sql_ms), backend settings, last benchmark data, progress runs, `prewarm` status — no scans triggered |
| `/api/debug/support-bundle` | (v0.4.555+) zip download: bundle.json (DSS version), debug-perf.json, logs/errors.json, logs/backend-tail.log (1M chars vs raw-tail's 100K) — use `curl -o` |

### Mutating endpoints (v0.4.539+) — ask before using on someone else's instance
- `POST /api/cache/clear` — wipes in-memory cache + sdk SQLite cache + bumps session epoch
- `POST /api/settings/update` (JSON body of known int settings) — runtime override of _BACKEND_SETTINGS; add `"persist": true` to ALSO write through to the saved plugin config (survives restarts; backend uses its own local admin client)
- `POST /api/settings/benchmark` (`{"apply": true}` to apply+persist) — real-workload worker-count sweep (8/16/32), ~12s, returns levels + recommendation

### Notes
- Heavy endpoints (`/api/code-envs`, `/api/project-footprint`) should NOT be fetched for debugging — they trigger full scans
- Log lines with `[perf:*]` prefix appear in `/api/logs/raw-tail` after scans run — grep for `[perf:sdk_cache]`, `[perf:catalog]`, `[perf:ce]`, `[perf:pf]`
- CAVEAT: raw-tail tails the main DSS backend.log, which is often flooded by `process-resource-monitor` DEBUG lines — webapp INFO lines (e.g. `[prewarm]`) may be pushed out of the 100K window. `/api/debug/perf` cache_keys is a more reliable way to confirm scans ran; the support bundle's 1M-char tail also helps.
