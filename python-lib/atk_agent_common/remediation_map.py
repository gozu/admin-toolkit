"""Finding → remediation registry.

Maps health-score issue ids (atk_agent_common.health) and k8s-insights rule
ids to catalogued actuator actions. First glob match wins. `auto: True` marks
the actions the daily triage loop may execute autonomously WHEN the admin has
opted that action into `auto_remediate_actions` — only the reversible,
capped, whitelist-safe cleanups qualify (log-cleanup, docker-prune).

Explicit `None` entries document known gaps: findings we can detect but not
remediate through any catalogued action (so agents say "manual" instead of
improvising).
"""

from fnmatch import fnmatchcase


def _log_cleanup_target(issue, settings):
    return {'roots': [],  # empty = every whitelisted root
            'minAgeDays': int(settings.get('log_cleanup_min_age_days') or 3),
            'maxDeleteGB': int(settings.get('auto_remediate_max_gb') or 20)}


def _docker_prune_target(issue, settings):
    return {'mode': 'builder',
            'keepStorageGB': int(settings.get('auto_remediate_max_gb') or 20)}


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
    ]),
    ('disk-critical-*', [
        _spec('log-cleanup', 'low', 'Reclaim rotated logs before anything invasive.',
              auto=True, build_target=_log_cleanup_target),
        _spec('docker-prune', 'low', 'Prune docker build/image cache if docker lives on the '
              'affected mount.', auto=True, build_target=_docker_prune_target),
    ]),
    ('disk-warning-*', [
        _spec('log-cleanup', 'low', 'Early cleanup keeps the warning from becoming critical.',
              auto=True, build_target=_log_cleanup_target),
        _spec('job-logs-cleanup', 'low', 'Aged job directories (jobs/<PROJECT>/<jobDir>) are '
              'the next-safest reclaim after rotated logs — newest N per project survive; '
              'severity is judged by share of the /data disk (rubric).'),
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
              'reports connectionOK true/false.'),
    ]),
    ('connection-broken-unused', [
        _spec('connection-delete', 'medium', 'Nothing references the connection — back up its '
              'definition JSON and delete it (batchable: one item, N names).'),
        _spec('connection-update', 'medium', 'Or repair it instead when the admin wants to '
              'keep it (path into the definition, drift-guarded).'),
    ]),
    ('connection-broken-unverified', [
        _spec('connection-test', 'low', 'Re-probe the connection; run the usage scan before '
              'proposing anything destructive.'),
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
        _spec('code-env-consolidate', 'medium', 'Consolidate per-project env sprawl onto shared '
              'envs; the plan enumerates every recipe/notebook/webapp/scenario touched.'),
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
              'per webapp, never touches a running backend.'),
    ]),

    # ── runtime workloads (sanity codes surface as sanity-warning-<CODE>) ────
    ('sanity-*LONG_RUNNING*', [
        _spec('notebook-kernels-shutdown', 'medium', 'Kernels alive beyond ~days rarely do '
              'real work (rubric): shut down the active kernels via the DSS API — files and '
              'outputs untouched, users just restart. Never a Linux-level kill.'),
    ]),
    ('sanity-*JUPYTER*', [
        _spec('notebook-kernels-shutdown', 'medium', 'Idle/leaked notebook kernels are '
              'reclaimed at the DSS level; the plan lists every kernel before approval.'),
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

    # ── documented gaps: detectable but NOT agent-remediable ─────────────────
    ('cap-diphome-nfs', None),        # moving DIP_HOME off NFS is a migration project
    ('cap-runtime-db', None),         # H2 → Postgres runtime-DB migration is manual
    ('impersonation-disabled', None),  # UIF setup is an installer-level change
    ('memory-*', None),               # host RAM sizing is infrastructure
    ('open-files-low', None),         # ulimits live in systemd/limits.conf
    ('spark-version-old', None),      # Spark upgrades are managed installs
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


# Actions that can NEVER run autonomously, whatever the admin's CSV says —
# python-run's whole safety story is the per-run human code ack, which an
# autonomous tier structurally cannot provide.
AUTO_EXCLUDED = frozenset({'python-run'})


def auto_candidates(issues, enabled_actions, settings):
    """Autonomous-fix candidates for one host's finding list.

    `issues` = [{'id': ..., ...}] (health topIssues rows), `enabled_actions` =
    the admin's auto_remediate_actions CSV as a set. One candidate per action
    per host (three disk findings still mean ONE log-cleanup run). Returns
    [{'issueId', 'action', 'target', 'why'}].
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
            if target is None:
                continue
            seen_actions.add(spec['action'])
            out.append({'issueId': issue_id, 'action': spec['action'],
                        'target': target, 'why': spec['why']})
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
