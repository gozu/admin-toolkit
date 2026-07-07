// Education content for the Agents module's ⓘ InfoDots. Every concept a
// first-time admin meets on the page has an entry; InfoDot renders nothing
// for ids that are absent, so dynamic ids (`tool.<name>`) degrade safely.

export interface EduEntry {
  title: string;
  body: string[];
}

export const EDU: Record<string, EduEntry> = {
  /* ── agents ─────────────────────────────────────────────────────────── */
  'agent.unified': {
    title: 'Admin Toolkit Agent',
    body: [
      'One conversational agent for admin work: fleet health and triage sweeps, scoping/architecture analysis, and — with your explicit approval — executing maintenance actions.',
      'Under the hood each request is routed to a specialist: read-only sensors answer health and scoping questions; a strictly gated actuator handles actions. Nothing ever changes without you approving that specific plan first.',
    ],
  },
  'agent.health-triage': {
    title: 'Health Triage agent',
    body: [
      'A read-only "sensor" agent. It sweeps every DSS host with the same 0–100 health score the toolkit UI uses, digs into logs, storage, databases and Kubernetes, and reports what needs attention.',
      'It can propose action items for the Ops Actuator, but it can never change anything itself — it has no plan or execute tools.',
    ],
  },
  'agent.scoping-architect': {
    title: 'Scoping Architect agent',
    body: [
      'A read-only analyst for sizing and architecture questions ("how big is this instance?", "what would a migration involve?"). Every claim is grounded in a tool call and cited with the host and tool that produced it.',
      'Like Health Triage, it can propose action items but never executes anything.',
    ],
  },
  'agent.ops-actuator': {
    title: 'Ops Actuator agent',
    body: [
      'The only agent that can change your instances — and only through a strict protocol: it PLANS an action (a read-only dry run showing the exact blast radius), shows you the plan, and executes only after you approve that specific plan.',
      'Five independent safety gates stand between a request and a mutation; the most important one (the kill switch) can only be flipped by a human administrator in the plugin settings.',
    ],
  },

  /* ── core concepts ──────────────────────────────────────────────────── */
  'concept.plan': {
    title: 'What is a plan?',
    body: [
      'A plan is a read-only dry run of an admin action. The agent gathers the real targets and consequences from live scans — sizes, owners, projects affected, backup destination — so you see exactly what would happen before anything happens.',
      'Approving a plan is the only way to authorize the action, and the approval covers exactly what the plan shows: if anything drifts (different target, host or action), the execution is automatically refused.',
    ],
  },
  'concept.confirm-token': {
    title: 'Confirm tokens',
    body: [
      'Every plan carries a cryptographically signed token binding the exact action + host + target with a 15-minute expiry. Execution recomputes the signature: a changed target, a forged token or an expired window is rejected outright.',
      'This means an agent can only ever execute the EXACT plan a human saw — nothing more, nothing else, nothing later.',
    ],
  },
  'concept.kill-switch': {
    title: 'The master kill switch',
    body: [
      'The agents plugin has an enable_red_actions master switch, OFF by default. While it is off, every execution — even a fully approved one — is refused at the last gate.',
      'Only a human administrator can turn it on, in the plugin settings. Agents cannot; that is the point.',
    ],
  },
  'concept.audit-trail': {
    title: 'Action audit trail',
    body: [
      'Every executed action — success or failure — writes one immutable row to the audit table (Postgres) before the result even reaches the agent: timestamp, agent, host, action, target, outcome, and a non-reversible hash of the confirm token.',
      'Rows proposed from an action-item checklist also carry the batch/item provenance, so you can trace an execution all the way back to the finding that motivated it.',
    ],
  },
  'concept.risk-colors': {
    title: 'Risk colors',
    body: [
      'Red = destructive or configuration-changing (deletes, settings writes) — read the plan carefully.',
      'Amber = maintenance that can briefly lock or slow things (e.g. VACUUM).',
      'Green = safe, low-impact housekeeping (e.g. ANALYZE, read-only checks).',
    ],
  },
  'concept.action-items': {
    title: 'Action items',
    body: [
      'When a sensor agent finds work worth doing, it proposes structured action items: title, reasoning, evidence, risk color, and — when the work maps exactly to the actuator catalog — a ready-to-plan action + target.',
      'Several objects needing the same action (say, six unused code envs) arrive as ONE batched item (the ×N chip): one plan, one approval, one confirm token covering every target, with per-target results at execution.',
      'Nothing is planned or executed at this stage. You check the items you want and hand them to the Ops Actuator, which plans each one fresh so blast radius and tokens are current at approval time.',
    ],
  },
  'concept.handoff': {
    title: 'Agent → actuator handoff',
    body: [
      'Checked action items are sent to the Ops Actuator as one batch message. The actuator plans every item (one plan card each) and then waits.',
      'You stay in the loop exactly where it matters: each plan still needs your explicit approval before execution, individually or via "Approve all".',
    ],
  },

  /* ── actuator actions (blast radius per catalog entry) ──────────────── */
  'action.project-delete': {
    title: 'project-delete',
    body: [
      'Backs the project up as a zip into a managed folder, then deletes it. The plan shows size, owner, inactivity and warns when the project is not on the inactive list.',
      'Irreversible apart from the zip backup — red risk, always.',
    ],
  },
  'action.code-env-delete': {
    title: 'code-env-delete',
    body: [
      'Backs up a code environment definition, then deletes it. The plan lists every project still using the env — deleting a used env breaks those projects.',
    ],
  },
  'action.image-delete': {
    title: 'image-delete',
    body: [
      'Deletes container images older than a cutoff from your registry (e.g. ECR). The plan includes a dry-run of exactly which images match. Frees registry storage; running workloads are unaffected.',
    ],
  },
  'action.db-vacuum': {
    title: 'db-vacuum',
    body: [
      'PostgreSQL VACUUM on one runtime-database table: reclaims space held by dead rows. Takes brief locks — amber risk, best in maintenance windows.',
    ],
  },
  'action.db-analyze': {
    title: 'db-analyze',
    body: [
      'PostgreSQL ANALYZE on one table: refreshes planner statistics so queries pick good plans. Cheap and safe — green risk.',
    ],
  },
  'action.plugin-deploy': {
    title: 'plugin-deploy',
    body: [
      'Deploys a plugin from this hub instance to another DSS host in the fleet. The plan shows the plugin version and whether it is a dev plugin.',
    ],
  },
  'action.k8s-exec-config-tune': {
    title: 'k8s-exec-config-tune',
    body: [
      'Right-sizes the CPU/memory requests and limits of one containerized execution config. The plan shows current vs proposed values and warns on cuts big enough to throttle or OOM-kill workloads.',
      'Affects new workloads using that config; running ones keep their old resources until restarted.',
    ],
  },
  'action.log-cleanup': {
    title: 'log-cleanup',
    body: [
      'Deletes ROTATED log files (backend.log.3, *.log.gz, dated rotations) older than a minimum age, under a fixed whitelist of DIP_HOME directories. A live *.log can never match — the whitelist is enforced inside the macro script, below the agent.',
      'The plan shows per-directory reclaimable GB; deletion aborts if candidates exceed the size cap.',
    ],
  },
  'action.docker-prune': {
    title: 'docker-prune',
    body: [
      'Prunes the docker builder cache (keeping a configured amount) or dangling images, with a fixed command line — no shell, no --all, docker-group access only.',
      'daemon.json cache limits are never executed: the plan carries a ready-made sudo script for a human admin instead.',
    ],
  },
  'action.k8s-apply-fix': {
    title: 'k8s-apply-fix',
    body: [
      'Runs policy-validated kubectl mutations (patch / apply / delete / label / scale / rollout-restart) on a DSS-attached cluster. Secrets, cluster-scoped kinds and --all/--force are refused inside the macro, not by trusting the model.',
      'The plan shows read-only previews and server dry-runs; with a verifyRule the finding is re-checked after execution and the result says whether it still fires.',
    ],
  },
  'action.code-env-consolidate': {
    title: 'code-env-consolidate',
    body: [
      'Repoints every usage of one code env (recipes, notebooks, webapps, scenarios, project defaults) onto a target env. The dry-run usage table in the plan is exactly what will change.',
      'Optionally retires the source env afterwards — backup-first, and only if every row updated cleanly.',
    ],
  },
  'action.settings-set': {
    title: 'settings-set',
    body: [
      'Sets one path in DSS general settings, with a current → proposed diff at approval. Security/auth/licensing paths and anything touching secret material are blacklisted below the agent.',
      'The observed current value is bound into the confirm token (drift between plan and execute refuses), and every applied change lands in the restorable settings history.',
    ],
  },
  'action.connection-test': {
    title: 'connection-test',
    body: [
      'Runs the native DSS connection test for one connection — a read-only probe reporting connectionOK true/false. Green risk: nothing is changed.',
      'The natural verification step right after a connection-update repair.',
    ],
  },
  'action.connection-update': {
    title: 'connection-update',
    body: [
      'Sets one path in a connection\'s definition (e.g. params.host for a blank-host repair), with a current → proposed diff at approval. Paths touching secret material (passwords, tokens, keys) are blacklisted below the agent — credentials are never readable or writable this way.',
      'The observed current value is bound into the confirm token (drift between plan and execute refuses), and every applied change lands in the restorable settings history.',
    ],
  },
  'action.connection-delete': {
    title: 'connection-delete',
    body: [
      'Backs up the connection definition as JSON into a managed folder, then deletes the connection. The plan warns when anything still uses it — deleting a used connection breaks those datasets and recipes.',
      'The backup may carry credential material, so the folder is admin-scoped. Restore = recreate the connection from the JSON.',
    ],
  },
  'action.cluster-detach': {
    title: 'cluster-detach',
    body: [
      'Backs up the cluster definition, then removes the DSS attachment only — the cloud-side cluster keeps running (and costing) until removed in the cloud console.',
      'Meant for stale attachments whose endpoint no longer resolves (DNS-dead). The plan warns when the cluster is RUNNING or still reachable.',
    ],
  },
  'action.plugin-uninstall': {
    title: 'plugin-uninstall',
    body: [
      'Backs the plugin up as a zip into a managed folder, then uninstalls it. Refused outright while ANY usage exists (checked at plan time and re-checked at execute), and the toolkit never uninstalls itself.',
      'Restore = re-upload the backed-up zip.',
    ],
  },
  'action.project-clear-webapp-runs': {
    title: 'project-clear-webapp-runs',
    body: [
      'Deletes dead webapp run directories of one project (the "Web app runs" bucket in the footprint breakdown), keeping the newest N per webapp and never touching a running backend\'s directory.',
      'The roots/age/keep-newest policy is enforced inside a macro on the target host, below both the agent and the backend — like log-cleanup.',
    ],
  },
  'action.connection-index': {
    title: 'connection-index',
    body: [
      'Re-indexes one or more connections (or all of them) in the DSS catalog — a read-only metadata crawl, green risk.',
      'Useful after repairing a connection or when catalog search is stale. Large connections can take a while.',
    ],
  },
  'action.cluster-stop': {
    title: 'cluster-stop',
    body: [
      'Stops a DSS-managed cluster. With terminate=true the cloud-side resources are DESTROYED — that variant is irreversible and the plan says so explicitly.',
      'Without terminate, cluster-start brings it back. Manual attachments cannot be stopped (use cluster-detach for those).',
    ],
  },
  'action.cluster-start': {
    title: 'cluster-start',
    body: [
      'Starts a stopped DSS-managed cluster — provisions cloud resources, so cost resumes when it comes up.',
    ],
  },
  'action.cluster-pods-cleanup': {
    title: 'cluster-pods-cleanup',
    body: [
      'Deletes FINISHED pods and job objects on one cluster — running workloads are untouched. Green risk: only completed/failed leftovers go away.',
    ],
  },
  'action.plugin-update': {
    title: 'plugin-update',
    body: [
      'Backs the current plugin version up as a zip, then updates the plugin from the Dataiku store. Rollback = re-upload the backup.',
      'Dev plugins cannot be store-updated; code-env-based components keep their env until plugin-code-env-rebuild runs.',
    ],
  },
  'action.plugin-code-env-rebuild': {
    title: 'plugin-code-env-rebuild',
    body: [
      'Rebuilds the managed code env of one plugin (after an update, or when the env is broken). Running kernels keep the old env until they recycle.',
    ],
  },
  'action.code-env-update': {
    title: 'code-env-update',
    body: [
      'Re-resolves and updates the packages of one code env (optionally a full rebuild), then refreshes its container images when any are configured.',
      'A failed package resolution leaves the env unchanged; running kernels keep the old env until restarted.',
    ],
  },
  'action.project-export': {
    title: 'project-export',
    body: [
      'Exports one project as the standard DSS zip bundle into a managed folder — a read-only snapshot, green risk, batchable.',
      'The natural prelude to project-delete, and the cheapest way to hand a project to another instance.',
    ],
  },
  'action.project-set-cluster': {
    title: 'project-set-cluster',
    body: [
      'Points one project at an explicit K8s cluster (settings.k8sCluster → EXPLICIT_CLUSTER) — the fix for the "No cluster selected in project" sanity warning.',
      'Drift-guarded and recorded in the restorable settings history.',
    ],
  },
  'action.project-change-owner': {
    title: 'project-change-owner',
    body: [
      'Changes the owner of one project (permissions stay as they are). The current owner is bound into the confirm token and the change lands in history.',
      'The new owner must be an existing enabled user.',
    ],
  },
  'action.project-variables-set': {
    title: 'project-variables-set',
    body: [
      'Sets one PROJECT variable via a scoped path (standard.myVar / local.myVar) with a current → proposed diff. Secret-material paths are blocked.',
      'Drift-guarded and restorable from the settings history — the project-level sibling of variables-set.',
    ],
  },
  'action.job-kill': {
    title: 'job-kill',
    body: [
      'Aborts one job through the DSS job API (never a Linux-level kill — DSS-managed processes respawn). Aborting a finished job is a no-op.',
      'Batchable: several runaway jobs = one item with targets[].',
    ],
  },
  'action.scenario-disable': {
    title: 'scenario-disable',
    body: [
      'Turns OFF the auto-triggers of one scenario — the standard response to a failure/retry storm. Reversible with scenario-enable; the toggle lands in the restorable history.',
      'A running instance of the scenario is not aborted by this (use scenario-kill for that).',
    ],
  },
  'action.scenario-enable': {
    title: 'scenario-enable',
    body: [
      'Turns the auto-triggers of one scenario back ON — the inverse of scenario-disable, same drift guard and history trail.',
    ],
  },
  'action.scenario-kill': {
    title: 'scenario-kill',
    body: [
      'Aborts the CURRENT run of one scenario at the DSS level. The scenario itself stays enabled — pair with scenario-disable to stop it from re-firing.',
    ],
  },
  'action.scenario-run': {
    title: 'scenario-run',
    body: [
      'Triggers one manual scenario run — works even when auto-triggers are disabled. The plan warns when a run is already in flight.',
    ],
  },
  'action.continuous-activity-stop': {
    title: 'continuous-activity-stop',
    body: [
      'Stops the continuous activity of one recipe; DSS persists the desired state, so it stays stopped until someone starts it again.',
    ],
  },
  'action.webapp-backend-stop': {
    title: 'webapp-backend-stop',
    body: [
      'Stops the backend of one webapp through the DSS API — users lose their session until it is started again. The go-to for zero-traffic 24/7 webapps.',
      'Batchable; webapp-backend-restart is the recovering sibling.',
    ],
  },
  'action.webapp-backend-restart': {
    title: 'webapp-backend-restart',
    body: [
      'Starts (or restarts) the backend of one webapp — the standard fix for a wedged backend, and the revert for webapp-backend-stop.',
    ],
  },
  'action.notebook-kernels-shutdown': {
    title: 'notebook-kernels-shutdown',
    body: [
      'Shuts down the ACTIVE Jupyter kernels of one project (or the whole instance) via the DSS API. Notebook files and outputs stay on disk — only running kernels and their memory go away; users just restart.',
      'The rubric answer to kernels alive beyond ~days. The plan lists every kernel before approval.',
    ],
  },
  'action.notebook-clear-outputs': {
    title: 'notebook-clear-outputs',
    body: [
      'Clears the SAVED cell outputs of one notebook — shrinks a bloated .ipynb; code cells are untouched. Outputs are not restorable, but re-running regenerates them.',
      'Batchable: several oversized notebooks = one item.',
    ],
  },
  'action.variables-set': {
    title: 'variables-set',
    body: [
      'Sets one GLOBAL instance variable via a dot/index path with a current → proposed diff. Secret-material paths are blocked, and the toolkit\'s own finding whitelist is protected — agents never edit their own suppression list.',
      'Drift-guarded and restorable from the settings history.',
    ],
  },
  'action.user-disable': {
    title: 'user-disable',
    body: [
      'Disables one user account — never deletes it, so user-enable reverts cleanly. The toolkit refuses to disable the identity it runs as (self-lockout guard).',
      'The plan warns when the target is in the administrators group. Batchable for departed-user sweeps.',
    ],
  },
  'action.user-enable': {
    title: 'user-enable',
    body: [
      'Re-enables a disabled user account — the inverse of user-disable, same drift guard and history trail.',
    ],
  },
  'action.api-key-delete': {
    title: 'api-key-delete',
    body: [
      'Deletes one personal or global API key. IRREVERSIBLE: the key secret cannot be restored or regenerated — anything still using it breaks immediately.',
      'The toolkit refuses to delete personal keys of its own identity; global keys carry no owner, so the plan tells the human to verify it is not the key the toolkit uses.',
    ],
  },
  'action.tmp-cleanup': {
    title: 'tmp-cleanup',
    body: [
      'Deletes aged entries INSIDE the DIP_HOME tmp buckets (tmp/<bucket>/<entry>, older than the age gate by newest inner mtime). The bucket directories themselves — and the webappruns bucket — are never touched.',
      'Depth, containment, symlink refusal and the age gate are re-applied per entry inside the fs-cleanup macro at delete time.',
    ],
  },
  'action.exports-cleanup': {
    title: 'exports-cleanup',
    body: [
      'Deletes aged export artifacts (exports/<kind>/<entry>) — one-shot downloads users can regenerate from the original object.',
    ],
  },
  'action.job-logs-cleanup': {
    title: 'job-logs-cleanup',
    body: [
      'Deletes whole aged job directories (jobs/<PROJECT>/<jobDir>) — activity logs and job metadata go with them; the newest N per project always survive. Rubric: job-log severity is judged by share of the /data disk.',
      'Optionally scoped to one project. The cap (maxDeleteGB) aborts the whole delete when candidates exceed it.',
    ],
  },
  'action.dataset-clear': {
    title: 'dataset-clear',
    body: [
      'Clears the DATA of one dataset — IRREVERSIBLE (schema and settings survive; rebuilding regenerates the data). Datasets exposed to other projects are refused unless the plan carries an explicit ackExposed the admin approved in conversation.',
      'Batchable for sweeping stale managed datasets.',
    ],
  },
  'action.db-reindex': {
    title: 'db-reindex',
    body: [
      'REINDEX one runtime-DB table (table name validated against pg_stat_user_tables inside the backend — never raw SQL). Takes an exclusive lock for the duration — maintenance-window material.',
      'Same scale gate as vacuum/analyze: propose only on ~1000+-user instances.',
    ],
  },
  'concept.auto-remediation': {
    title: 'Auto-remediation',
    body: [
      'An admin can opt specific actions (only the reversible, capped cleanups: log-cleanup, docker-prune) into autonomous execution during the daily triage sweep via the auto_remediate_actions plugin setting.',
      'Every autonomous run still passes the kill-switch and policy gates, respects cumulative GB/object caps, writes an audit row as triage-auto, and is reported in the digest — skips included, with reasons.',
    ],
  },

  /* ── tools (sensor + actuator surface) ──────────────────────────────── */
  'tool.triage_sweep': {
    title: 'triage_sweep',
    body: [
      'Deterministic fleet triage: scores every configured host with the toolkit health score, ranks worst-first and flags hosts under the threshold. The agent must use this ranking, not invent its own.',
    ],
  },
  'tool.propose_action_items': {
    title: 'propose_action_items',
    body: [
      'How a sensor agent turns findings into the checklist you see: up to 10 structured items, validated server-side (invalid actions become advisory, never silently dropped).',
    ],
  },
  'tool.plan_admin_action': {
    title: 'plan_admin_action',
    body: [
      'Builds the read-only dry run for one admin action: real targets, blast radius, warnings, backup destination — plus the signed confirm token that makes approval binding.',
    ],
  },
  'tool.execute_admin_action': {
    title: 'execute_admin_action',
    body: [
      'Executes a planned action. Refused unless ALL gates pass: master kill switch on, agent allowed to execute, action on the agent allowlist, explicit confirm, and a valid unexpired token matching the exact plan.',
    ],
  },
  'tool.list_hosts': {
    title: 'list_hosts',
    body: ['Lists the DSS hosts the toolkit can reach (id, label, url), optionally probing reachability.'],
  },
  'tool.instance_health': {
    title: 'instance_health',
    body: ['Health snapshot of one host: system checks, sanity checks, Java memory, issues, and optionally the full 0–100 health score.'],
  },
  'tool.compute_cost': {
    title: 'compute_cost',
    body: ['Compute + LLM cost from Compute Resource Usage audit records, grouped by project, user or context type. Span limited to audit retention.'],
  },
  'tool.config_inspect': {
    title: 'config_inspect',
    body: ['Inspects one config domain — connections, code envs, plugins or LLM Mesh — with health or usage detail.'],
  },
  'tool.log_errors': {
    title: 'log_errors',
    body: ['Groups recent backend.log errors by signature; can also grep the raw tail with a custom pattern.'],
  },
  'tool.storage_footprint': {
    title: 'storage_footprint',
    body: ['Project storage totals, largest projects, and inactive+large cleanup candidates. A heavy scan — may report scan_running while warming.'],
  },
  'tool.k8s_health': {
    title: 'k8s_health',
    body: ['Kubernetes clusters for a host: states plus a reachability sweep; can run a deep audit of one cluster.'],
  },
  'tool.db_health': {
    title: 'db_health',
    body: ['Runtime-database PostgreSQL health: overview, biggest/bloated tables, or per-project usage.'],
  },
};

export function eduEntry(id: string | undefined): EduEntry | null {
  if (!id) return null;
  return EDU[id] || null;
}
