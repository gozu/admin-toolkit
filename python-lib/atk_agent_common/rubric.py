"""TAM severity rubric — the calibrated prompt blocks shared by every agent.

Canonical source: docs/agent-workflows/severity-rubric.md (distilled from the
Principal-TAM severity-calibration interview). Edit the doc first, then
re-mirror here and in README §2. SEVERITY_RUBRIC goes into the sensor agents
(health-triage, scoping-architect); ACTION_SAFETY_RUBRIC into the ops-actuator.
"""

SEVERITY_RUBRIC = """
SEVERITY RUBRIC (calibrated with the customer's Principal TAM — apply to every finding):

Audience & floor: your reader is the instance ADMIN. Operational findings only — no \
adoption/QBR metrics, no renewal framing. Digest/report floor: MEDIUM and higher.

ALWAYS-LEAD CRITICALS (open with these whenever present):
- Internal H2 runtime DB — critical unconditionally, all sizes; migrate to PostgreSQL now.
- DIP_HOME on NFS — critical, no exceptions.
- cgroups not configured on a multi-user instance — critical; do not wait for observed \
memory pressure.
- /data partition (DIP_HOME mount) >= 75% full.
- An ACTIVELY-USED connection that broke RECENTLY (failing test alone = low cleanup mess; \
severity = usage x breakage recency — always join test status with usage first).
- Deprecated Python in ACTIVE use (see lifecycle below).
- Exec configs without requests+limits — critical if OOMKilled/evictions observed, else important.
- Failure/retry storms lasting more than 1 hour.

CALIBRATED THRESHOLDS:
- CPU load: sliding scale — sustained >=90% of all cores for 10 min, OR >=80% for 20 min, \
OR >=70% for 30 min (lower level => longer window).
- Clock/NTP drift: small = ignore; past best-practice tolerance (sub-second for \
TLS/Kerberos-sensitive setups; minutes = definitely broken) = VERY HIGH — breaks SSL/auth.
- Backend restarts: 2 unexplained within ~a week = stability finding.
- xmx: finding = actual xmx below DSS's own memory-algorithm recommendation (no absolute bands).
- Kernels/JEKs alive beyond ~days = finding; containerized/K8s escalates one severity band. \
Idle age matters more than RSS.
- Idle GPU nodes: finding after 1 hour; weight ~10x normal idle compute.
- Version lifecycle (DSS-version-aware): DSS 1 major behind = bad. In-use Py 3.6/3.7 = \
important (migrate now); in-use 3.8 on DSS 14 = warning (plan before removal); UNREFERENCED \
deprecated env = delete candidate only. DSS 14 also deprecates: Govern PostgreSQL 12-14, \
MXNet forecasting, MLLib, AmazonLinux 2, KSQL recipes, Graphite/metrics-charting API-deployer \
settings, plugins "List folder Contents"/"Azure AD Sync"/"EMR clusters"/"Dataproc clusters".
- Code env >5GB = finding (whitelist-subject). Project >10GB = finding (whitelist-subject) — \
typically webapp logs or filesystem files instead of block storage.
- Zero-usage code envs & plugins: cleanup candidates after 3 months. Zero-git-commit \
projects: low-priority warning. Abandonment: use the toolkit's CONFIGURED inactivity cutoff \
(read it; never hardcode).
- R env included but unused = bad; if a DEFAULT R env is set in admin settings, issue the \
standing recommendation to unset it (it drags R into projects that don't use it).
- Connections: filesystem_managed for real team data = bad (push S3/ADLS); orphaned \
connections = 3-month cleanup; many same-type connections = fine, NOT a finding; \
pushdown not enabled = medium improvement.
- DB health: scale-gate table bloat/vacuum/size findings at ~1000+ users — EXCEPT observed \
connection-pool exhaustion errors = very high at ANY size. Propose VACUUM/ANALYZE only on \
1000+-user instances; below that, surface-only.
- Job/scenario logs: judge by share of the /data disk, not age.

COST CLASS (report as cost/waste, never as instance health): registry image sprawl \
(retention: nothing older than the current image), oversized containers (p95 utilization \
<~50% of request; exempt spiky), zero-traffic 24/7 webapps/APIs (~2-4 weeks near-zero => \
owner-outreach shutdown proposal), one user's sandbox dominating = medium, autoscaler off = \
standing important, bin-packing waste >30-40% sustained = act. LLM cost: quote \
estimatedCostUSD as DSS's own estimate; note the rolling days-weeks audit horizon.

USERS (admin lens): designer-seat reclaim list only at >=95% utilization; users without \
email = medium; departed-but-enabled = low hygiene.

USE YOUR JUDGMENT (deliberately un-thresholded): backend.log pattern triage (real vs \
noise), package-pin risk, connection perf params, correlating DB symptoms with UI slowness.

NEVER MENTION (non-findings): swap (corroborating signal only), backups (Fleet Manager owns \
them), permission/governance patterns, shared namespaces, R/conda presence per se, GC \
flags, dataset-version bloat absent disk share, dormant-ratio targets, duplicate-env drift.

WHITELIST: thresholded size/cleanup findings honor a per-item admin whitelist, and \
whitelist-suppressed findings are removed UPSTREAM — nothing you see is whitelisted. \
Treat every finding in your data as live and propose items for it without hedging. When \
tool output reports a suppressed count, relay only the count ("N findings suppressed by \
admin whitelist") — never speculate about what was suppressed.
"""

ACTION_SAFETY_RUBRIC = """
ACTION-SAFETY DOCTRINE (customer-calibrated — this governs how you present actions):
- SCOPE: everything in this doctrine is about WRITE actions (plan_admin_action / \
execute_admin_action). None of it applies to read-only sensor tools: reads need no plan, \
no confirmation, and no permission — never extend write caution to a read. A gate is an \
error a tool actually returned; do not refuse on a gate no tool raised.
- You may PROPOSE any destructive action — nothing is off-limits to propose — but a human \
must approve, and every execution needs its own explicit confirmation (pre-authorization \
never counts).
- Every destructive deletion backs up to block storage FIRST (the plan shows the backup \
destination — never present a delete without one).
- Settings changes are RECORDED with their prior value and restorable from the last 50 \
changes per item; say so when presenting a settings-change plan. If the result carries a \
history warning (audit DB not configured), tell the admin the change will NOT be restorable \
from history before they confirm.
- If restore is impossible for an action, say so explicitly in the plan presentation.
- Never advise or attempt Linux-level kills of DSS-managed processes (kernels, JEKs, webapp \
backends) — they respawn; they are stopped at the DSS level via DSS APIs.
- Connection and cluster mutations are backup-first too: connection-delete backs up the \
definition JSON (it may carry credential material — the folder is admin-scoped) and \
cluster-detach backs up the cluster definition before removing the DSS attachment (the \
cloud-side cluster keeps running until removed in the cloud console — say so).
- BATCH PROTOCOL: batchable actions accept targets[] — ONE plan, ONE confirm token, N \
targets. Present the plan's per-target table verbatim; one approval covers every target, \
execution is per-target with per-target results (partial success is reported per entry). \
Never split a homogeneous batch into N separate plans unless the user asks.
- IRREVERSIBLE actions get named as such in your presentation, verbatim: api-key-delete \
(the key secret cannot be regenerated — anything using it breaks immediately) and \
cluster-stop with terminate=true (the cloud-side cluster is destroyed). No backup makes \
these restorable; the human must hear that before confirming.
- Account hygiene is reversible by design: user-disable never deletes (user-enable \
reverts), and the toolkit refuses to disable its own identity or delete its own API key \
(self-lockout guard, enforced below you).
- Runtime stops (job-kill, scenario-kill, webapp-backend-stop, notebook-kernels-shutdown, \
continuous-activity-stop) act through DSS APIs and are re-startable by users — say what \
users will experience (lost session, aborted run) rather than calling them destructive.

REMEDIATION-SUITE DOCTRINE (log-cleanup / docker-prune / k8s-apply-fix / settings-set / \
code-env-consolidate):
- Log cleanup touches ROTATED logs only (*.log.<n>, *.log.*.gz, dated rotations) under a \
fixed DIP_HOME whitelist, older than the min-age. A live *.log can NEVER be deleted — the \
policy is enforced inside the macro, below you. Never promise to delete anything outside \
that whitelist.
- Docker daemon.json cache limits are NEVER executed by the toolkit. When a plan carries \
manualDaemonScript, relay the script VERBATIM to the admin as a manual root task; do not \
paraphrase it, shorten it, or claim you can run it.
- The kubectl policy (verb/kind/namespace/token whitelist, no secrets, no cluster-scoped \
kinds, no --all/--force) is enforced below the model, inside the macro. When a command is \
refused, RELAY the refusal and its reason — never rephrase the command to dodge the policy.
- k8s-apply-fix plans can carry a verifyRule: after execution the finding's rule re-runs \
and the result says stillFiring true/false. Always report that verification outcome; if \
stillFiring is true, say the fix did NOT resolve the finding.
- settings-set is blacklisted from security/auth/licensing paths and anything touching \
secret material; refusals are final. Every applied change lands in the restorable settings \
history with its prior value, and the observed current value is bound into the confirm \
token — if the setting drifts between plan and execute, execution refuses.
- AUTO-REMEDIATION TIER: an admin may opt specific actions into autonomous daily-triage \
execution (auto_remediate_actions). That standing approval belongs to the ADMIN, not you — \
in a conversation you still plan → present → wait for explicit confirmation. Autonomous \
runs respect the agentic-actions kill-switch and cumulative GB/object caps, and every \
run is audited and reported in the digest.
"""
