# TAM Severity Rubric (canonical)

The calibrated severity rubric for everything the Admin Toolkit surfaces,
distilled from the Principal-TAM severity-calibration interview
(`tam-interview-questions.txt`, ANSWERS LOG — corrections applied). This file
is the single source the agent prompts quote from:
`agents-plugin/python-lib/atk_agent_common/rubric.py` embeds it as
`SEVERITY_RUBRIC` / `ACTION_SAFETY_RUBRIC`. Edit HERE first, then re-mirror
into rubric.py and the README.

## Audience & digest doctrine

- The audience is the instance **ADMIN** — operational findings only. No
  account/renewal framing, no QBR material.
- Daily digest severity floor: **medium and higher**. Adoption/engagement
  metrics are excluded from the digest (report/QBR material only).
- When any always-lead finding is present, it opens the digest.

## Hard severity rules

### Critical — always leads the digest when present
- **Internal H2 runtime DB** — unconditional, all instance sizes. "Must
  upgrade immediately to stop running a slow DSS unnecessarily."
- **DIP_HOME on NFS** — very very bad, no filer exception.
- **cgroups not configured** on a multi-user instance — preventable runaway
  kernels/jobs take the host down "for no good reason"; do not wait for
  observed memory pressure.
- **/data partition (DIP_HOME data mount) ≥75% full.**
- **Actively-used connection broken recently** — failing test alone is low
  mess; severity = usage × breakage recency. Join test status with usage
  before scoring.
- **Deprecated Python in active use** — see version lifecycle below.
- **Exec configs without requests+limits** — critical if the cluster shows
  OOMKilled/evictions, else important (same preventable-blowup class as
  missing cgroups).
- **Failure/retry storms lasting >1 hour** — log-spamming loops.

### Host & process
- **CPU load ladder** (sliding scale — lower sustained level ⇒ longer
  window): sustained ≥90% of all cores for 10 min, OR ≥80% for 20 min, OR
  ≥70% for 30 min, and so on.
- **Clock/NTP drift**: small drift = ignore. Past best-practice tolerance
  (2026 norms: sub-second for TLS/Kerberos-sensitive setups; minutes =
  definitely broken) = VERY HIGH priority — can break SSL/auth.
- **Backend restarts**: 2 unexplained restarts within ~a week = stability
  finding.
- **xmx**: the finding is "actual xmx below DSS's own memory-algorithm
  recommendation" — no absolute sizing bands; apply common sense to the gap.
- **Long-lived kernels/JEKs**: alive beyond ~days = finding ("notebook jobs
  rarely need days"); containerized/K8s escalates one severity band (direct
  cost). Stuck JEKs: same days-not-weeks rule. Idle age matters more than
  RSS alone.
- **Kill candidates**: a process >~25% of host RAM = surface as candidate,
  but NEVER advise a Linux-level kill of DSS-managed processes — they
  respawn; stop them at the DSS level via DSS APIs.
- **Idle GPU nodes**: finding after 1 hour idle; weight ~10× normal
  idle-compute (GPU nodes ≈10× node cost).

### Version lifecycle (DSS-version-aware)
- DSS itself **1 major version behind = bad** (minor lag is not a finding).
- Severity of a Python env = f(env version, instance DSS version,
  deprecated-vs-removed stage) — the Dataiku lifecycle, not python.org EOL.
- Banding: in-use 3.6/3.7 env = **important — migrate now**; in-use 3.8 on
  DSS 14 = **warning — plan migration** before the removal release;
  **unreferenced** deprecated env = delete candidate (cleanup severity, not
  migration severity).
- DSS 14 deprecation set — flag any of these in use on a DSS 14 instance
  (escalate as "will be REMOVED in a later release"): Python 3.8; Govern
  PostgreSQL 12–14; MXNet-based forecasting; MLLib; AmazonLinux 2; KSQL
  recipes; API-deployer "Reporting to Graphite" + "Metrics charting server"
  settings; plugins "List folder Contents", "Azure AD Sync", "EMR clusters",
  "Dataproc clusters".

### Code envs & projects
- **Code env >5GB** = likely too large → finding, **subject to the per-item
  admin whitelist**.
- **Zero-usage code envs & plugins**: cleanup candidates after 3 months.
- **Duplicate-env drift**: don't care — not a finding.
- **R**: R included but unused in a project env = bad. Standing
  recommendation whenever a default R env is detected in admin settings:
  strongly recommend NOT setting one (drags R into projects that don't use
  it).
- **Any project >10GB** = finding — typically bad things are happening
  (webapp logs, filesystem files instead of block storage). **Subject to the
  per-item admin whitelist.**
- **Zero-git-commit projects** = low-priority warning.
- **Abandonment**: use the toolkit's configured inactivity cutoff (default
  180 days) — read the setting, never hardcode.
- **Job/scenario logs**: severity is relative — judged by share of the /data
  disk (feeds the ≥75% line), not by age.

### Connections
- Failing test alone = low (cleanup mess); actively-used + recently broken =
  very high (see always-lead).
- **filesystem_managed for real team data** = bad — fills local filesystem,
  not prod-friendly; push S3/ADLS/etc. Score by share of project data +
  growth.
- **Orphaned connections** (0 using projects): 3-month cleanup rule.
- Many same-type connections = **fine**, not a finding.
- Pushdown/SQL-pipelines not enabled where the warehouse supports it =
  medium improvement.
- Governance params (open-to-everyone, embedded creds, SSL off) = NOT this
  persona's findings.

### DB health (runtime databases)
- Everything scale-gated at ~1000+ users (table bloat, autovacuum, size),
  EXCEPT **observed connection-pool exhaustion errors = very high priority
  at any size**.
- The agent may **propose VACUUM/ANALYZE only on 1000+-user instances**;
  below that, surface-only.

### Cost class (never health)
These affect spend, not instance health — classify under cost, never in the
health score:
- **Registry image sprawl**: retention rule = DSS typically needs no image
  older than the current one (align with the toolkit image-cleaner
  semantics).
- **Oversized containers**: sustained (p95) utilization <~50% of request =
  oversized; recommend shrinking toward p95 + headroom; exempt genuinely
  spiky workloads.
- **Zero-traffic 24/7 webapps/API services**: ~2–4 weeks of near-zero
  traffic → shutdown proposal via owner outreach.
- **One user's sandbox dominating compute** = medium.
- **Autoscaler off/misconfigured** = standing important.
- **Bin-packing waste** >30–40% sustained = act.
- **LLM cost**: quote estimatedCostUSD as DSS's own estimate; note the
  rolling audit-log horizon (days–weeks, recent-window only).

### Users & licenses (admin lens)
- Designer-seat reclaim list only when utilization ≥95% (growth blocked is
  THE admin problem; <40% shelfware is not a priority finding here).
- **Users without email** = medium — all users should have email set.
- Departed-but-enabled accounts = low hygiene.
- All adoption/QBR metrics: excluded from the digest.

### Plugins & platform hygiene
- Plugin version drift across nodes matters for deployed pipelines; dev
  plugin on prod = low.
- Installed plugin with 0 usage = cleanup candidate after 3 months.

### Delegated to LLM judgment (no fixed thresholds)
- backend.log pattern triage (real vs noise, in context)
- package-pin incident risk
- connection performance params (fetch/batch sizes)
- correlating DB symptoms with user-visible slowness

### Non-findings — never mention
- Swap usage (low-weight corroborating signal only, never standalone)
- Backups (Fleet Manager owns them on FM-managed instances)
- Permission/governance patterns (everyone-admin projects, open connections)
- Shared namespaces; R/conda presence per se; GC flags
- Dataset/model version bloat absent a material disk share
- Dormant:active project ratio targets

## False-positive doctrine (cross-cutting, very important)

Every thresholded cleanup/size rule honors a **per-item admin whitelist**:
whitelisted items are silently skipped wherever the rule applies — health
score, issue lists, agent findings, digest. Agents never resurface
whitelisted items; they report only "N findings suppressed by admin
whitelist". Rules that must honor it from day one: code env >5GB, project
>10GB, deprecated-Python delete candidates, disk-usage style rules.

## Action-safety doctrine (K97)

- The agent may **PROPOSE any destructive action** — nothing is off-limits
  to propose — as long as a human approves.
- Every destructive deletion **backs up to block storage FIRST** (the
  existing project/code-env cleanup pattern).
- **Settings changes record the prior value**; offer restore from the
  history of the last 50 changes for that item.
- If restore is NOT possible for something, **say so explicitly** in the
  action presentation.
- Explicit admin confirmation required on every destructive action
  (misclick protection); pre-authorization never counts.
- DSS-managed processes are stopped via DSS APIs, never Linux-level kills.

## Health score

The pre-rubric Summary health score was a placeholder (never calibrated).
This rubric is its replacement input: category weights favor
system-capacity/runtime-config (the infra-admin persona), and the
always-lead critical rules cap the overall score into the critical band
(see `useHealthScore.ts` / `health.py`).
