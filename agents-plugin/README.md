# Admin Toolkit Agents (`admin-toolkit-agents`)

AI-native field ops on the Dataiku agentic framework. A separate plugin that layers
**agent tools** (sensors + gated actuators) and **plugin agents** on top of the
Admin Toolkit webapp backend's fleet-aware APIs. No frontend; everything surfaces
through the LLM Mesh (tools in projects, agents as virtual LLMs, Agent Hub).

## Architecture

```
Agent Hub / Answers / API
        │  (agents are virtual LLMs in the Mesh)
python-agents/{health-triage, scoping-architect, ops-actuator}
        │  in-process LangChain tools (agent_tools.py → tools_impl)
python-agent-tools/  ← 12 standalone tools, thin adapters over the same impls
        │
python-lib/atk_agent_common
        ├── client.py      ToolkitClient: one HTTP wrapper — unlock cookies,
        │                  X-DSS-Host-Id fleet routing, heavy-scan timeouts
        ├── tools_impl.py  pure sensor implementations (single source of truth)
        ├── health.py      exact Python port of the UI health score (parity-gated)
        ├── actuator.py    plan/execute with HMAC confirm tokens (confirm.py)
        ├── audit.py       story.agent_actions audit rows
        └── triage/        deterministic fleet sweep + daily scenario + store
        │
        ▼ HTTP (X-DSS-Host-Id header = fleet-aware for free)
Admin Toolkit webapp backend (existing plugin, UNCHANGED)
```

**Fleet model:** every tool takes `host` (default `local`); the backend proxies
remote hosts via its presets. Host ids are validated against `GET /api/hosts` ∩
the plugin's `host_allowlist` — a hallucinated id fails fast with the valid list.

**Unlocks:** the client lazily POSTs the configured passwords to
`/api/auth/red/unlock` and `/api/hosts/keys/unlock` on 403/409 and retries once.
A post-unlock rejection means password rotation → structured `red-locked` /
`remote-keys-locked` error, never a loop.

**Output discipline:** every tool result is top-N'd, key-allowlisted, and capped
at ~12KB serialized (`shaping.enforce_budget`), with `truncated`+note when the cap
bites and `name_filter`/`top_n` as pressure valves.

**Heavy scans** (`/api/code-envs`, `/api/project-footprint`, CRU): blocking GET
up to `heavy_timeout_s`; on timeout the tool returns
`{"status": "scan_running", progress, remediation}` — the backend coalesces
in-flight scans, so a later retry hits the warm cache. The daily triage sweep
doubles as the fleet-wide cache pre-warmer.

## Health score

`health.py` is a line-faithful port of
`resource/frontend/src/hooks/useHealthScore.ts` (`calculateHealthScore` @ 87eaa67)
**including the live-mode quirks** (see module docstring — do not "fix" them
without changing the TS first). Parity gate:
`scripts/agents/score_parity.py` runs the REAL TS path (frontend parsers +
calculateHealthScore via tsx) against the Python port on identical live payloads;
tolerance ±2, currently Δ=0.00 in every category on tam-global.

## Actuator safety model (layered, independent gates)

1. Plugin `enable_red_actions` master kill-switch (default OFF).
2. Per-agent `allow_red_actions` + `allowed_actions` allowlist.
3. `plan-admin-action` is the only token mint: `HMAC(red_password,
   action|host|canonical(target)|exp)`, 15-min TTL. `execute-admin-action`
   recomputes — any drift (action/host/target/expiry/password rotation) rejects.
4. `confirm: true` required, sent only after explicit human approval of the plan.
5. The backend's own `@advanced` red gate still applies server-side.

The plan **is** the dry run (blast radius from read-only scans: sizes, usage,
inactivity, backup folder). Every execute writes a `story.agent_actions` audit row.

Action catalog: `project-delete`, `code-env-delete` (both back up to a managed
folder first — the backend enforces it), `db-vacuum`, `db-analyze`,
`image-delete` (plan runs the backend's dryRun), `plugin-deploy`.
Excluded by design (highest blast radius): container-exec/code-env replace,
email send, cs-template migrate.

### Planned: `k8s-exec-config-tune` (cost optimizer)

Goal: let the agent propose containerized-execution config right-sizing (e.g. a
memory request from 8g → 2g) to cut k8s cost with minimal productivity impact.

- **Evidence:** `compute-cost` (CRU byContextType actual usage), `k8s-health`
  deep audit (node pressure / utilization), `config-inspect` (current
  `containerSettings.executionConfigs` values from `/api/settings/raw`).
- **Plan:** current vs proposed requests/limits, plus blast radius (which
  workloads run on that exec config, recent OOM signals from log-errors).
- **Execute:** containerized exec configs live in DSS general settings — a pure
  DSS API write. First increment: local-host writes via `dataiku.api_client()`
  from this plugin (no admin-toolkit change). Fleet-wide: either a small red
  endpoint in the admin-toolkit backend (later consolidation) or per-host plugin
  install. Same plan→confirm→execute handshake as every other action.

## Daily triage loop

`python-runnables/agent-triage-sweep` (global admin): deterministic sweep
(`triage/sweep.py` — health score per host, no LLM in ranking) → one LLM Mesh
completion per flagged host drafts a grounded recommendation → upsert into
`story.agent_triage_daily` → digest email → RAISES on host errors so the
scenario's failure reporter fires. Provisioning
(`triage/provision.py` + `scripts/agents/provision_triage.py`) copies the Story
scenario pattern exactly: ensure-or-repair, daily trigger, END_OF_RUN reporter,
save→refetch→verify with fallback shape.

## Configuration (plugin settings)

| Param | Meaning |
|---|---|
| `backend_url` | Admin Toolkit webapp backend base. Empty = auto-discover on local DSS. |
| `red_actions_password` | Plaintext Advanced Actions password. Empty = actuator permanently locked. |
| `host_keys_password` | Password for encrypted (`adkfk1$`) remote-host API keys. |
| `host_allowlist` | CSV of allowed host ids. Empty = all. |
| `default_llm_id` | Mesh LLM for agents (e.g. `anthropic:kaosclaude:claude-opus-4-8`). |
| `enable_red_actions` | Master kill-switch for execute-admin-action. Default false. |
| `triage_connection` | Postgres connection for triage rows + audit (same as Story's). |
| `triage_score_threshold` / `triage_mail_channel` / `triage_recipient` | Daily sweep knobs. |

Every setting has an `ATK_AGENTS_*` env override so the whole stack tests as pure
Python against a live backend without DSS (see `scripts/agents/`).

## Build & deploy (from repo root)

- `make agents-plugin` — bump version + zip to `dist/`
- `make agents-deploy-dev` — bump + zip + updateFromZip/installFromZip on the dev
  DSS (`.dss-url`/`.dss-api-key`), then build the code env once after first install:
  `bash scripts/dss_api.sh POST /public/api/plugins/admin-toolkit-agents/code-env/actions/create --data '{}'`

Tool instances use type `Custom_agent_tool_admin-toolkit-agents_<component>`;
agent instances use `create_agent(name, 'PLUGIN_AGENT', plugin_agent_type='admin-toolkit-agents_<component>')`.

## Verification scripts (`scripts/agents/`, not packaged)

- `verify_endpoints.py` — record real backend response shapes (run FIRST, always)
- `test_tools.py` — run tool instances through the real DSS agent-tool runtime
- `test_agent.py` — query an agent as a virtual LLM through the Mesh
- `score_parity.py` — Python-vs-TS health-score parity gate (±2)
- `provision_triage.py` — ensure the daily triage scenario
