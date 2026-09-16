# Fresh Legacy versus Headless validation

The [0.4.868 follow-up](headless-remaining-validation-2026-09-16.md) covers the six cases blocked in this dated report. The measurements below remain unchanged.

Measured plugin: **0.4.867**. Transport: actual Dataiku Headless MCP.

**57 passed**, **0 failed**, **6 blocked**, **1 excluded**.

One paired trial per capability with owned, reset fixtures. These results test the comparison adapter; separate production-loop tests cover human approval and autonomous grants. Historical passes are not imported. The Legacy harness uses DSS local AI server mainLLMId; application agent model overrides are not applied. Legacy and Cobuild need not use the same underlying model. No Cobuild model identity or usage credits are assumed.

Across the 57 passing pairs, median Headless/Legacy task-time ratio: **1.10×**. Legacy median: 13.28s; Headless median: 14.31s. This mixes different tasks and includes ADTK execution; it is not a model-only latency or reliability benchmark.

| Capability | Outcome | Legacy | Headless | Headless / Legacy |
|---|---|---:|---:|---:|
| list_hosts | passed | 5.04s | 8.07s | 1.60× |
| instance_health | passed | 19.68s | 22.45s | 1.14× |
| compute_cost | passed | 32.22s | 15.08s | 0.47× |
| config_inspect | passed | 5.29s | 8.06s | 1.52× |
| log_errors | passed | 8.16s | 9.06s | 1.11× |
| log_tail | passed | 5.68s | 7.10s | 1.25× |
| storage_footprint | passed | 9.67s | 12.17s | 1.26× |
| k8s_health | passed | 13.36s | 12.86s | 0.96× |
| toolkit_get | passed | 4.88s | 8.05s | 1.65× |
| list_capabilities | passed | 13.63s | 15.08s | 1.11× |
| scenario-enable | passed | 11.09s | 12.16s | 1.10× |
| scenario-disable | passed | 8.39s | 12.17s | 1.45× |
| project-variables-set | passed | 11.07s | 11.16s | 1.01× |
| variables-set | passed | 10.62s | 12.15s | 1.14× |
| notebook-clear-outputs | passed | 9.50s | 13.13s | 1.38× |
| user-enable | passed | 10.71s | 14.18s | 1.32× |
| user-disable | passed | 9.99s | 12.16s | 1.22× |
| user-update | passed | 12.04s | 14.16s | 1.18× |
| project-change-owner | passed | 9.73s | 11.16s | 1.15× |
| connection-test | passed | 19.70s | 12.12s | 0.62× |
| connection-update | passed | 11.58s | 12.16s | 1.05× |
| connection-delete | passed | 18.35s | 14.51s | 0.79× |
| dataset-clear | passed | 12.16s | 13.15s | 1.08× |
| dataset-delete | passed | 13.28s | 14.31s | 1.08× |
| webapp-backend-stop | passed | 11.01s | 12.16s | 1.10× |
| webapp-backend-restart | passed | 14.75s | 16.16s | 1.09× |
| scenario-run | passed | 10.82s | 18.17s | 1.68× |
| toolkit-scenario-write | passed | 12.37s | 12.18s | 0.98× |
| api-key-delete | passed | 16.73s | 15.15s | 0.91× |
| scenario-kill | passed | 9.39s | 12.13s | 1.29× |
| job-kill | passed | 12.65s | 16.26s | 1.28× |
| code-env-update | passed | 15.42s | 16.97s | 1.10× |
| code-env-delete | passed | 13.02s | 13.51s | 1.04× |
| settings-set | passed | 15.78s | 11.15s | 0.71× |
| k8s-exec-config-tune | passed | 30.05s | 36.20s | 1.20× |
| project-export | passed | 12.84s | 14.35s | 1.12× |
| project-delete | passed | 31.16s | 27.92s | 0.90× |
| plugin-code-env-rebuild | passed | 21.97s | 14.21s | 0.65× |
| plugin-uninstall | passed | 26.03s | 17.78s | 0.68× |
| code-env-consolidate | passed | 12.12s | 17.38s | 1.44× |
| connection-index | passed | 14.04s | 15.27s | 1.09× |
| cluster-detach | passed | 23.13s | 31.10s | 1.34× |
| continuous-activity-stop | passed | 11.82s | 12.40s | 1.05× |
| notebook-kernels-shutdown | passed | 16.70s | 14.36s | 0.86× |
| plugin-update | passed | 79.31s | 83.61s | 1.05× |
| tmp-cleanup | passed | 22.09s | 23.24s | 1.05× |
| exports-cleanup | passed | 13.89s | 15.97s | 1.15× |
| job-logs-cleanup | passed | 14.50s | 18.98s | 1.31× |
| log-cleanup | passed | 16.31s | 17.98s | 1.10× |
| project-clear-webapp-runs | passed | 13.23s | 17.98s | 1.36× |
| docker-prune | passed | 25.44s | 20.17s | 0.79× |
| db_health | passed | 7.71s | 10.10s | 1.31× |
| db-vacuum | passed | 14.56s | 15.20s | 1.04× |
| db-analyze | passed | 18.78s | 14.18s | 0.76× |
| db-reindex | passed | 16.57s | 17.20s | 1.04× |
| notification-send | passed | 13.76s | 15.16s | 1.10× |
| project-set-cluster | passed | 13.44s | 18.29s | 1.36× |
| cluster-start | blocked | — | — | — |
| cluster-stop | blocked | — | — | — |
| cluster-pods-cleanup | blocked | — | — | — |
| k8s-apply-fix | blocked | — | — | — |
| image-delete | blocked | — | — | — |
| python-run | blocked | — | — | — |
| plugin-deploy | excluded | — | — | — |

## Remaining checks

- **cluster-start (blocked):** Needs a new owned cluster lifecycle fixture; previous cloud resources were deleted.
- **cluster-stop (blocked):** Needs a new owned cluster lifecycle fixture; previous cloud resources were deleted.
- **cluster-pods-cleanup (blocked):** Needs a new owned cluster lifecycle fixture; previous cloud resources were deleted.
- **k8s-apply-fix (blocked):** Needs a new owned cluster lifecycle fixture; previous cloud resources were deleted.
- **image-delete (blocked):** Needs new disposable image targets; previous two deletion approvals were consumed.
- **python-run (blocked):** Needs fresh per-run approval of concrete Python code; previous two approvals were consumed.
- **plugin-deploy (excluded):** plugin-deploy remains excluded at the user's request.

## Repeated checks

- **log_tail:** failed → passed. Filtered log observations match; only live window metadata differs. Earlier attempts remain in the JSON evidence.
- **plugin-update:** failed → passed. Both tasks completed with matching verified observations. Earlier attempts remain in the JSON evidence.

## Fixture cleanup

- fixture: verified.
- data: verified.
- runtime: verified.
- native: verified.
- archive: verified.
- consolidate: verified.
- detach: verified.
- continuous: verified.
- notebook: verified.
- store: verified.
- cleanup: verified.
- docker: verified.
- database: verified.
- notification: verified.
- project_cluster: verified.

Only allowlisted aggregate fields are published. Private transcripts, targets, fixture identifiers, postcondition payloads and raw exceptions remain outside this report.
