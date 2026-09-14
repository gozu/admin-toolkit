# Headless or Cobuild or Neither

For every tool, read, action and skill the Admin Toolkit agents carry today: can
Dataiku Headless do it, can Cobuild do it as a fallback, or must it stay in the
Toolkit, and why.

Checked 14 September 2026 against Headless 0.6.0 (commit `9d7f6cc`, 128 tools),
Toolkit 0.4.830 source, and live runs on TAMGLOBAL as the non-admin user
`kaosdex` in project `KAOSHEADLESS`. The HTML version of this report is
`docs/headless-disposition-report.html`.

## The short answer

Headless covers the plain DSS inventory work the Toolkit agents do (projects,
connections, code envs, plugins, users, LLM connections, scenarios, webapps,
jobs, datasets) plus fourteen of the fifty-three admin actions. Cobuild adds
in-project building and deletion with its own confirmation flow. Everything that
makes the Toolkit an admin product falls outside both: host filesystem and logs,
Kubernetes and PostgreSQL health, cost and adoption analytics from audit logs,
cluster and connection writes, fleet routing, and the plan-confirm-execute
safety layer.

- Headless can take over 10 of 19 config domains, 14 of 53 actions, and the
  read-only halves of 2 curated reads.
- Cobuild is a real fallback for 5 actions that change project assets, and for
  narrative synthesis if the Toolkit hands it evidence.
- Neither can do all 11 sensors as designed, 19 of 21 curated reads, 8 config
  domains and 34 actions.
- Cobuild is a public REST API in dataiku-api-client since 14.7.2
  (`DSSProject.new_cobuild_conversation`, `send_message`, `answer_confirmation`,
  `answer_question`). The Toolkit backend can call it directly without the
  Headless MCP wrapper.

## The three options

| | Headless (preferred) | Cobuild (fallback) | Toolkit (neither) |
| --- | --- | --- | --- |
| Runs | Operator's laptop, MCP server inside Codex or Claude Code | Inside DSS, scoped to one project | Inside DSS, plus host macros |
| Identity | Operator's own API key; users, groups, licensing, general settings refuse non-admins | Calling user, project permissions | Admin key |
| Reaches | One configured instance, public API only | Project assets | Admin API, DIP_HOME, backend.log, kubectl, runtime PostgreSQL, registries, a fleet |
| Reasoning | The harness model | Metered Dataiku AI Services usage, DSS 14.7+ | LLM Mesh today |

## Disposition at a glance

| Verdict | Count | Breakdown |
| --- | --- | --- |
| Headless | 24 | 10 domains, 14 actions |
| Partial (Headless supplies the raw read, Toolkit keeps the analysis) | 5 | 2 sensors, 1 domain, 2 reads |
| Cobuild | 5 | 5 actions |
| Neither | 70 | 9 sensors, 8 domains, 19 reads, 34 actions |

Counts cover the 104 concrete entry points. Prompts and skills are instruction
text and are handled separately, because text alone does not give any surface a
tool it lacks.

## Why "neither", in five buckets

1. **Host access.** DIP_HOME tree, backend.log, tmp and export folders, Docker,
   process table. Headless is public-API only; Cobuild is project-only.
2. **Admin API not wrapped.** The public API has it, Headless has no tool:
   clusters, API keys, connection writes, global variables, general settings
   writes, notebook kernels, webapp backends, continuous activities, scenario
   abort, project delete/export/owner, messaging.
3. **Derived analytics.** Audit logs or cross-instance scans plus Toolkit rules:
   health score and sanity, CRU cost, adoption and churn, LLM audit,
   app-instance attribution, compute placement.
4. **Infrastructure beyond DSS.** kubectl, runtime PostgreSQL, container
   registries.
5. **Toolkit-internal.** Capability manifest, action audit timeline, settings
   snapshot, version, agent-authored Python, Toolkit scenario writer.

## Sensors (11)

| Sensor | Verdict | Headless equivalent | Why not more |
| --- | --- | --- | --- |
| list_hosts | Partial | list_instances, switch_instance | Local profiles, one active instance, no routing or probe |
| config_inspect | Partial | per domain | 10 of 19 domains map |
| instance_health | Neither | none | Bucket 3 |
| compute_cost | Neither | none | Bucket 3 |
| log_errors | Neither | get_job_log only | Bucket 1 |
| log_tail | Neither | none | Bucket 1 |
| storage_footprint | Neither | none | Bucket 1 |
| k8s_health | Neither | none | Bucket 4 |
| db_health | Neither | none | Bucket 4 |
| toolkit_get | Neither | per endpoint | 19 of 21 are Toolkit analyses |
| list_capabilities | Neither | none | Bucket 5 |

## Config domains (19)

| Domain | Verdict | Headless tools | Notes |
| --- | --- | --- | --- |
| projects | Headless | list_projects, get_project_metadata, get_project_settings | Live-verified non-admin |
| connections | Headless | list_connections, get_connection_info, test_connection | Keys redacted upstream |
| connections-usage | Partial | list_datasets per project | Cross-project matrix, owner emails, trigger flag stay in Toolkit |
| code-envs | Headless | list_code_envs (package filter) | Size, deprecated-Python, unused analysis stay in Toolkit |
| plugins | Headless | list_plugins, list_plugin_usages | |
| llms | Headless | list_llms, get_llm_info | |
| clusters | Neither | none | Bucket 2 |
| users | Headless | list_users | Admin-gated upstream |
| api-keys | Neither | none | Bucket 2 |
| scenarios | Headless | list_scenarios, get_scenario_settings, get_scenario_run_history | Run history is a gain |
| webapps | Headless | list_webapps, get_webapp_state | Read only |
| notebooks | Neither | none | Bucket 2 |
| jobs | Headless | list_jobs, get_job_status, wait_for_job | Wait is a gain |
| datasets | Headless | list_datasets, get_flow_graph, get_flow_object_metadata | Lineage is a gain |
| continuous-activities | Neither | none | Bucket 2 |
| app-instances | Neither | none | Bucket 3, public API strips creator id |
| adoption | Neither | none | Bucket 3 |
| settings | Neither | list_container_exec_configs, list_spark_configs only | Bucket 2 |
| cost-detail | Neither | none | Bucket 3 |

## Curated reads (21)

| Read | Verdict | Why |
| --- | --- | --- |
| connections-audit | Partial | get_connection_info gives raw params; findings are Toolkit rules |
| container-execs | Partial | list_container_exec_configs; per-project usage is a Toolkit scan |
| errors, dir-tree, docker-usage, resources-snapshot, resources-processes | Neither | Bucket 1 |
| users-churn, compute-placement, cost-cru-detail, llm-audit, cs-templates, cs-template-projects | Neither | Bucket 3 |
| k8s-insights, db-health-connections, db-health-overview, db-health-tables, db-health-per-project | Neither | Bucket 4 |
| audit-timeline, settings-snapshot, version | Neither | Bucket 5 |

## Admin actions (53)

Headless direct writes have no dry run and no confirm token. Cobuild has a
confirmation step for deletions only.

| Action | Risk | Verdict | Route |
| --- | --- | --- | --- |
| connection-test | green | Headless | test_connection |
| code-env-update | amber | Headless | update_code_env |
| code-env-delete | red | Headless | delete_code_env, no usage check upstream |
| plugin-update | amber | Headless | update_plugin source=store |
| plugin-deploy | amber | Headless | update_plugin source=local_path |
| plugin-code-env-rebuild | amber | Headless | update_plugin rebuild_code_env=true |
| plugin-uninstall | red | Headless | delete_plugin, no zip backup upstream |
| user-update, user-enable, user-disable | amber/red | Headless | update_user; no self-lockout guard upstream |
| project-variables-set | amber | Headless | set_project_variables |
| project-set-cluster | amber | Headless | update_project_settings; no cluster existence guard upstream |
| scenario-run | amber | Headless | run_scenario |
| job-kill | amber | Headless | abort_job |
| dataset-delete | red | Cobuild | deletion confirmation flow; drop-data semantics unverified |
| scenario-enable, scenario-disable | amber | Cobuild | not live-tested |
| toolkit-scenario-write | amber | Cobuild | step set becomes prompt text |
| notebook-clear-outputs | amber | Cobuild | not live-tested |
| tmp-cleanup, exports-cleanup, job-logs-cleanup, log-cleanup, docker-prune | amber | Neither | Bucket 1 |
| connection-update, connection-delete, connection-index | amber/red | Neither | Bucket 2 |
| cluster-start, cluster-stop, cluster-detach, cluster-pods-cleanup, k8s-apply-fix, k8s-exec-config-tune | mixed | Neither | Buckets 2 and 4 |
| project-delete, project-export, project-change-owner, project-clear-webapp-runs | mixed | Neither | Bucket 2; Cobuild deletes objects, not projects |
| scenario-kill, continuous-activity-stop, webapp-backend-stop, webapp-backend-restart, notebook-kernels-shutdown | amber | Neither | Bucket 2; abort_job does not abort scenarios |
| variables-set, settings-set, api-key-delete, notification-send | amber/red | Neither | Bucket 2 |
| code-env-consolidate, image-delete, dataset-clear | amber/red | Neither | workflow / bucket 4 / no upstream tool |
| db-vacuum, db-analyze, db-reindex | amber | Neither | Bucket 4 |
| python-run | red | Neither | Bucket 5; Headless forbids raw Python fallback |

## What overlaps, and what to do about it

Two ways to remove the duplication:

- Give the Toolkit agent Headless tools: import its tool functions into the
  plugin backend. Works for reads. For writes it strips the plan step, confirm
  token, backups and audit.
- Give Headless the Toolkit's knowledge: port playbooks as reference guides.
  Cheaper, and reaches operators who never install the Toolkit.

Recommendation: do the second for the ten covered domains and the green and
amber actions. Keep Toolkit executors for every red action even where Headless
has a raw tool.

## In Headless, not in the Toolkit agents, worth adopting

| Headless capability | Gain for the Toolkit agent |
| --- | --- |
| get_flow_graph, get_flow_object_metadata, list_flow_zones | Real lineage before dataset-clear / dataset-delete |
| get_dataset_profile, get_dataset_metrics, get_dataset_sample | Size and shape evidence without a filesystem walk |
| list_code_envs package filter | CVE and deprecation sweeps by package |
| list_plugin_usages | Replaces the Toolkit plugin usage scan |
| get_scenario_run_history, wait_for_job, get_future_status | Verify actions finished instead of "submitted" |
| Data quality rules and status | New per-project health signal |
| Agents, agent tools, agent reviews, knowledge banks, RAG LLMs | GenAI estate inventory for LLM audit |
| list_shared_objects, data collections | Exposure checks before project-delete / connection-delete |
| Project libraries read and validate | Find hard-coded connection names before a rename |

## Toolkit skills worth porting to Headless

Port as reference guides: the action safety pattern (plan, target shape, drift
check, execute), remediation map and severity playbook, code-env hygiene rules,
plugin drift across two profiles, connection audit rules, scoping prompts for
projects and envs.

Not useful to Headless: repository maintenance skill, frontend contracts and
stores, fleet prompts, host / Kubernetes / database / Docker prompts, cost and
adoption prompts, safety drills, system prompts and rubrics as-is.

## What else this report surfaces

| Dimension | Headless | Cobuild | Toolkit |
| --- | --- | --- | --- |
| Who runs it | Operator's laptop | DSS server, in a project | DSS server plus host macros |
| Whose key | Personal key; non-admin refused on users, groups, licensing, settings (verified) | Calling user | Admin key |
| Scope | One instance at a time | One project per conversation | Fleet |
| Who pays for reasoning | Harness subscription | AI Services usage then AI Credits | LLM Mesh |
| Safety model | Read-before-write rule; no dry run | Deletion confirmation only | Plan, confirm token, drift check, backups, gates, kill switch, audit |
| DSS version | Client pinned 14.7.2 | Conversation API needs 14.7+ | Any supported |
| Durability | Process-local conversations | Retained in DSS, no cancel API | Persisted |
| Can call the other's tools | No (verified live) | No | Could call Cobuild via public client |
| Customer readiness | Apache 2.0, guide marked internal | Licensed, credits | Internal plugin |

## What was verified

- Headless tool inventory read from source at `9d7f6cc`, cloned fresh. The
  128-tool split is upstream's own count in `docs/capabilities.md`.
- `require_admin()` called only in users, groups, licensing, general settings.
- Toolkit catalogs imported from `tools_impl`, `domain_registry`,
  `read_registry`, `actions`: 11 sensors, 19 domains, 21 reads, 53 actions.
- Live on TAMGLOBAL as kaosdex (DSS 14.7.3, `docs/headless-integration-evidence.json`):
  seven reads succeeded, licensing refused, Cobuild created an agent tool and a
  native agent, Cobuild reported it cannot invoke plugin tools, macros, users or
  host state.
- Cobuild API confirmed in dataiku-api-client 14.7.2 source.
- Not verified: the Cobuild-fallback actions marked not live-tested, and any
  Cobuild usage debit.

## Recommended sequence

1. Port the six guide-worthy playbooks to Headless reference format and open
   them upstream.
2. Adopt flow-graph, dataset-profile, plugin-usage and job-wait reads as new
   Toolkit sensors through the existing admin client.
3. Pilot Cobuild as the synthesis step for the scheduled triage sweep, via the
   public client. Measure a usage delta on a customer-scoped identity first.
4. Ask upstream for a dry-run flag on admin writes, cluster and connection write
   tools, scenario abort, and a way for Cobuild to call registered plugin tools.
