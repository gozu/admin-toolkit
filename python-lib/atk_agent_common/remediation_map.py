"""Finding → remediation registry.

Maps health-score issue ids (atk_agent_common.health) and k8s-insights rule
ids to catalogued actuator actions. First glob match wins.

`auto: True` means "has a deterministic finding→target mapping": a
`build_target` that produces a concrete target from the finding + settings
alone, so the nightly deterministic tier can run it without an LLM. It is NOT
the autonomy consent — that lives on the per-action Autonomous flag in
Agents → Permissions (`agent_autonomous_gates`), which covers the whole
catalog: the nightly LLM planning pass may propose ANY autonomous-granted
action, mapped here or not. A build_target returning None means "this
finding lacks the data" and the candidate is silently not proposed.

Explicit `None` entries document known gaps: findings we can detect but not
remediate through any catalogued action (so agents say "manual" instead of
improvising).
"""

from fnmatch import fnmatchcase

# Aged job dirs are a different corpus than rotated logs — 14 days keeps the
# recent-debugging window intact while still draining months of buildup.
_JOB_LOGS_MIN_AGE_DAYS = 14
# Batched auto targets stay bounded so one pathological finding can't turn
# into a hundred-target plan nobody reviewed.
_AUTO_BATCH_CAP = 10


def _log_cleanup_target(issue, settings):
    return {'roots': [],  # empty = every whitelisted root
            'minAgeDays': int(settings.get('log_cleanup_min_age_days') or 3),
            'maxDeleteGB': int(settings.get('auto_remediate_max_gb') or 20)}


def _docker_prune_target(issue, settings):
    return {'mode': 'builder',
            'keepStorageGB': int(settings.get('auto_remediate_max_gb') or 20)}


def _job_logs_cleanup_target(issue, settings):
    return {'minAgeDays': _JOB_LOGS_MIN_AGE_DAYS,
            'maxDeleteGB': int(settings.get('auto_remediate_max_gb') or 20)}


def _connection_test_target(issue, settings):
    """Re-probe the connections the finding names (issue.items). Batch when
    several are failing — one plan, per-connection pass/fail in the result."""
    names = [str(n) for n in (issue.get('items') or []) if n]
    if not names:
        return None
    if len(names) == 1:
        return {'name': names[0]}
    return [{'name': n} for n in names[:_AUTO_BATCH_CAP]]


def _kernels_shutdown_target(issue, settings):
    # Empty projectKey = every active kernel on the host; the planner refuses
    # (→ skip) when there is nothing to shut down.
    return {'projectKey': ''}


def _clear_webapp_runs_target(issue, settings):
    """Trim dead webapp run dirs in the projects the finding names. Only ever
    deletes non-running run directories, newest N per webapp survive."""
    keys = [str(k) for k in (issue.get('items') or []) if k]
    if not keys:
        return None
    if len(keys) == 1:
        return {'projectKey': keys[0]}
    return [{'projectKey': k} for k in keys[:_AUTO_BATCH_CAP]]


def _spec(action, risk, why, auto=False, build_target=None):
    return {'action': action, 'risk': risk, 'auto': auto, 'why': why,
            'build_target': build_target}


# First-match (id_glob, [spec, ...] | None) list. None = documented gap.
REMEDIATIONS = [
    # ── disk pressure: reversible cleanups first ─────────────────────────────
    ('cap-data-mount-full', [
        _spec('log-cleanup', 'low', 'Rotated logs are the safest space to reclaim on a full '
              'data mount.', auto=True, build_target=_log_cleanup_target),
        _spec('docker-prune', 'low', 'When DockerRootDir shares the data mount, builder/image '
              'cache is usually the biggest reclaimable block.', auto=True,
              build_target=_docker_prune_target),
        _spec('job-logs-cleanup', 'low', 'Aged job directories are the next reclaim after '
              'rotated logs and docker cache — newest N per project survive.',
              auto=True, build_target=_job_logs_cleanup_target),
    ]),
    ('disk-critical-*', [
        _spec('log-cleanup', 'low', 'Reclaim rotated logs before anything invasive.',
              auto=True, build_target=_log_cleanup_target),
        _spec('docker-prune', 'low', 'Prune docker build/image cache if docker lives on the '
              'affected mount.', auto=True, build_target=_docker_prune_target),
        _spec('job-logs-cleanup', 'low', 'Aged job directories (jobs/<PROJECT>/<jobDir>) '
              'free real space with zero risk to running work.',
              auto=True, build_target=_job_logs_cleanup_target),
    ]),
    ('disk-warning-*', [
        _spec('log-cleanup', 'low', 'Early cleanup keeps the warning from becoming critical.',
              auto=True, build_target=_log_cleanup_target),
        _spec('job-logs-cleanup', 'low', 'Aged job directories (jobs/<PROJECT>/<jobDir>) are '
              'the next-safest reclaim after rotated logs — newest N per project survive; '
              'severity is judged by share of the /data disk (rubric).',
              auto=True, build_target=_job_logs_cleanup_target),
    ]),

    # ── connections ──────────────────────────────────────────────────────────
    ('cap-connection-broken', [
        _spec('connection-update', 'high', 'Repair a broken definition field — but '
              'connection-update REQUIRES {name, path, newValue}, and the newValue must be a '
              'concrete known-good value the evidence supplies (the finding, settings history, '
              'or the admin named it). Never guess or invent one. A BLANK required field '
              '(blank host/database/JDBC URL) usually means a never-configured junk '
              'connection, not drift: keep that ADVISORY and recommend connection-delete '
              'after a usage review instead of a repair. Dead endpoints and expired '
              'credentials are not definition repairs either — ADVISORY (infra/creds work). '
              'When a real newValue exists the action is drift-guarded, secret paths '
              'blocked, prior value restorable from history.'),
        _spec('connection-test', 'low', 'Verify the repair immediately: the test result '
              'reports connectionOK true/false.', auto=True,
              build_target=_connection_test_target),
    ]),
    ('connection-broken-unused', [
        _spec('connection-delete', 'medium', 'Nothing references the connection — back up its '
              'definition JSON and delete it (batchable: one item, N names).'),
        _spec('connection-update', 'medium', 'Or repair it instead when the admin wants to '
              'keep it (path into the definition, drift-guarded).'),
    ]),
    ('connection-broken-unverified', [
        _spec('connection-test', 'low', 'Re-probe the connection; run the usage scan before '
              'proposing anything destructive.', auto=True,
              build_target=_connection_test_target),
    ]),

    # ── clusters ─────────────────────────────────────────────────────────────
    ('cluster-endpoint-unreachable*', [
        _spec('cluster-detach', 'medium', 'A DNS-dead endpoint means the cluster is gone and '
              'the attachment is stale — back up the definition JSON and detach it from DSS. '
              'Other error classes (auth, timeout) need investigation first, not detachment.'),
    ]),

    # ── plugins ──────────────────────────────────────────────────────────────
    ('plugin-deprecated*', [
        _spec('plugin-uninstall', 'medium', 'Deprecated plugin (rubric DSS-14 list): zip '
              'backup first, uninstall refused while any usage exists (batchable).'),
    ]),
    ('plugin-unused*', [
        _spec('plugin-uninstall', 'medium', 'Zero-usage plugin past the 3-month cleanup '
              'window: zip backup first, usage re-checked at execute (batchable).'),
    ]),

    # ── code-env sprawl / lifecycle ──────────────────────────────────────────
    ('python-lifecycle-*', [
        _spec('code-env-consolidate', 'medium', 'Repoint usages of deprecated-Python envs onto '
              'a supported target env (dry-run usage table shown at approval), optionally '
              'retiring the source afterwards. code-env-consolidate REQUIRES a concrete '
              'targetEnvName — propose it as an ACTIONABLE item only when a suitable supported '
              'target env actually exists in the inventory to name (never a guessed name, never '
              'an unused/empty env). If no clear target env is available, keep this ADVISORY: '
              'name the migration work and let the admin pick the target — do NOT emit a '
              'consolidate action with a missing or invented targetEnvName.'),
        _spec('code-env-delete', 'medium', 'UNREFERENCED deprecated envs skip consolidation: '
              'backup-first delete, several envs as ONE batched item (targets[]).'),
    ]),
    ('project-codenv-*', [
        _spec('code-env-consolidate', 'medium', 'The finding names only PROJECT KEYS; the '
              'action needs concrete sourceEnvName+targetEnvName — DRILL before proposing: '
              'config_inspect code-envs name_filter=<projectKey> returns exactly the envs '
              'that project uses. GATE: envs serving structurally different needs (GPU vs '
              'CPU stacks, conflicting version pins, a plugin-managed env) should NOT be '
              'merged — propose whitelisting under project-code-envs instead. Pick the '
              'survivor by package math: the superset env on a supported Python wins; '
              'never target an unused/empty env, never retire an env other projects '
              'share. Propose with retireSource:false (planning is read-only and '
              'enumerates every recipe/notebook/webapp/scenario touched); validate by '
              'scenario-run of the project\'s load-bearing scenario going green; retiring '
              'the drained source is a SEPARATE backup-first code-env-delete item. '
              'Neither env covers the other and both live ⇒ a merged env is admin work '
              '(ADVISORY; no catalogued env-create action exists).'),
    ]),
    ('code-env-size*', [
        _spec('code-env-consolidate', 'medium', 'Merge oversized near-duplicate envs, then '
              'retire the source (backup-first).'),
    ]),

    # ── general settings ─────────────────────────────────────────────────────
    ('java-memory-*', [
        _spec('settings-set', 'medium', 'Tune the offending runtime setting via the gated '
              'settings mutator (JSON diff at approval, restorable history). Java Xmx values '
              'that live in install.ini are NOT reachable — say so instead of guessing a path.'),
    ]),
    ('features-disabled-*', [
        _spec('settings-set', 'low', 'Re-enable the disabled feature flags using the exact '
              'paths in the finding\'s details[].settingsPath — several flags as ONE batched '
              'settings-set item (targets[]). Skip entries marked sensitive (impersonation) '
              'unless the admin asks.'),
    ]),
    ('cgroups-*', [
        _spec('settings-set', 'medium', 'cgroup integration toggles live in general settings '
              '(the enable flag is cgroupSettings.enabled; empty target types live under '
              'cgroupSettings.<targetType>.targets); propose the exact path + value and let '
              'the human approve the diff.'),
    ]),

    # ── k8s (k8s-insights rule ids) ──────────────────────────────────────────
    ('nvidia-device-plugin-missing-affinity', [
        _spec('k8s-apply-fix', 'medium', 'Patch the nvidia-device-plugin daemonset affinity '
              '(the exact kubectl patch is in the finding remediation), then verify with '
              'verifyRule so the plan proves the fix landed.'),
    ]),
    ('daemonset-*', [
        _spec('k8s-apply-fix', 'medium', 'Daemonset targeting/crashloop fixes are patch/'
              'rollout-restart operations within the kubectl policy.'),
    ]),
    ('pod-stuck-terminating', [
        _spec('k8s-apply-fix', 'low', 'Delete the stuck pod (policy forbids --force/'
              '--grace-period=0; a plain delete is usually enough).'),
    ]),
    ('pod-oomkilled-recent', [
        _spec('k8s-exec-config-tune', 'medium', 'Raise memRequestMB/memLimitMB on the '
              'execution config the workload uses.'),
    ]),
    ('pod-overrequested-*', [
        _spec('k8s-exec-config-tune', 'low', 'Right-size the over-requesting execution config '
              '(CRU usage evidence lands in the plan).'),
    ]),
    ('pod-underrequested-memory', [
        _spec('k8s-exec-config-tune', 'medium', 'Raise the memory request to match observed '
              'usage before the node OOMs.'),
    ]),
    ('execution-config-*', [
        _spec('k8s-exec-config-tune', 'low', 'Set/repair requests+limits on the flagged '
              'execution config.'),
    ]),
    ('cluster-autoscaler-scale-down-*', [
        _spec('k8s-apply-fix', 'medium', 'Autoscaler behaviour is configured on its deployment '
              '(kube-system patch/annotate is within policy for deployments).'),
    ]),

    # ── project storage ──────────────────────────────────────────────────────
    ('project-size-*', [
        _spec('project-clear-webapp-runs', 'medium', 'When the footprint breakdown names '
              '"Web app runs" (bucketKey webApps), trim dead run dirs — keeps the newest N '
              'per webapp, never touches a running backend.', auto=True,
              build_target=_clear_webapp_runs_target),
    ]),

    # ── runtime workloads (sanity codes surface as sanity-warning-<CODE>) ────
    ('sanity-*LONG_RUNNING*', [
        _spec('notebook-kernels-shutdown', 'medium', 'Kernels alive beyond ~days rarely do '
              'real work (rubric): shut down the active kernels via the DSS API — files and '
              'outputs untouched, users just restart. Never a Linux-level kill.',
              auto=True, build_target=_kernels_shutdown_target),
    ]),
    ('sanity-*JUPYTER*', [
        _spec('notebook-kernels-shutdown', 'medium', 'Idle/leaked notebook kernels are '
              'reclaimed at the DSS level; the plan lists every kernel before approval.',
              auto=True, build_target=_kernels_shutdown_target),
    ]),
    ('sanity-*CLUSTERS_NONE_SELECTED*', [
        _spec('project-set-cluster', 'low', 'Point the flagged project at an explicit K8s '
              'cluster (settings.k8sCluster) — drift-guarded, restorable from history.'),
    ]),
    ('sanity-*SCENARIO*', [
        _spec('scenario-disable', 'medium', 'Failure-storm or log-spamming scenarios are '
              'disabled (auto-triggers off) — reversible with scenario-enable; history '
              'records the toggle.'),
    ]),
    ('sanity-*SNOWFLAKE_NO_AUTOFASTWRITE*', [
        _spec('connection-update', 'medium', 'Enable auto fast-write on the flagged '
              'Snowflake connection — GATE first: if the same connection is also '
              'broken-unused, propose connection-delete instead (never both for one '
              'connection). Ground on the ACTUAL definition (config_inspect connections '
              'name_filter=<name>) and use only param paths observed there, never '
              'guessed ones. Auto fast-write needs an existing cloud-storage staging '
              'connection (S3/GCS/Azure) usable by the same projects '
              '(connections-usage) — none present ⇒ ADVISORY, say why. Verify after: '
              'connection-test passes and the sanity warning clears on re-read.'),
    ]),
    ('sanity-*SPARK_NO_GROUP_WITH_DETAILS_READ_ACCESS*', [
        _spec('connection-update', 'medium', 'Grant detail-read groups on the flagged '
              'Spark connection. Derive candidates from observed usage, never invent: '
              'connections-usage names the projects/users on it, the users domain maps '
              'them to groups — propose the SMALLEST existing group set covering the '
              'actual Spark users. Global credentials on the connection ⇒ note that '
              'detail-read can expose them and stay narrowest. Usage empty or spanning '
              'unrelated groups ⇒ policy call: ADVISORY listing candidate groups with '
              'user counts for the admin to pick. Verify by re-reading the definition '
              'and the sanity warning clearing.'),
    ]),
    ('sanity-*APP_AS_RECIPE*', [
        _spec('project-delete', 'medium', 'Orphan app instances (creating App recipe '
              'deleted) are dead projects: identify them via config_inspect '
              'app-instances — trust orphanKeys only when orphanDeterminable=true '
              '(macro-attributed, never guessed from project labels). Per-candidate '
              'safety sweep before proposing: nothing exposed/shared, no scenario '
              'references, no recent activity — then ONE batched project-delete '
              '(backup-first) whose evidence names each deleted parent recipe. Any '
              'doubt ⇒ ADVISORY with the candidate list. TOO_MANY_INSTANCES is the '
              'cause, not a delete target: recommend switching the recipe\'s '
              'keepInstance flag off (App Instances page — no catalogued action '
              'mutates it). Verify: sanity re-read shows the warning cleared.'),
    ]),
    ('sanity-*GIT_PROJECT_NOT_MIGRATED*', [
        _spec('notification-send', 'low', 'Post-upgrade housekeeping, score-exempt by '
              'default — never sell it as health-score points. The migration itself is '
              'human work: each flagged project\'s branches must be checked out once on '
              'the current DSS version (project Version Control page) by someone with '
              'write access, in-flight work committed first. Do the legwork: name the '
              'projects from the sanity details, resolve owner (projects domain) and '
              'email (users domain), and offer ONE notification-send carrying that '
              'precise checklist. Branch checkout mutates working state — never script '
              'it; python-run only when the admin explicitly asks (per-run code ack).'),
    ]),

    # ── users & licenses ─────────────────────────────────────────────────────
    ('users-departed*', [
        _spec('user-disable', 'low', 'Departed-but-enabled accounts are disabled, never '
              'deleted — user-enable reverts; the toolkit refuses its own identity.'),
    ]),

    # ── DB ───────────────────────────────────────────────────────────────────
    ('db-*', [
        _spec('db-vacuum', 'low', 'Vacuum the bloated table (locks briefly).'),
        _spec('db-reindex', 'low', 'Rebuild badly bloated indexes (exclusive lock — '
              'maintenance window; same 1000+-user scale gate as vacuum).'),
    ]),

    # ── exec configs (2026-07-19 drill: fell through unmapped; the agent
    #    already plans k8s-exec-config-tune for these — the registry now
    #    agrees so triage proposes it too) ───────────────────────────────────
    ('exec-config-resources*', [
        _spec('k8s-exec-config-tune', 'low', 'Add the missing memory/cpu requests+limits '
              'to the flagged containerized exec config — drift-guarded, restorable.'),
    ]),

    # ── k8s rule findings that previously fell through unmapped ──────────────
    ('node-memory-pressure', [
        _spec('cluster-pods-cleanup', 'low', 'Clear finished pods/jobs first — the '
              'zero-risk reclaim on a pressured node.'),
        _spec('k8s-exec-config-tune', 'low', 'Right-size the exec configs whose pods '
              'crowd the node (requests drive the scheduler).'),
    ]),
    ('node-disk-pressure', [
        _spec('cluster-pods-cleanup', 'low', 'Finished pods pin image/log storage; '
              'clearing them is the safe first move on a disk-pressured node.'),
    ]),
    ('pod-without-resources', [
        _spec('k8s-exec-config-tune', 'low', 'Give the offending exec config explicit '
              'requests+limits so its pods stop running unbounded.'),
    ]),
    ('idle-long-running-pod', [
        _spec('k8s-apply-fix', 'medium', 'Delete the idle pod via a reviewed kubectl '
              'plan — its controller (if any) recreates it clean.'),
    ]),
    ('gpu-pod-not-using-gpu', [
        _spec('k8s-exec-config-tune', 'low', 'Move the workload to a CPU exec config '
              '(or drop the GPU request) so the GPU node frees up.'),
    ]),
    ('node-over-provisioned', [
        _spec('k8s-exec-config-tune', 'low', 'Shrink over-requested exec configs so the '
              'autoscaler can bin-pack nodes away.'),
    ]),

    # ── documented gaps: detectable but NOT agent-remediable ─────────────────
    ('cap-diphome-nfs', None),        # moving DIP_HOME off NFS is a migration project
    ('cap-runtime-db', None),         # H2 → Postgres runtime-DB migration is manual
    ('cap-cgroups-missing', None),    # impersonation+cgroups pairing is installer-level
    ('impersonation-disabled', None),  # UIF setup is an installer-level change
    ('memory-*', None),               # host RAM sizing is infrastructure
    ('open-files-low', None),         # ulimits live in systemd/limits.conf
    ('spark-version-old', None),      # Spark upgrades are managed installs
    ('pod-imagepull-failure', None),  # registry/image fixes live outside DSS
    ('node-not-ready', None),         # node recovery is cloud-infra territory
    ('gpu-node-idle', None),          # scale-down is an autoscaler/capacity decision
    ('cluster-floor-projection', None),  # bin-pack consolidation = capacity planning
    ('sanity-*', None),               # catch-all: un-routed sanity codes are documented
                                      # manual (specific code routes above win first)
]


def remediations_for(issue_id):
    """First-match specs for one finding id. Returns [] when the finding is a
    documented gap or simply unmapped."""
    for glob, specs in REMEDIATIONS:
        if fnmatchcase(issue_id or '', glob):
            return specs or []
    return []


def is_documented_gap(issue_id):
    for glob, specs in REMEDIATIONS:
        if fnmatchcase(issue_id or '', glob):
            return specs is None
    return False


# Actions that can NEVER run autonomously, whatever the stored autonomy map
# says — python-run's whole safety story is the per-run human code ack, which
# an autonomous tier structurally cannot provide. Enforced at four layers:
# route 400 (agent_gates), action_gates hard floor, auto_candidates
# subtraction, and the LLM planner's propose_fix refusal (auto_agent).
AUTO_EXCLUDED = frozenset({'python-run'})

# Admin-facing copy — what granting an action autonomy actually means, in
# plain language. Curated for the classic deterministic-tier actions;
# autonomous_description() generates honest fallback copy for the rest.
AUTO_DESCRIPTIONS = {
    'log-cleanup': 'Delete aged rotated logs under the whitelisted log roots when a disk '
                   'fills up. Oldest-first, capped by the GB budget below.',
    'docker-prune': 'Prune the Docker builder/image cache when it crowds the data mount. '
                    'Rebuilt on demand; keeps the configured storage floor.',
    'job-logs-cleanup': 'Remove job directories older than %d days when disk pressure '
                        'builds. The newest runs of every project always survive.'
                        % _JOB_LOGS_MIN_AGE_DAYS,
    'connection-test': 'Re-probe connections that failed their health test and record '
                       'pass/fail — a zero-mutation verification so the morning report '
                       'says "still broken" or "recovered", not "unknown".',
    'notebook-kernels-shutdown': 'Shut down ALL active notebook kernels when the DSS sanity '
                                 'check flags long-running/leaked kernels. Files and outputs '
                                 'untouched — but in-memory state of every kernel on the host '
                                 'is lost, so enable this only if overnight kernels are '
                                 'never legitimate here.',
    'project-clear-webapp-runs': 'Trim dead webapp run directories in the projects the '
                                 'footprint scan flags as oversized. Running backends and '
                                 'the newest runs per webapp are never touched.',
}


def autonomous_description(action):
    """What autonomy means for one action, for prompts and admin surfaces:
    curated copy where it exists, an honest generated line otherwise."""
    if action in AUTO_DESCRIPTIONS:
        return AUTO_DESCRIPTIONS[action]
    return ('May be planned and executed by the nightly agent without a human in '
            'the loop when a flagged finding warrants it — same plan → confirm-token '
            '→ audit path as a human-approved run.')


def auto_catalog():
    """Deduped, admin-facing catalog of every auto-eligible action:
    [{action, risk, description, findings: [glob, ...]}], sorted by action.
    Single source for the Permissions panel and the settings validator."""
    by_action = {}
    for glob, specs in REMEDIATIONS:
        for spec in (specs or []):
            if not spec['auto'] or spec['action'] in AUTO_EXCLUDED:
                continue
            row = by_action.setdefault(spec['action'], {
                'action': spec['action'], 'risk': spec['risk'],
                'description': AUTO_DESCRIPTIONS.get(spec['action'], spec['why']),
                'findings': []})
            if glob not in row['findings']:
                row['findings'].append(glob)
    return [by_action[a] for a in sorted(by_action)]


def auto_candidates(issues, enabled_actions, settings):
    """Autonomous-fix candidates for one host's finding list.

    `issues` = [{'id': ..., ...}] (health topIssues rows), `enabled_actions` =
    the admin's auto_remediate_actions CSV as a set. One candidate per action
    per host (three disk findings still mean ONE log-cleanup run). Returns
    [{'issueId', 'action', 'target', 'why', 'risk'}] — `target` is a single
    target dict, or a LIST of target dicts for a batched plan.
    """
    out = []
    seen_actions = set()
    enabled_actions = set(enabled_actions or ()) - AUTO_EXCLUDED
    for issue in issues or []:
        issue_id = (issue or {}).get('id') or ''
        for spec in remediations_for(issue_id):
            if not spec['auto'] or spec['action'] not in enabled_actions:
                continue
            if spec['action'] in seen_actions:
                continue
            build = spec['build_target']
            target = build(issue, settings) if build else None
            if not target:
                continue
            seen_actions.add(spec['action'])
            out.append({'issueId': issue_id, 'action': spec['action'],
                        'target': target, 'why': spec['why'], 'risk': spec['risk']})
    return out


def prompt_table():
    """Compact text table for agent prompts: which findings map to which
    catalogued actions (and which are documented manual gaps)."""
    lines = []
    for glob, specs in REMEDIATIONS:
        if specs is None:
            lines.append('%s -> MANUAL (no catalogued action; recommend, do not improvise)' % glob)
            continue
        for spec in specs:
            lines.append('%s -> %s (risk %s%s): %s'
                         % (glob, spec['action'], spec['risk'],
                            ', auto-eligible' if spec['auto'] else '', spec['why']))
    return '\n'.join(lines)
