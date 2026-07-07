# Admin Toolkit Agents (`admin-toolkit-agents`)

AI-native fleet administration on the Dataiku agentic framework. This plugin layers
**agent tools** (read-only sensors + gated actuators) and three **plugin agents**
on top of the Admin Toolkit webapp backend's fleet-aware APIs; the Admin Toolkit
webapp's **Agents page** is the first-party chat frontend for them.

The design goal, stated bluntly: *let the laziest, least knowledgeable admin run a
DSS fleet like a veteran* — every prompt they could need is one click away, every
finding turns into a checkable action item, every action is planned, human-approved,
executed, audited and deep-linked, and every concept on screen explains itself.

This document is both the README and the developer reference: architecture, the
exact system prompts, the wire protocols, the safety model, the frontend contract,
configuration, deployment, testing, and the traps we hit so you don't have to.

- Plugin: `admin-toolkit-agents` **v0.1.008**
- Webapp counterpart: `admin-toolkit` **v0.4.631** (Agents page v2)
- DSS: developed and verified against **14.7**

---

## Table of contents

1. [System architecture](#1-system-architecture)
2. [The three agents and their system prompts](#2-the-three-agents-and-their-system-prompts)
3. [Tools](#3-tools)
4. [The streaming event protocol](#4-the-streaming-event-protocol)
5. [The action-item pipeline (sensor → checklist → actuator)](#5-the-action-item-pipeline-sensor--checklist--actuator)
6. [Actuator safety model](#6-actuator-safety-model)
7. [Audit trail](#7-audit-trail)
8. [Tracing (LLM Mesh trace explorer)](#8-tracing-llm-mesh-trace-explorer)
9. [Webapp frontend (Agents page v2)](#9-webapp-frontend-agents-page-v2)
10. [Prompt library & megaprompts](#10-prompt-library--megaprompts)
11. [Education layer (InfoDots)](#11-education-layer-infodots)
12. [DSS deep links](#12-dss-deep-links)
13. [Health score & daily triage loop](#13-health-score--daily-triage-loop)
14. [Configuration](#14-configuration)
15. [Provisioning](#15-provisioning)
16. [Build, deploy & the kernel-pinning trap](#16-build-deploy--the-kernel-pinning-trap)
17. [Testing & verification](#17-testing--verification)
18. [Extending the system](#18-extending-the-system)
19. [Known traps](#19-known-traps)

---

## 1. System architecture

```
                       Admin Toolkit webapp — Agents page (React)
                       chat UI · prompt library · action-item checklists ·
                       plan/execution cards · batch approvals · audit trail
                                        │  SSE
                       POST /api/agents/chat   (webapp backend, Flask)
                                        │  agent.as_llm().new_completion()
                                        │      .execute_streamed()
                       ┌────────────────▼─────────────────┐
    Agent Hub /        │            LLM Mesh              │
    Answers / API ─────►  agents are virtual LLMs         │
                       └────────────────┬─────────────────┘
                                        │ (plugin agent kernel, python)
        python-agents/{health-triage, scoping-architect, ops-actuator}
                                        │  hand-rolled LangChain tool loop
                                        │  (agent_runtime.run_tool_loop)
        python-agent-tools/  ← 12 standalone Mesh tools, thin adapters
                                        │
        python-lib/atk_agent_common
            ├── client.py        ToolkitClient: one HTTP wrapper — unlock
            │                    cookies, X-DSS-Host-Id fleet routing,
            │                    heavy-scan timeouts
            ├── tools_impl.py    pure sensor implementations (single source
            │                    of truth for tools AND agents)
            ├── agent_tools.py   LangChain StructuredTool wrappers
            ├── agent_runtime.py streaming tool loop + event protocol + spans
            ├── action_items.py  propose_action_items (validation only)
            ├── actuator.py      plan/execute + per-action planners/executors
            ├── confirm.py       HMAC confirm tokens (mint/verify)
            ├── audit.py         agents.agent_actions audit rows
            ├── shaping.py       output budget (~12KB cap per tool result)
            ├── health.py        exact Python port of the UI health score
            └── triage/          deterministic fleet sweep + daily scenario
                                        │
                                        ▼ HTTP (X-DSS-Host-Id header)
        Admin Toolkit webapp backend (existing plugin — UNCHANGED by agents)
```

Two plugins, one brain:

- **`admin-toolkit`** (the big one) owns the backend APIs, all scanning, the red
  (destructive) endpoints, and the webapp UI including the Agents page.
- **`admin-toolkit-agents`** (this one) owns everything agentic: tool
  implementations call the admin-toolkit backend over HTTP exactly like the
  frontend does, which makes every tool **fleet-aware for free** (the
  `X-DSS-Host-Id` header routes to remote hosts through the backend's presets).

**Fleet model:** every tool takes `host` (default `local`). Host ids are
validated against `GET /api/hosts` ∩ the plugin's `host_allowlist`; a
hallucinated host id fails fast with the list of valid ids in the error.

**Unlocks:** `client.py` lazily POSTs the configured passwords to
`/api/auth/red/unlock` and `/api/hosts/keys/unlock` on 403/409 and retries once.
A post-unlock rejection means password rotation → structured `red-locked` /
`remote-keys-locked` error, never a retry loop.

**Output discipline:** every tool result is top-N'd, key-allowlisted, and capped
at ~12KB serialized (`shaping.enforce_budget`), with a `truncated` note when the
cap bites and `name_filter`/`top_n` as pressure valves.

**Heavy scans** (`/api/code-envs`, `/api/project-footprint`, CRU): blocking GET
up to `heavy_timeout_s`; on timeout the tool returns
`{"status": "scan_running", progress, remediation}` — the backend coalesces
in-flight scans, so a later retry hits the warm cache. The daily triage sweep
doubles as the fleet-wide cache pre-warmer.

---

## 2. The three agents and their system prompts

All three are `PLUGIN_AGENT`s (kernel-per-agent, pooled). They register in the
Mesh as `agent_admin-toolkit-agents_<component>` and are instantiated in the
`ADMINTOOLKIT` project (the toolkit's own macro project — since 0.4.658; they
lived in a separate `AGENTOPS` project before). They share one runtime (`agent_runtime.run_tool_loop`): a
deterministic bind-tools → stream model turn → execute tool calls → repeat loop
(max 12 iterations), chosen over `AgentExecutor` for version stability and
because it lets us stream the typed event protocol (§4).

### 2.1 ATK Health Triage (`health-triage`) — read-only sensor

Fleet health sweeps and triage reports. Tools: **all 10 sensors** +
`triage_sweep` + `propose_action_items`. Per-instance config: `llm_id`,
`hosts` (CSV filter), `score_threshold` (default 75), `max_recommendations`
(default 5).

System prompt (verbatim; `{max_recommendations}`, `{severity_rubric}` (§2.5)
and `{action_items_addendum}` (§2.3) are substituted at build time):

```
You are the Admin Toolkit health-triage agent for a fleet of Dataiku DSS instances.

Ground rules:
- Answer ONLY from tool output. Never invent metrics, host names, or issues. If a tool
returns an error payload, relay its message and remediation instead of guessing.
- Cite the host id and the tool that produced each number or claim, e.g. "(instance-health, host=akaos-vm)".
- A tool result with status=scan_running means the data is still warming: say so and
suggest retrying in a few minutes; do not treat it as a failure or as healthy.

When the user asks for a sweep / triage / fleet check / "how are my instances":
1. Call the triage_sweep tool ONCE — it deterministically scores every host with the same
0-100 health score the toolkit UI shows and flags hosts under the threshold. Do not
re-derive or second-guess the ranking.
2. For each flagged host (worst first, at most {max_recommendations}), draft ONE concrete
recommendation grounded in its topIssues and signals (log errors, sanity check). Structure
per host: score + status, top 3 issues, your recommendation, the suggested next action,
and the evidence (issue ids / log signatures you used).
3. Close with a one-paragraph fleet summary.

For ad-hoc questions, use the sensor tools directly and keep the same grounding rules.
Health scores are 0-100 (higher is better); by default <80 is a warning, <50 critical. A
score capped at the critical band means one of the always-lead critical rules fired — name
the rule, don't just report the number.
{severity_rubric}
{action_items_addendum}
```

### 2.2 ATK Scoping Architect (`scoping-architect`) — read-only analyst

Sizing, migration and capability questions for field engineers. Tools:
`list_hosts`, `config_inspect`, `instance_health`, `k8s_health`, `db_health`,
`compute_cost`, `storage_footprint` + `propose_action_items`.

System prompt (verbatim; the severity rubric (§2.5) then the action-items
addendum (§2.3) are appended):

```
You are the Admin Toolkit scoping architect: you answer technical scoping and
architecture questions about a fleet of Dataiku DSS instances for field engineers preparing
customer work (sizing, migration, capability, integration questions).

Grounding contract — this is absolute:
- Every factual claim about an instance MUST come from a tool call in this conversation, and
MUST cite the host id and tool, e.g. "(config_inspect llms, host=local)".
- If the toolkit cannot observe something, say "not observable from the toolkit" and name
what WOULD answer it (e.g. a missing scan, an unconfigured module). Never fill gaps from
general Dataiku knowledge without labeling it as general knowledge, clearly separated from
observed facts.
- Tool errors carry a message + remediation: relay them; do not retry more than once.
- status=scan_running means data is warming server-side — say so and suggest asking again in
a few minutes.

Method: start with list_hosts when host scope is unclear; prefer targeted tools (config_inspect
with domain/name_filter) over broad pulls; issue independent tool calls in parallel. Answer
structure: direct answer first, then the observed evidence with citations, then caveats.
General Dataiku architecture guidance (version support, sizing rules of thumb) is welcome as
long as it is labeled as guidance and tied to the observed configuration.
```

### 2.3 The shared action-items addendum (both sensor agents)

`action_items.PROMPT_ADDENDUM`, appended to both sensor prompts (verbatim,
with `{max_items}`=10 and `{actions}`=the actuator catalog substituted):

```
When your findings imply concrete admin work (cleanup, maintenance, tuning, deletions,
deploys), finish the investigation by calling propose_action_items ONCE with every piece of
work you identified (most important first, max 10). Rules:
- Propose only items at MEDIUM severity or higher (the severity rubric's digest floor).
Whitelist-suppressed findings never reach you — do not hedge live findings; every finding
you see is live.
- Set `action` + `target` ONLY when they map exactly to the actuator catalog ({actions});
anything else stays advisory (title/why/evidence only, no action).
- Several objects needing the SAME action (e.g. six unused code envs) = ONE item with
`targets: [dict, ...]` — never burn one item slot per object. Batchable: {batchable}.
- risk: 'red' for anything destructive or settings-mutating (deletions, config/settings
changes — all require backup-first / prior-value recording downstream), 'amber' for
locking/maintenance operations, 'green' for safe low-impact work. Never soften a risk color.
- Every item needs concrete `evidence` entries citing tool + host + the numbers that justify it.
The items render as a checklist; the USER decides what is handed to the ops-actuator for
planning and approval. Never plan, never execute, never promise execution yourself.
```

### 2.4 ATK Ops Actuator (`ops-actuator`) — the only agent that can mutate

Tools: `list_hosts`, `instance_health`, `storage_footprint`, `config_inspect`,
`db_health`, `compute_cost` (for grounding targets) + `plan_admin_action` +
`execute_admin_action`. Per-instance config: `llm_id`, `allow_red_actions`
(bool), `allowed_actions` (CSV subset of the catalog; empty = all).

System prompt (verbatim; `{action_safety_rubric}` (§2.5) and
`{allowed_actions}` substituted):

```
You are the Admin Toolkit ops actuator: you carry out administrative actions on
Dataiku DSS instances with a strict human-in-the-loop protocol.

The protocol — never deviate:
1. UNDERSTAND: use the sensor tools to identify the exact target (never guess names/keys).
2. PLAN: call plan_admin_action. It returns the blast radius and a confirm_token.
3. SHOW: present the returned plan to the user VERBATIM — summary, sizes, warnings,
projects affected, backup destination. Do not soften warnings.
4. WAIT: ask "Do you confirm?" and STOP. Only an explicit affirmative in the user's NEXT
message counts as confirmation. Pre-authorization ("just do it for anything") does NOT count
— each action needs its own confirmation after its own plan.
5. EXECUTE: call execute_admin_action with the exact canonicalTarget, confirm=true and the
token. Report the outcome AND the auditId.

If a tool returns an error (red-locked, kill-switch off, token rejected/expired), relay its
message and remediation; never work around a gate. If the token expired because the user took
time to answer, re-plan and re-confirm.

Batch protocol (messages carrying a list of pre-approved-for-planning action items, e.g. a
handoff from another agent's checklist): plan EVERY listed item — one plan_admin_action call
per item, passing the item's item_ref verbatim so plans and audit rows stay traceable to the
checklist. Present each plan (the UI renders them as cards), then WAIT. The user may approve
plans individually or in one batch message enumerating several tokens; execute exactly the
plans whose tokens they approved, one execute_admin_action per plan with its own item_ref, and
report each outcome + auditId separately. A batch handoff is NOT confirmation — every execution
still requires the user's explicit approval of that specific plan.
{action_safety_rubric}
Allowed actions for this agent: {allowed_actions}.
```

### 2.5 The shared severity rubric (`atk_agent_common/rubric.py`)

Canonical source: `docs/agent-workflows/severity-rubric.md` (the Principal-TAM
severity-calibration interview, distilled). `rubric.SEVERITY_RUBRIC` is
substituted into both sensor prompts; `rubric.ACTION_SAFETY_RUBRIC` into the
actuator prompt.

`SEVERITY_RUBRIC` (verbatim):

```
SEVERITY RUBRIC (calibrated with the customer's Principal TAM — apply to every finding):

Audience & floor: your reader is the instance ADMIN. Operational findings only — no
adoption/QBR metrics, no renewal framing. Digest/report floor: MEDIUM and higher.

ALWAYS-LEAD CRITICALS (open with these whenever present):
- Internal H2 runtime DB — critical unconditionally, all sizes; migrate to PostgreSQL now.
- DIP_HOME on NFS — critical, no exceptions.
- cgroups not configured on a multi-user instance — critical; do not wait for observed
memory pressure.
- /data partition (DIP_HOME mount) >= 75% full.
- An ACTIVELY-USED connection that broke RECENTLY (failing test alone = low cleanup mess;
severity = usage x breakage recency — always join test status with usage first).
- Deprecated Python in ACTIVE use (see lifecycle below).
- Exec configs without requests+limits — critical if OOMKilled/evictions observed, else important.
- Failure/retry storms lasting more than 1 hour.

CALIBRATED THRESHOLDS:
- CPU load: sliding scale — sustained >=90% of all cores for 10 min, OR >=80% for 20 min,
OR >=70% for 30 min (lower level => longer window).
- Clock/NTP drift: small = ignore; past best-practice tolerance (sub-second for
TLS/Kerberos-sensitive setups; minutes = definitely broken) = VERY HIGH — breaks SSL/auth.
- Backend restarts: 2 unexplained within ~a week = stability finding.
- xmx: finding = actual xmx below DSS's own memory-algorithm recommendation (no absolute bands).
- Kernels/JEKs alive beyond ~days = finding; containerized/K8s escalates one severity band.
Idle age matters more than RSS.
- Idle GPU nodes: finding after 1 hour; weight ~10x normal idle compute.
- Version lifecycle (DSS-version-aware): DSS 1 major behind = bad. In-use Py 3.6/3.7 =
important (migrate now); in-use 3.8 on DSS 14 = warning (plan before removal); UNREFERENCED
deprecated env = delete candidate only. DSS 14 also deprecates: Govern PostgreSQL 12-14,
MXNet forecasting, MLLib, AmazonLinux 2, KSQL recipes, Graphite/metrics-charting API-deployer
settings, plugins "List folder Contents"/"Azure AD Sync"/"EMR clusters"/"Dataproc clusters".
- Code env >5GB = finding (whitelist-subject). Project >10GB = finding (whitelist-subject) —
typically webapp logs or filesystem files instead of block storage.
- Zero-usage code envs & plugins: cleanup candidates after 3 months. Zero-git-commit
projects: low-priority warning. Abandonment: use the toolkit's CONFIGURED inactivity cutoff
(read it; never hardcode).
- R env included but unused = bad; if a DEFAULT R env is set in admin settings, issue the
standing recommendation to unset it (it drags R into projects that don't use it).
- Connections: filesystem_managed for real team data = bad (push S3/ADLS); orphaned
connections = 3-month cleanup; many same-type connections = fine, NOT a finding;
pushdown not enabled = medium improvement.
- DB health: scale-gate table bloat/vacuum/size findings at ~1000+ users — EXCEPT observed
connection-pool exhaustion errors = very high at ANY size. Propose VACUUM/ANALYZE only on
1000+-user instances; below that, surface-only.
- Job/scenario logs: judge by share of the /data disk, not age.

COST CLASS (report as cost/waste, never as instance health): registry image sprawl
(retention: nothing older than the current image), oversized containers (p95 utilization
<~50% of request; exempt spiky), zero-traffic 24/7 webapps/APIs (~2-4 weeks near-zero =>
owner-outreach shutdown proposal), one user's sandbox dominating = medium, autoscaler off =
standing important, bin-packing waste >30-40% sustained = act. LLM cost: quote
estimatedCostUSD as DSS's own estimate; note the rolling days-weeks audit horizon.

USERS (admin lens): designer-seat reclaim list only at >=95% utilization; users without
email = medium; departed-but-enabled = low hygiene.

USE YOUR JUDGMENT (deliberately un-thresholded): backend.log pattern triage (real vs
noise), package-pin risk, connection perf params, correlating DB symptoms with UI slowness.

NEVER MENTION (non-findings): swap (corroborating signal only), backups (Fleet Manager owns
them), permission/governance patterns, shared namespaces, R/conda presence per se, GC
flags, dataset-version bloat absent disk share, dormant-ratio targets, duplicate-env drift.

WHITELIST: thresholded size/cleanup findings honor a per-item admin whitelist, and
whitelist-suppressed findings are removed UPSTREAM — nothing you see is whitelisted.
Treat every finding in your data as live and propose items without hedging. When tool
output reports a suppressed count, relay only the count ("N findings suppressed by
admin whitelist") — never speculate about what was suppressed.
```

`ACTION_SAFETY_RUBRIC` (verbatim):

```
ACTION-SAFETY DOCTRINE (customer-calibrated — this governs how you present actions):
- You may PROPOSE any destructive action — nothing is off-limits to propose — but a human
must approve, and every execution needs its own explicit confirmation (pre-authorization
never counts).
- Every destructive deletion backs up to block storage FIRST (the plan shows the backup
destination — never present a delete without one).
- Settings changes are RECORDED with their prior value and restorable from the last 50
changes per item; say so when presenting a settings-change plan. If the result carries a
history warning (audit DB not configured), tell the admin the change will NOT be restorable
from history before they confirm.
- If restore is impossible for an action, say so explicitly in the plan presentation.
- Never advise or attempt Linux-level kills of DSS-managed processes (kernels, JEKs, webapp
backends) — they respawn; they are stopped at the DSS level via DSS APIs.
- Connection and cluster mutations are backup-first too: connection-delete backs up the
definition JSON (it may carry credential material — the folder is admin-scoped) and
cluster-detach backs up the cluster definition before removing the DSS attachment (the
cloud-side cluster keeps running until removed in the cloud console — say so).
- BATCH PROTOCOL: batchable actions accept targets[] — ONE plan, ONE confirm token, N
targets. Present the plan's per-target table verbatim; one approval covers every target,
execution is per-target with per-target results (partial success is reported per entry).
Never split a homogeneous batch into N separate plans unless the user asks.
```

---

## 3. Tools

### 3.1 The 10 sensors (available standalone in the Mesh AND in-process to agents)

One implementation per tool in `tools_impl.py`; `python-agent-tools/<name>/tool.py`
are thin Mesh adapters, `agent_tools.build_langchain_tools()` wraps the same
functions as LangChain `StructuredTool`s (signature-derived arg schemas, results
JSON-serialized into `ToolMessage` content).

| Tool | What it returns |
|---|---|
| `list_hosts` | Reachable DSS hosts (id, label, url); `probe=true` checks reachability. |
| `instance_health` | Health snapshot of one host: system/sanity/java/issues/score sections; `include_score=true` forces heavy scans (may return `scan_running`). |
| `compute_cost` | Compute + LLM cost from CRU audit records, `group_by=project\|user\|context_type`. Span limited to audit retention (`span` field). |
| `config_inspect` | One config domain: `connections\|code-envs\|plugins\|llms`, `detail=health\|usage`, `name_filter`. |
| `log_errors` | backend.log error groups; `pattern=<regex>` greps the raw tail. |
| `storage_footprint` | Project storage totals, largest projects, inactive+large cleanup candidates. Heavy scan. |
| `k8s_health` | K8s clusters: states + reachability sweep; `cluster=<id>` runs a deep audit. |
| `db_health` | Runtime PostgreSQL health: `overview\|tables\|per-project`. |

### 3.2 Runtime-injected tools (agents only, not Mesh components)

These are built inside the agent's `aprocess_stream` and injected into the tool
list — no plugin component, no provisioning change needed to add one.

**`triage_sweep`** (health-triage only): deterministic fleet triage — scores
every configured host with the exact UI health score, ranks worst-first, flags
hosts under the threshold, attaches supporting signals. No LLM in the ranking.
Takes no arguments.

**`propose_action_items`** (both sensor agents): pure validation/normalization
(`action_items.py`) — **no tokens, no planning, no side effects**. Tool
description shown to the model (verbatim):

```
Propose up to 10 structured admin action items derived from your findings. Each item:
{title (<=120 chars), why (<=500), host (default local), risk: red|amber|green,
action?: exact actuator action name, target?: the action's target dict,
targets?: [target dicts] for several objects under ONE item (batchable actions only),
evidence: [strings]}.
Set action ONLY when it maps exactly to one of: <full catalog> — target shapes:
<generated from atk_agent_common.actions.TARGET_SHAPES — single source, never edited by
hand>. Batchable actions accept targets: [dict, ...] (max 20) — several objects, same
action, ONE plan and ONE confirm token. Items with no valid action become advisory
(still shown, not executable). Call ONCE, at the end of the investigation.
```

Normalization rules (`action_items.propose_action_items`):
- Cap **10** items; extras counted in `droppedCount` and the model is told to
  mention the important dropped ones.
- **Server-assigned ids**: item `id = ai-<8hex>`, batch `batchId = aib-<8hex>`
  (model-provided ids are ignored — the frontend can trust uniqueness).
- `title` clipped to 120 chars (required — titleless items dropped), `why` to
  500, evidence entries to 300 chars / max 6.
- `risk` normalized to `red|amber|green`; anything else → `amber` + a
  `validation` note.
- `targets[]` (batching): capped at **20** per item; non-dict entries dropped
  with a note; >1 target on a non-batchable action keeps the first target only
  (noted). Output items carry `targets` + `targetCount`, with `target`
  mirroring `targets[0]` for back-compat.
- An `action` not in the actuator catalog, or an action without any target
  dict → **downgraded to advisory** (`actionable:false`, `action:null`) with a
  `validation` note. *Never silently dropped* — the human still sees the finding.
- Output: `{batchId, items[], count, nextStep, droppedCount?}` where `nextStep`
  tells the model the items are now a user-facing checklist and it must not
  plan or execute anything.

**`plan_admin_action` / `execute_admin_action`** (ops-actuator only): wrappers
over `actuator.py` that add the per-agent allowlist gate and the
`allow_red_actions` gate, and thread `item_ref {batchId, itemId}`:
- `plan_admin_action(action, target, host='local', params=None, item_ref=None)` —
  `item_ref` is echoed as `itemRef` in the plan output. It is **deliberately NOT
  part of the signed token payload** (`confirm.py` is byte-identical to v0.1.007).
- `execute_admin_action(action, target, confirm, confirm_token, host='local',
  item_ref=None)` — `item_ref` becomes `provenance`, which lands in the audit
  row's `params` column and is echoed as `itemRef` in the execution output.

---

## 4. The streaming event protocol

### 4.1 Kernel → Mesh (what `run_tool_loop` yields)

DSS plugin agents stream `{'chunk': {...}}` dicts. Ours:

| Chunk | Shape | When |
|---|---|---|
| text delta | `{'chunk': {'text': '<delta>'}}` | model tokens, streamed as they arrive (`DKUChatModel.astream`, AIMessageChunks summed so tool_calls reassemble) |
| `tool_call` | `{'chunk': {'type':'event','eventKind':'tool_call','eventData':{'name','args'}}}` | before each tool executes |
| `tool_result` | `... eventKind:'tool_result', eventData:{'name','durationMs','ok','error'}` | after each tool, `error` = the tool's error object or null |
| `plan` | `... eventKind:'plan', eventData:<full plan_admin_action output>` | when the executed tool was `plan_admin_action` and the result has `confirm_token`. Includes `action, host, canonicalTarget, plan, confirm_token, expiresInSeconds, nextStep, itemRef?` |
| `execution` | `... eventKind:'execution', eventData:<full execute output>` | when the tool was `execute_admin_action` and the result has `status`. Includes `action, host, target, status, result, auditId, itemRef?, auditWarning?` |
| `action_items` | `... eventKind:'action_items', eventData:{batchId, items[], count, nextStep, droppedCount?}` | when the tool was `propose_action_items` and the result has `items` |

Event parsing is defensive (`_result_event`): the events are best-effort UI
sugar, never load-bearing for the loop — a malformed tool result still flows to
the model as a `ToolMessage`. Unknown eventKinds are ignored by old frontends
(verified), so adding kinds is backward-compatible.

The loop stops on: a model turn with no tool calls (final answer), or 12
iterations (then a visible `[stopped: tool-call iteration limit reached …]` text
chunk).

### 4.2 Webapp backend → browser (SSE)

`POST /api/agents/chat` with `{agentId, messages:[{role:'user'|'assistant',
content}, …]}` (last 40 messages, 20k chars each). The Flask generator relays
`completion.execute_streamed()` chunks as SSE:

| SSE event | Payload |
|---|---|
| `chunk` | `{text}` |
| `agent_event` | `{eventKind, eventData}` (verbatim from §4.1) |
| `done` | `{finishReason, durationMs, traceAvailable, traceId?, traceExplorer?, traceExplorerPath?}` |
| `error` | `{message}` |

The frontend consumes this **only** through `utils/sseStream.parseSseStream`
(inline SSE parsers are banned by the contract checker), via `fetchRaw` from
`utils/api.ts` (which injects `X-DSS-Host-Id` — the chat is per-host like
everything else).

Note: the completion footer contains the **full trace dict but no trace id**
(verified live on 14.7 — keys are `additionalInformation`, `finishReason`,
`totalUsage`, `trace`, `type`), so the backend mints its own: `traceId` is a
short id into an in-memory ring of the last 8 traces (`GET
/api/agents/last-trace?id=…` returns the JSON; expiry is a normal state).
`traceExplorer` is `{viewPath, webAppId, projectKey}` of the Trace Explorer
webapp when one exists on the host (`traceExplorerPath` is the pre-0.4.648
back-compat alias carrying just the path). See §8 for the one-click flow.

---

## 5. The action-item pipeline

The v2 headline feature: sensor findings become executable work without giving
sensor agents any mutating tools, and without weakening a single gate.

```
health-triage / scoping-architect                    (sensor conversation)
      │ propose_action_items(items) → action_items event
      ▼
ActionItemsCard checklist  — risk-colored, advisory rows disabled,
      │                      evidence expanders, "Send N to Ops Actuator"
      │ user checks items    (only actionable+unsubmitted rows selectable)
      ▼
ONE synthetic user message to the ops-actuator conversation      (frontend-built)
      │
      ▼
ops-actuator plans each item (plan_admin_action × N, item_ref passthrough)
      │ plan events → PlanCard × N (+ PendingApprovalsBar at ≥2 pending)
      │ user approves individually or "Approve all (N)"
      ▼
ONE approval message enumerating each plan's own confirm_token
      │
      ▼
execute_admin_action × N  → execution events → audit rows with provenance
```

**Why frontend-mediated?** The rejected alternative — giving sensor agents
`plan_admin_action` — would fire heavy scans inside triage turns and start the
15-minute token TTL at *proposal* time (stale before the human finishes reading
the checklist). With the handoff, blast radius and tokens are minted fresh at
approval time, and `confirm.py`/all five gates are untouched.

### 5.1 The synthetic messages (exact formats, built in `agentsChatStore.ts`)

**Handoff** (`submitActionItemsToActuator`) — one message, N checked items:

```
Action-item batch handoff (batch <batchId>, <N> item(s) selected by the user from another agent's findings).
Plan EVERY item below — one plan_admin_action call per item, passing its item_ref verbatim. Present each plan and WAIT for my approval. Do NOT execute anything yet.

1. [<itemId>] <title> — action=<action> host=<host> target=<JSON> item_ref={"batchId":"<batchId>","itemId":"<itemId>"}
   why: <why>
   evidence: <evidence1> | <evidence2>
2. …
```

**Single approve** (`approvePlans` with one plan — also used by the per-card button):

```
Approved — I confirm. Execute the planned <action> on host <host> with the exact planned
target, confirm=true and confirm_token <token>[ and item_ref <JSON>]. Report the outcome
and the auditId.
```

**Batch approve** (`approvePlans`, ≥2 plans — sent after the confirm dialog):

```
Approved — I confirm ALL <N> plans below. Execute each independently with its exact planned
target, confirm=true, its own confirm_token (and its item_ref where given); report each
outcome and auditId separately:
1. <action> on host <host> — confirm_token <token> item_ref=<JSON>
2. …
```

**Reject** (`rejectPlans`; single / batch):

```
Rejected — do NOT execute the planned <action>. Stand down and await further instructions.
Rejected — do NOT execute ANY of the following <N> planned actions: <action> on <host>; …. Stand down and await further instructions.
```

Store-level guards: the handoff refuses (no-op) if the actuator conversation is
mid-stream (items would be marked submitted while the message dropped);
submitted item ids lock their checkboxes; advisory items are never submittable.

---

## 6. Actuator safety model

Five **independent** gates — every one alone is sufficient to stop an execution:

1. Plugin `enable_red_actions` master kill-switch (default OFF). Checked at
   execute time, *before* anything else — a killed execution writes **no audit
   row** because nothing was attempted.
2. Per-agent-instance `allow_red_actions` + `allowed_actions` allowlist
   (checked in the agent's tool wrappers; a plan for a non-allowlisted action
   is refused too).
3. The confirm token: `plan_admin_action` is the **only** mint —
   `HMAC-SHA256(sha256(red_password + 'atk-agents-confirm-v1'),
   {a: action, h: host, t: canonical(target), e: expiry})`, TTL **15 minutes**,
   base64url `payload.signature`. `execute_admin_action` recomputes; any drift
   (action/host/target/expiry/password rotation) rejects with a structured
   error. Rotating the Advanced Actions password instantly voids all
   outstanding tokens.
4. `confirm: true` required — the prompt instructs the model to send it only
   after the human explicitly approved that specific plan in the conversation.
5. The admin-toolkit backend's own `@advanced` red gate still applies
   server-side (the plugin client unlocks with the configured password; a wrong
   password is a hard stop).

The plan **is** the dry run: each planner gathers the exact targets + blast
radius from read-only scans and shows the human precisely what will happen.
There is no single-call path to a mutation.

### Action catalog

Legacy planners/executors live in `actuator.py`; every newer action lives in
`python-lib/atk_agent_common/actions/` (per-domain `SPECS` merged by the
package registry, which also GENERATES the target-shape prose all three tool
descriptions quote — the catalog can never drift from its docs). Patterns:
**A** = in-agent local `dataiku.api_client()` write (LOCAL-ONLY), **B-api** =
backend red route, pure dataikuapi on `g.client` (fleet-routable), **B-macro**
= backend red route → ADMINTOOLKIT macro + policy engine.

**Batching**: actions marked ⨯N accept `targets: [dict, ...]` (max 20) — ONE
plan, ONE confirm token, per-target execution results (continue-on-error,
`ok/partial/error` overall status, ONE audit row).

| Action | Target | Pattern / notes |
|---|---|---|
| `project-delete` ⨯N | `{projectKey}` | B-api; zip backup enforced |
| `code-env-delete` ⨯N | `{name, lang}` | B-api; backup enforced; breaks-projects warning |
| `db-vacuum` / `db-analyze` ⨯N | `{connection, table}` | B-api; lock note |
| `image-delete` | `{provider, cutoff, images}` | B-api; plan carries the backend dryRun |
| `plugin-deploy` ⨯N | `{pluginId, targetHostId}` | B-api (hub → fleet host) |
| `k8s-exec-config-tune` | `{configName, changes}` | A (LOCAL-ONLY); >75%-cut OOM warnings |
| `log-cleanup` | `{roots?, minAgeDays?, maxDeleteGB?}` | B-macro (LOCAL-ONLY); rotated logs only |
| `docker-prune` | `{mode, keepStorageGB?, filterUntilHours?}` | B-macro (LOCAL-ONLY); fixed argv |
| `k8s-apply-fix` | `{clusterId, commands[], …}` | B-macro (LOCAL-ONLY); kubectl policy |
| `code-env-consolidate` | `{sourceEnvName, targetEnvName, …}` | B-api; dry-run usage table |
| `settings-set` ⨯N | `{path, newValue}` | A (LOCAL-ONLY); blacklist + drift guard + history |
| `connection-test` ⨯N | `{name}` | B-api, green; read-only probe |
| `connection-update` | `{name, path, newValue}` | B-api, amber; secret paths blocked, drift-guarded, history hook |
| `connection-delete` ⨯N | `{name}` | B-api, red; definition JSON backup (may carry credentials — admin-scoped folder), usage warning |
| `cluster-detach` | `{clusterId}` | B-api, red; definition backup; detaches the DSS attachment ONLY |
| `plugin-uninstall` ⨯N | `{pluginId}` | B-api, red; zip backup; refused while ANY usage exists; never `admin-toolkit` itself |
| `project-clear-webapp-runs` ⨯N | `{projectKey, keepDays?, keepLastRuns?}` | B-macro, amber; fs-cleanup macro (fs_paths policy: run_* dirs only, keep-newest-N, running-backend exclusion) |
| `connection-index` | `{connectionNames?}` | B-api, green; catalog re-crawl, empty = all |
| `cluster-stop` | `{clusterId, terminate?}` | B-api, red; managed only; `terminate=true` **IRREVERSIBLE** (cloud resources destroyed) |
| `cluster-start` | `{clusterId}` | B-api, amber; managed only; cost resumes |
| `cluster-pods-cleanup` | `{clusterId}` | B-api, green; finished pods/jobs only |
| `plugin-update` | `{pluginId}` | B-api, amber; pre-update zip backup; store update (dev plugins refused) |
| `plugin-code-env-rebuild` | `{pluginId}` | B-api, amber; kernels recycle lazily |
| `code-env-update` | `{name, lang?, forceRebuild?}` | B-api, amber; packages + container images |
| `project-export` ⨯N | `{projectKey}` | B-api, green; zip bundle → managed folder |
| `project-set-cluster` | `{projectKey, clusterId}` | B-api, amber; drift-guarded; history hook; fixes WARN_CLUSTERS_NONE_SELECTED_PROJECT |
| `project-change-owner` | `{projectKey, newOwner}` | B-api, amber; drift-guarded; history hook |
| `project-variables-set` | `{projectKey, path, newValue}` | B-api, amber; scoped path (standard./local.); secret paths blocked; history hook |
| `job-kill` ⨯N | `{projectKey, jobId}` | B-api, amber; DSS-level abort |
| `scenario-disable` / `scenario-enable` ⨯N | `{projectKey, scenarioId}` | B-api, amber; drift-guarded toggle; history hook; each is the other's revert |
| `scenario-kill` | `{projectKey, scenarioId}` | B-api, amber; aborts the current run only |
| `scenario-run` | `{projectKey, scenarioId}` | B-api, amber; manual trigger |
| `continuous-activity-stop` | `{projectKey, recipeId}` | B-api, amber; desired state persisted |
| `webapp-backend-stop` / `webapp-backend-restart` ⨯N | `{projectKey, webappId}` | B-api, amber; users lose their session |
| `notebook-kernels-shutdown` | `{projectKey?}` | B-api, amber; active kernels only; files/outputs untouched |
| `notebook-clear-outputs` ⨯N | `{projectKey, notebookName}` | B-api, amber; outputs not restorable (re-run regenerates) |
| `variables-set` | `{path, newValue}` | B-api, amber; GLOBAL variables; secret paths + `admin_toolkit_finding_whitelist` blocked; history hook |
| `user-disable` ⨯N | `{login}` | B-api, red; never deletes; refuses the toolkit's own identity; history hook |
| `user-enable` | `{login}` | B-api, amber; inverse of user-disable |
| `api-key-delete` | `{keyType: personal\|global, keyId}` | B-api, red; **IRREVERSIBLE**; refuses the caller's own personal key; global keys carry no owner → plan tells the human to verify |
| `tmp-cleanup` | `{minAgeDays?, maxDeleteGB?}` | B-macro, amber; aged entries inside tmp buckets (webappruns bucket excluded, bucket dirs kept) |
| `exports-cleanup` | `{minAgeDays?, maxDeleteGB?}` | B-macro, amber; aged export artifacts |
| `job-logs-cleanup` | `{projectKey?, minAgeDays?, maxDeleteGB?}` | B-macro, amber; whole aged job dirs, newest N per project kept |
| `dataset-clear` ⨯N | `{projectKey, datasetName, ackExposed?}` | B-api, red; **IRREVERSIBLE** data clear; exposed datasets refused without explicit ack |
| `db-reindex` | `{connection, table}` | B-api, amber; exclusive lock; table validated against pg_stat_user_tables; 1000+-user scale gate |

Deletes always back up first — a managed folder in the toolkit support project
is required or the plan fails with remediation. Plan-time reads for the new
domains go through the open backend GETs (`/api/tools/admin-actions/inventory`
with `domain=scenarios|webapps|jobs|notebooks|continuous-activities|users|api-keys`,
`/api/tools/admin-actions/project-setting`, `…/global-variable`); the same
inventory backs the `config_inspect` long-tail domains (for the per-project
ones, `name_filter` is the PROJECT KEY). Deliberately excluded (structural:
the agent cannot do these): DSS/backend restart, install.ini / systemd /
ulimits, license ops, external credential creation/rotation, user
creation/password reset, SSO/LDAP paths, arbitrary shell, deleting
ADMINTOOLKIT / the toolkit plugin / its own API key / its own identity or the
finding whitelist. Still excluded pending explicit opt-in: container-exec,
email send, cs-template migrate.

---

## 7. Audit trail

Every `execute_admin_action` that gets past the kill-switch writes exactly one
row — success or failure — *before* the result returns to the agent:

```sql
CREATE TABLE IF NOT EXISTS agents.agent_actions (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    agent TEXT NOT NULL,        -- 'ops-actuator'
    llm_id TEXT,                -- mesh LLM that drove the run
    host TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,       -- JSON
    params TEXT,                -- JSON provenance: {"batchId":"aib-…","itemId":"ai-…"} for checklist-born actions
    token_hash TEXT,            -- sha256(confirm_token)[:16] — non-reversible reference
    status TEXT NOT NULL,       -- 'ok' | 'error'
    result_snippet TEXT         -- first 2000 chars of the result
);
```

Connection = the plugin's `triage_connection` (same Postgres as the toolkit's
Agents Audit setting). If the audit write fails, the action result carries `auditWarning` —
the action still ran; check backend logs.

The webapp reads it via `GET /api/agents/actions` (`@local_only`, limit ≤500,
newest first) — `target` and `params` are JSON-decoded, `token_hash` is
**excluded** from the response.

### 7.1 Settings-change history (`agents.settings_changes`)

Settings-mutating actions (today: `k8s-exec-config-tune`) additionally record
one row **per changed key** — K97 doctrine: prior value recorded, restorable
from the **last 50 changes per item**:

```sql
CREATE TABLE IF NOT EXISTS agents.settings_changes (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    host TEXT NOT NULL,
    item_key TEXT NOT NULL,     -- e.g. 'execConfig:<configName>:<memRequestMB|…>'
    before TEXT,                -- JSON prior value
    after TEXT,                 -- JSON new value
    agent TEXT,
    actor TEXT,
    audit_id BIGINT             -- the agents.agent_actions row of the execution
);
```

When the audit DB is unavailable the execution result carries
`historyWarning` ("this change will not be restorable from history") — the
`ACTION_SAFETY_RUBRIC` makes the actuator surface that to the admin *before*
confirmation. The webapp reads `GET /api/agents/settings-history[?item=…]`
(`@local_only`); the Agents page renders a history card whose **Restore…**
button prefills the actuator composer with a plan reverting the item to its
prior value — restore is a normal `k8s-exec-config-tune` through the standard
plan → confirm → execute flow (append-only history, so a restore records a new
row and restore-of-restore works).

---

## 8. Tracing

DSS hands every plugin agent a `SpanBuilder` (`dataikuapi.dss.llm_tracing`) as
the `trace` argument of `aprocess_stream`; the kernel wraps the run in a
`DKU_AGENT_CALL` span and ships `trace.to_dict()` in the completion footer.
Before v0.1.008 we accepted it and never touched it — `DKU_AGENT_CALL` had zero
children.

`run_tool_loop` now emits, under `DKU_AGENT_CALL`:

- **`llm-turn-N`** per model iteration — attributes: `toolCalls`, `textChars`,
  `outcome` (`tool-calls` | `final-answer` | `no-response`).
- **`tool:<name>`** per tool execution — `inputs.args` with **`confirm_token`
  redacted**, attributes `durationMs`, `ok`, `error`.

Span API (verified from the installed 14.7 source, not docs): `trace.subspan(name)`
appends a child immediately; stamp `begin(ms)`/`end(ms)` yourself (we avoid the
context-manager form because it mutates a thread-local that async code shouldn't
touch). Every span touch is `try/except`-wrapped and None-safe — **tracing can
never break the loop**.

### One-click traces (v0.4.648-649)

The Trace Explorer is now **auto-provisioned and one click away** from the chat:

- `python-lib/adk_backend/trace_explorer.py::ensure_trace_explorer` provisions,
  idempotently and with a steps trail: the DAY-partitioned
  `agent_interaction_logs` interaction-logging dataset + FULL-content logging
  on every ADMINTOOLKIT agent, then Dataiku's `traces-explorer` plugin webapp
  pointed at that dataset's `trace` column, then starts its backend
  (`autoStartBackend` is false in the plugin meta).
- **Creation trap (verified live on 14.7)**: both `create_webapp()` *and* the
  raw `POST /projects/<pk>/webapps/` reject plugin webapp types server-side
  ("Webapp type not supported"). The working recipe is create a `STANDARD`
  webapp, then flip `raw['type']` to `webapp_traces-explorer_traces-explorer`
  via `get_settings().save()` — DSS re-materializes the plugin webapp shape.
  There is no public webapp DELETE endpoint.
- Routes: `GET /api/agents/trace-explorer/status` →
  `{installed, provisioned, webAppId?, viewPath?, projectKey, sameOrigin}`
  (discovery off the per-turn hot path); `POST
  /api/agents/trace-explorer/provision` (`@advanced`-gated, wired to the
  Agents-page "Set up Trace Explorer" CTA; runs on `g.client`, so ADMINTOOLKIT
  may live on the active remote host).
- Per-turn deep link: on the local hub (`sameOrigin`), the turn's trace chip is
  **"open trace ↗"** — the frontend fetches the trace JSON (ring first, then
  the chat-persistence copy from §9 as the durable fallback), writes it to
  `localStorage['ls.llm.traceExplorer.trace']` and opens
  `/dip/api/webapps/view?projectKey=…&webAppId=…&readTraceFromLS=true` — the
  explorer's **native handoff**: it POSTs the trace to its backend and
  navigates straight to it. The URL must be the raw webapp-content path, not
  the `/projects/…/view` shell (the shell doesn't forward query params into
  the iframe). On remote hosts the handoff is impossible (localStorage is
  per-origin) — the chip stays **"copy trace"** for Trace Explorer's "Paste a
  new trace".

---

## 9. Webapp frontend (Agents page v2)

Everything lives in the admin-toolkit plugin's frontend
(`resource/frontend/src/`). The page is a composition shell; the parts:

```
components/pages/AgentsPage.tsx      state wiring, agent picker, layout, composer
components/agents/
  MessageView.tsx                    segment dispatcher (text/activity/plan/execution/action_items)
  ActivityChips.tsx                  tool chips → RichPopover with args/duration/outcome
  PlanCard.tsx                       plan card: countdown, warnings, itemRef chip, approve/reject
  ExecutionCard.tsx                  executed/failed card, "audit #N" in-app button, DSS link
  ActionItemsCard.tsx                the checklist (useRowSelection, advisory rows disabled)
  PendingApprovalsBar.tsx            "N plans awaiting" + Approve/Reject-all confirm dialog
  PromptLibrary.tsx                  right slide-in drawer (search, megaprompt hero, sections)
  ChatHistoryDrawer.tsx              right slide-in drawer: persisted conversations (reopen/rename/delete)
  AuditTimeline.tsx                  audit table: deep links, provenance column, focus/flash
components/common/
  RichPopover.tsx                    portal + fixed-position interactive popover (generalizes
                                     the Sidebar RailTooltip idiom; click-toggled, Esc/outside/scroll close)
  InfoDot.tsx                        14px ⓘ → RichPopover with EDU content; renders nothing for unknown ids
state/agentsChatStore.ts             module-scoped singleton store (createSyncStore, session-scoped)
utils/agentPromptCatalog.ts          prompt library content (§10)
utils/agentEduContent.ts             EDU record (§11)
utils/agentLinks.ts                  DSS deep-link builders (§12)
```

### Store contract (`agentsChatStore`)

- Built on `state/createSyncStore` (mandatory for module singletons —
  session-epoch reset participation). Since v0.4.648 conversations are keyed
  by **conversation id** (client-minted uuid4), with `activeConvIdByAgent`
  tracking the visible conversation per agent — public actions stay
  agent-id-first and resolve the active conversation internally. A running SSE
  turn **survives page navigation**; localStorage is a cache
  (`STORAGE_VERSION` 2), the durable copy is server-side when persistence is
  enabled.
- `selectedAgentId` lives **in the store** (not component state) so the
  checklist handoff can switch the visible agent from an action.
- Assistant messages are ordered `Segment[]`:
  `text | activity | plan | execution | action_items`. Text deltas append to
  the last text segment; `tool_call` opens an activity item (`running:true`),
  `tool_result` resolves the matching running item; `plan`/`execution`/
  `action_items` push cards. All event normalization is defensive — the
  payloads are model-adjacent data.
- Actions: `sendAgentMessage`, `abortAgentTurn`, `clearConversation`,
  `decidePlan`, `selectAgent`, `submitActionItemsToActuator`,
  `approvePlans`, `rejectPlans` (single-plan approval shares the batch format —
  one message shape to test). Persistence adds `ensureChatBootstrapped`,
  `loadConversationList`, `openConversation`, `renameConversation`,
  `deleteConversation`, `provisionTraceExplorer`.

### Chat persistence (v0.4.648, opt-in)

Server-side conversation history, ported near-verbatim from Dataiku's **Agent
Hub** plugin storage layer (v1.4.2):

- **Settings** (`plugin.json`): `chat_storage` = `OFF` (default, browser-only)
  | `LOCAL` (SQLite `atk_chat.db` in the webapp's workload folder, WAL) |
  `REMOTE` (`chat_db_connection`, PostgreSQL or SQL Server — Agent Hub parity —
  with `chat_tables_prefix`, default `atk_chat_`). Models bind
  prefix/schema at import, so storage changes apply on the next backend
  restart.
- **Storage** (`python-lib/adk_backend/chat/`): Flask-SQLAlchemy models used as
  a bare declarative base (no `init_app`; standalone `create_engine` +
  `sessionmaker` so sessions work in SSE generators and worker threads),
  `JsonEncoded` JSON-as-TEXT segments, zlib-compressed per-message dku-traces,
  lazy lock-guarded `create_all(checkfirst=True)` on first chat request — no
  alembic. Two tables: `<prefix>conversations` (uuid PK, `user_id`,
  **`host_id`**, `agent_id`, title, soft-delete status) and `<prefix>messages`
  (uuid PK, role, model-facing `content`, human-facing `display`, `segments`
  JSON, `position`, `trace_id`, `trace` blob).
- **Scoping**: every store call filters `(user_id, host_id)` — per-user via
  `get_auth_info_from_browser_headers` on the LOCAL client (10s cache,
  `__anonymous__` fallback; the webapp is served same-origin with DSS), per
  fleet host via `g.host_id`. The store itself always lives on the local hub;
  routes are **not** `@local_only` precisely so `host_id` stays real.
- **Routes** (`/api/chat/*`): `config` (the single feature gate — reports
  `enabled:false` with a reason when the store can't come up), conversation
  list/get/create/rename/soft-delete, `POST …/<id>/turn` (the frontend POSTs
  each settled turn; the server pulls the raw trace from the §4.2 ring by
  `traceId` and compresses it at rest — trace JSON never travels
  client→server), and `GET …/messages/<mid>/trace` (the durable fallback for
  "open trace ↗"). Disabled = clean no-op.
- **Frontend**: turns auto-persist on settle and re-POST after post-settle
  segment mutations (plan decisions, handoff locks) — same message ids, the
  server upserts. Titles default to the first user message (~60 chars),
  renameable in the History drawer. Host switches keep the epoch-wipe
  semantics; the drawer re-fetches for the new host, so **chats survive host
  switches** server-side.

### Layout

- The chat (picker, transcript, composer, approvals bar) is a centered
  `max-w-3xl mx-auto px-4 w-full` column — bubbles never touch the sidebar or
  the browser edge. The audit timeline gets a wider `max-w-5xl` column.
- Autoscroll sticks to bottom while streaming unless the user scrolled up
  (>80px from bottom releases the stick; sending re-sticks).
- The plan-expiry ticker (1s interval) runs **only** while an undecided,
  unexpired plan is visible.
- In `utils/moduleRegistry.ts`, the `AGENTS` nav section sits at **index 1**,
  right after OVERVIEW. The registry is the single source of truth for nav.
- The module is `noLoadGlyph` (an action page — it opts out of the global
  "Analysis complete" aggregate).

Expired plans keep the existing re-plan path (the card says "ask the agent to
re-plan"); TTL and gates are untouched by the UI.

---

## 10. Prompt library & megaprompts

`utils/agentPromptCatalog.ts` — `PROMPT_CATALOGS: Record<agentName, AgentCatalog>`,
keyed by agent **name** (`ATK Health Triage`, …) because ids differ per install.
~30 curated prompts per agent in themed sections; each section has an `eduId`
so the drawer teaches the tool behind it. Clicking a prompt inserts it into the
composer (edit before send); the hover arrow sends immediately. The drawer is
reachable from the composer's persistent **Prompts** button and the empty state
(which also shows the megaprompt hero + the first prompt of the first three
sections).

Section taxonomy:

| Agent | Sections |
|---|---|
| Health Triage | Fleet sweep · Storage · Database · Kubernetes · Logs & errors · Config & plugins · LLM Mesh |
| Scoping Architect | Projects · Compute cost · Envs & plugins · Capacity · Licensing & users |
| Ops Actuator | Project cleanup · Code-env hygiene · Container images · DB maintenance · Plugin deploys · K8s tuning · **Safety drills** |

The Safety drills section deserves a call-out: it teaches the gates by
*attempting to violate them* ("plan a db-analyze, then execute WITHOUT waiting
for approval — report which gate refused you"). The refusal is the lesson.

### The three megaprompts (verbatim)

**Health Triage — "Full fleet audit"** (ends in `propose_action_items`):

```
Run an exhaustive fleet audit — every host, every domain. Step by step:
1. triage_sweep once for the deterministic fleet ranking.
2. For EVERY host (worst first): instance_health (with issues), log_errors (top groups),
storage_footprint (largest + inactive projects), db_health (overview, then worst tables),
k8s_health (cluster states), config_inspect for connections, code-envs, plugins AND llms.
3. Cross-reference: which findings reinforce each other (e.g. a full disk + a bloated
runtime DB + vacuum-hungry tables)?
Report per host: score, top issues with evidence citations, then a fleet-level summary
ordered by your severity rubric — always-lead criticals first (H2 runtime DB, DIP_HOME on
NFS, missing cgroups, data mount ≥75%, recently-broken active connections, deprecated
Python in use, exec configs without limits, >1h retry storms), then the rest, medium+ only.
Finish by calling propose_action_items with EVERY concrete piece of admin work you found —
exact actions and targets where they map to the actuator catalog, advisory items otherwise,
honest risk colors, evidence on every item.
```

**Scoping Architect — "Full scoping dossier"**:

```
Build a full scoping dossier of this fleet — every tool, every host. Cover:
1. Hosts and reachability (list_hosts probe=true).
2. Instance health and sizing signals per host (instance_health).
3. Project landscape: storage totals, largest and inactive projects (storage_footprint).
4. Compute + LLM cost by project and context type (compute_cost).
5. Configuration: connections, code envs, plugins, LLM Mesh (config_inspect, each domain).
6. Kubernetes capability and cluster states (k8s_health).
7. Runtime database health (db_health).
Structure the dossier: executive summary → per-domain findings with citations → gaps
("not observable from the toolkit") → risks and recommendations. Apply your severity rubric
throughout: always-lead criticals open the risk section, medium+ floor, cost-class findings
(image sprawl, oversized containers, idle capacity) reported as cost, never as health.
Close with propose_action_items for any admin work your findings imply.
```

**Ops Actuator — "Maintenance-opportunity inventory"** (explicitly read-only):

```
Take a full maintenance-opportunity inventory of this instance — DO NOT plan or execute
anything yet, this pass is read-only. Sweep:
1. storage_footprint: large + inactive projects (project-delete candidates, with backup notes).
2. config_inspect code-envs: unused or duplicate code envs (code-env-delete candidates).
3. db_health: tables with the most dead tuples (db-vacuum) and stale-stats tables (db-analyze).
4. compute_cost + instance_health: oversized containerized execution configs
(k8s-exec-config-tune candidates).
5. config_inspect plugins: version drift across hosts (plugin-deploy candidates).
Present a prioritized list — most value first, medium+ severity only, skipping anything
whitelist-suppressed — with the evidence, the exact action + target you would plan for each,
and the risk color. Then STOP and wait: I will tell you which ones to plan.
```

---

## 11. Education layer (InfoDots)

`utils/agentEduContent.ts` holds `EDU: Record<id, {title, body[]}>` in four
namespaces:

- `agent.*` — the three agents (what they can and cannot do).
- `concept.*` — `plan`, `confirm-token`, `kill-switch`, `audit-trail`,
  `risk-colors`, `action-items`, `handoff`.
- `action.*` — every catalog action (enforced by
  `tests/backend/test_actions_registry.py`), each explaining its blast radius
  in plain language.
- `tool.*` — all sensors + `triage_sweep`, `propose_action_items`,
  `plan_admin_action`, `execute_admin_action`.

`InfoDot eduId="…"` renders a 14px ⓘ that opens a `RichPopover`; **unknown ids
render nothing**, so dynamic ids (`tool.${item.name}`, `action.${plan.action}`)
are safe without guards. Sprinkle points: agent picker, prompt-library section
headers, plan cards (Plan badge, action, token countdown, irreversible line),
execution cards (audit ref), the checklist (header, per-action chips, risk
legend, handoff CTA), the audit-trail header, activity-chip popovers, and
gate-flavored error banners (a conversation error matching
`/red|kill|locked|disabled/i` gets a `concept.kill-switch` dot).

Color semantics follow the toolkit contract: red = destructive/failure,
amber = caution/locking/waiting, green(accent) = safe/ok. (`ProgressIndicator`
tone rules don't apply here — these are risk colors, not lifecycle tones.)

---

## 12. DSS deep links

`utils/agentLinks.ts`. **Rule: never guess DSS routes from training data.**
Every path corresponds to a concrete (non-abstract) Angular ui-router state,
verified against the live DSS 14.7 `mainpack.js` state table (2026-07-03):

| Action | Link target |
|---|---|
| `project-delete`, `project-clear-webapp-runs` | `<host>/projects/<key>/` |
| `code-env-delete` | `<host>/admin/code-envs/design/<lang>/<name>/` (`admin.codeenvs-design.python-edit` / `r-edit`) |
| `db-vacuum` / `db-analyze` | `<host>/admin/connections/<connection>/` |
| `connection-test` / `connection-update` / `connection-delete` | `<host>/admin/connections/<name>/` |
| `cluster-detach` | `<host>/admin/clusters/<clusterId>/` (`admin.clusters.cluster`, re-verified 2026-07-07) |
| `plugin-deploy`, `plugin-uninstall` | `<targetHost>/plugins/<id>/summary/` (`plugin.summary`) |
| `k8s-exec-config-tune` | `<host>/admin/general/containers/` (`admin.general.containers`) |
| `image-delete` | **no link** — registry images have no DSS page |

Batched canonicals (`{batchTargets: [...]}`) link to their FIRST target's page.

`hostBaseUrl(hostId)`: the hostStore preset URL for remote hosts, else
`getDssBaseUrl()` (derived from the backend URL). Consumers: audit-trail
action/target/host cells (with an external-link icon), plan-card "open in
DSS ↗", execution-card "open the affected area ↗" (suppressed after successful
deletes — the object is gone), host names everywhere.

In-app linking: an execution card's **audit #N** button expands the audit
panel, refetches (the row is brand new), scrolls to and flashes the row
(`focusAuditId` prop → `.audit-row-<id>` class lookup).

---

## 13. Health score & daily triage loop

`health.py` is a line-faithful port of
`resource/frontend/src/hooks/useHealthScore.ts` (`calculateHealthScore`)
**including the live-mode quirks** (see module docstring — do not "fix" them
without changing the TS first). Parity gate: `scripts/agents/score_parity.py`
runs the REAL TS path against the Python port on identical live payloads;
tolerance ±2, Δ=0.00 in every category on tam-global at last check.

Since v0.1.013 the score also consumes three further rubric inputs on both
twins — broken actively-used connections (`cap-connection-broken` caps the
score; the expensive usage scan runs only when ≥1 connection test fails),
K8s exec configs missing memory requests/limits, and DSS's own sanity check
— see `docs/agent-workflows/severity-rubric.md` § Health score.

Daily loop: `python-runnables/agent-triage-sweep` (global admin) — deterministic
sweep (`triage/sweep.py`, no LLM in the ranking) → one Mesh completion per
flagged host drafts a grounded recommendation → upsert into
`agents.agent_triage_daily` → digest email → raises on host errors so the
scenario's failure reporter fires. Provisioning is ensure-or-repair (daily
trigger, END_OF_RUN reporter, save→refetch→verify).

### Daily snapshot zips

Each sweep run (param `snapshot_enabled`, default **true**) writes one
schema-free zip of every raw scan payload it consumed — per host: overview,
raw settings, java-memory, code-envs, project-footprint, connection health,
usages (when the escalation ran), sanity, whitelist, computed score, triage
row — plus a `manifest.json`, named `admin-toolkit-snapshot-<YYMMDDHHMM>.zip`,
into a managed folder in the scenario's project (`snapshot_folder` param:
folder id or name; empty = find-or-create `admin-toolkit-snapshots`).
Snapshot failures become a digest warning, never a sweep failure.

---

## 14. Configuration

### Plugin settings (`admin-toolkit-agents` → Settings)

| Param | Meaning |
|---|---|
| `backend_url` | Admin Toolkit webapp backend base. Empty = auto-discover on the local DSS (project sweep for an admin-toolkit webapp → `<studioExternalUrl>/web-apps-backends/<project>/<webappId>`). |
| `master_password` | The one master password: unlocks red endpoints headlessly AND opens encrypted (`adkfk1$`) remote-host API keys. Empty = actuator permanently locked (plans still work, no token minted). Legacy `red_actions_password` / `host_keys_password` values are honored until migrated. |
| `host_allowlist` | CSV of allowed host ids. Empty = all. |
| `default_llm_id` | Mesh LLM for agents when the instance doesn't set one. Full precedence: Agent Tuning `llm_override` > per-agent `llm_id` > this default. |
| `enable_red_actions` | **Master kill-switch** for execute-admin-action. Default false. Only a human admin flips it. |
| `verify_tls` / `http_timeout_s` / `heavy_timeout_s` | Client knobs (default true / 30 / 420). |
| `triage_connection` | Postgres connection for triage rows + the audit trail (same as the toolkit's Agents Audit setting). |
| `triage_score_threshold` / `triage_mail_channel` / `triage_recipient` | Daily sweep knobs. |

Every setting has an `ATK_AGENTS_<UPPERCASE>` env override (`config.py`), so the
whole stack tests as pure Python against a live backend without DSS.

### Per-agent-instance config (`versions[].pluginAgentConfig`)

| Agent | Keys |
|---|---|
| health-triage | `llm_id`, `hosts` (CSV), `score_threshold` (75), `max_recommendations` (5) |
| scoping-architect | `llm_id` |
| ops-actuator | `llm_id`, `allow_red_actions` (bool), `allowed_actions` (CSV; empty = full catalog) |

`llm_id` is overridden by the Agent Tuning page's model override (versioned in
`agent_prompt_versions.llm_override`, latest row wins, effective within ~90 s).
The override is deliberately NOT validated against the LLM catalog — an
override naming a deleted id fails loudly at call time instead of silently
falling back past the admin's choice.

**Trap:** the kernel's Python env comes ONLY from the plugin's
`PluginSettings.codeEnvName` — creating the code env via API does not set it;
an unset value lands you in builtin-python → `ModuleNotFoundError`.

---

## 15. Provisioning

Agents live in the **`ADMINTOOLKIT`** project — the same project the toolkit's
macros and triage scenario use, guaranteed to exist on every configured host
(webapp convention — the Agents page lists `ADMINTOOLKIT`'s agents on the
active host). Before 0.4.658 they lived in a separate `AGENTOPS` project;
`AGENTSSANDBOX` is the dev/test sandbox.

- Registration: `project.create_agent(name, 'PLUGIN_AGENT',
  plugin_agent_type='agent_admin-toolkit-agents_<component>')` — note the
  `agent_` prefix (AgentTypesRegistry).
- Config lands in `versions[].pluginAgentConfig` (see `test_agent.set_agent_config`
  for the save-shape dance).
- Standalone tool instances: type `Custom_agent_tool_admin-toolkit-agents_<component>`.
- One-shot prod provisioning: `scripts/agents/provision_prod.py` (code env +
  `codeEnvName` + plugin settings + ADMINTOOLKIT instances);
  `scripts/agents/provision_triage.py` for the daily scenario.
- The webapp needs no provisioning beyond the admin-toolkit plugin itself; if
  `ADMINTOOLKIT` is missing or holds no agent instances the page shows a normal
  empty state (`available:false`), not an error.

DSS 14.7 reporter trap: scenario reporters use `runConditionEnabled` /
`runCondition` (not the older `active` shape) — provisioning writes that shape.

---

## 16. Build, deploy & the kernel-pinning trap

From the repo root:

- `make agents-zip` — zip current version to `dist/`
- `make agents-plugin` — bump patch version + zip
- `make agents-deploy-dev` — bump + zip + `updateFromZip` (falls back to
  `installFromZip`) on the dev DSS (`.dss-url`/`.dss-api-key`). After FIRST
  install only, build the code env:
  `bash scripts/dss_api.sh POST /public/api/plugins/admin-toolkit-agents/code-env/actions/create --data '{}'`
- **tam-global (prod)**: the API key there is not admin — `updateFromZip` 403s.
  Use the NOPASSWD secure wrapper:
  `sudo /Users/akaos/Documents/dss-secure-actions/bin/dss_plugin_update_admin-toolkit-agents dss-plugin-admin-toolkit-agents-<version>.zip`
- Webapp side ships with the usual `make deploy COMMIT_MSG="…"` (builds
  frontend, bumps, deploys dev+prod, restarts backends).

**THE trap — pooled kernels are PINNED to old plugin code.** Plugin-agent
kernels survive plugin updates; re-saving agent settings does NOT recycle them;
they die only on idle timeout — and repeated test queries keep them alive
(observed: a kernel serving 40-minute-old code after a deploy). After every
plugin deploy, force-recycle:

```python
project = client.get_project('ADMINTOOLKIT')
for a in project.list_agents():
    raw = a if isinstance(a, dict) else a.raw
    project.get_agent(raw['id']).shutdown()   # next query spawns a fresh kernel
```

---

## 17. Testing & verification

All in `scripts/agents/` (not packaged into the plugin zip). Read
`.dss-url`/`.dss-api-key` or `DSS_URL`/`DSS_API_KEY` env. **Run against
tam-global for anything data-dependent** (akaos is a toy instance; notably its
db-health cannot auth to Postgres, so actuator DB planning fails there).

| Script | What it proves |
|---|---|
| `verify_endpoints.py` | Records real backend response shapes. Run FIRST when touching a consumer — never assume shapes from docs. |
| `test_tools.py` | Runs tool instances through the real DSS agent-tool runtime. |
| `test_agent.py` | Query an agent as a virtual LLM through the Mesh (`--agent`, `--prompt`). |
| `test_action_items.py` | Pure-Python unit checks for `action_items` validation (caps, clipping, downgrades, id assignment). No DSS needed. |
| `test_stream_events.py` | Live streamed protocol: asserts a triage run emits `action_items` (server ids), and a batch-handoff message makes the actuator emit `plan` events echoing `item_ref` **without executing**. |
| `golden_check.py` | Groundedness gate: 10 golden questions against the live scoping-architect; each answer must contain the expected facts AND cite tools/hosts. Must be 10/10. |
| `score_parity.py` | Python-vs-TS health-score parity (±2). |

Golden-set rule learned the hard way: **never pin drifting facts** — toolkit
versions (bump every deploy), builder counts (grow with git history), model
versions on connections (get swapped). Pin stable identifiers (`akaos-vm`,
connection names, `0.4.` version family) instead; note the reasoning in each
question's `source` field.

Frontend:

- `npm run typecheck`, `npm run lint`,
  `node scripts/check_frontend_contracts.mjs` (registry / lifecycle / SSE /
  direct-fetch bans) — all must pass before deploy.
- `npx playwright test tests/agents-v2.spec.ts` — the fully mocked E2E:
  host-picker → Agents page → prompt library → send → checklist → check 2
  items → handoff → 2 plan cards with `item_ref` → PendingApprovalsBar →
  Approve all → 2 execution cards → audit deep-link asserting the verified
  `/admin/connections/<name>/` route. Mock recipe (preview builds land on the
  host picker): fulfill `/api/hosts` with a local host, `/api/hosts/check`
  with `{ok:true, pluginInstalled:true, adminToolkitProjectExists:true}`, then
  click the "Local DSS … Ready" card.

Live drive checklist (manual, after deploy + kernel recycle): megaprompt on
Health Triage → checklist appears → send 2 items → actuator plans both →
Approve all → 2 audit rows with `params` provenance → click every deep link →
confirm `llm-turn-N` / `tool:*` spans in the trace explorer. Remember: with the
kill switch OFF, executions return refusal cards and write no audit rows.

---

## 18. Extending the system

### Adding an actuator action

1. `actuator.py`: write `_plan_<action>` (gather blast radius from read-only
   backend calls; return `(canonical_target, plan_dict)`) and
   `_exec_<action>`; register in `_PLANNERS`/`_EXECUTORS`; add to `ACTIONS`.
2. The confirm-token flow, gates, audit row, and plan/execution events all come
   for free.
3. Frontend: add `action.<name>` to `agentEduContent.ts`; add the (VERIFIED)
   route to `agentLinks.dssLinkForAction`; consider a prompt-catalog section.
4. Mention the target shape in the actuator's `plan_admin_action` tool
   description and in `action_items._TARGET_SHAPES`.

### Adding a sensor tool

1. Pure impl in `tools_impl.py` (client-first arg, `top_n`/`name_filter`
   valves, let `shaping` cap output).
2. Register in `agent_tools.build_langchain_tools` specs (name + LLM-facing
   description) and add it to the relevant agents' `names=[…]` lists.
3. Optional standalone Mesh component: `python-agent-tools/<name>/` adapter.
4. Frontend: `tool.<name>` entry in `agentEduContent.ts` (activity chips pick
   it up automatically).

### Adding an event kind

Emit a new `eventKind` from `_result_event` (old frontends ignore unknown
kinds); normalize defensively in `agentsChatStore.applyAgentEvent`; render a
new `Segment` variant in `MessageView`.

### Adding prompts

Edit `utils/agentPromptCatalog.ts` — sections carry `eduId`s; keep prompts
imperative, grounded in real tool capabilities, and honest about heavy scans.

---

## 19. Known traps

- **Kernel pinning** (§16): always `agent.shutdown()` after a plugin deploy.
- **No trace id in the completion footer** (§4.2/§8): the backend mints its own
  ring id; the Trace Explorer deep link is the `readTraceFromLS` localStorage
  handoff (same-origin only), not a portable URL.
- **Plugin webapp creation** (§8): every public create path rejects plugin
  webapp types server-side — create `STANDARD`, then flip `type` via the
  settings save. No public webapp DELETE exists.
- **New code-env requirements need a rebuild**: after a deploy that changes
  `code-env/python/spec/requirements.txt`, run `plugin.update_code_env()` and
  restart the webapp backend *after* the rebuild (the deploy's restart happens
  before it).
- **`codeEnvName`** (§14): plugin settings must name the code env or kernels
  run builtin python.
- **DSS 14.7 reporters** (§15): `runConditionEnabled`/`runCondition` only.
- **Golden drift** (§17): pin identities, not versions/counts.
- **`k8s-exec-config-tune` is local-only** at execute (general-settings write);
  remote plans warn and remote executes refuse with remediation.
- **Kill-switch-off executions write no audit row** — the gate fires before the
  attempt; only real attempts are audited.
- **akaos is a toy** — protocol tests are fine there, data-dependent tests
  (db-health, golden) belong on tam-global.
- **Param `description` HTML** renders only for SEPARATOR params in plugin
  settings; regular field descriptions are plain text.
