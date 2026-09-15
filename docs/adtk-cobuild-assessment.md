# ADTK with Cobuild: what stays, what changes

**Historical operation-bridge assessment.** The 64-check suite now has a
[whole-task model replacement runner](agent-workflows/cobuild-model-comparison.md).
Its [new measurements](reports/adtk-cobuild-model-replacement.html) include model
selection and final generation on both paths. The historical passes and timings
below are not replacement-run results and do not change live application routing.

**63 of 64 capabilities matched within their tested functional scope.** `plugin-deploy` is **skipped at the user’s request**, not failed. The latest retries resolved Python execution, cluster start, cluster stop, pod cleanup and image deletion. ADTK **0.4.863** is deployed to akaos and TAMGLOBAL; live comparisons were performed on akaos.

The [CSV](adtk-cobuild-comparison.csv) and [interactive table](adtk-cobuild-comparison.html) contain all **11 sensors and 53 actions**, with purpose, route, retained ADTK responsibilities, measured verdict, timings, scope and remaining limits. Search across columns, filter by verdict or type, sort, show route details, or export the filtered rows. These files and this assessment are also in the user’s Public folder.

Each capability has an **Existing / Headless-Cobuild** switch in Agents → Permissions. Existing remains the default. Changing the provider does not grant execution permission or autonomous approval. The actions are capabilities behind the plan/execute entry points, not 53 separately registered DSS tools.

The original Cobuild path is **Cobuild requests the operation → ADTK executes it → Cobuild explains the actual result**. Version 0.4.862 optimizes three selected reads: **ADTK reads → full data returned → one background Cobuild interpretation**. These are `toolkit_get(endpoint="version")` without field projection or parameters, `list_hosts(probe=False)`, and `list_capabilities()`. Other arguments and actions retain the original bridge. **Second Look reviews are omitted at the user’s request**; Cobuild calls still use the service, and existing action permissions and confirmations remain in force. It calls the public Cobuild SDK service behind Headless. It does not launch the Headless MCP server or replace ADTK executors with native Headless tools. **No ADTK executor can be removed on the strength of these comparisons.**

If all agent inference moves to Cobuild, ADTK would still provide:

- Host selection, credentials, host macros and access to the actual admin systems.
- Sensors, health scoring, diagnostic evidence and all action executors.
- Usage checks, plans, previews, backups, permissions, confirmations and token validation.
- Audit/history, scenarios, scheduling and the agent interface.
- The rest of the administration application, including its non-agent features.

The portion that could change is the conversation/model integration: Cobuild could select operations and explain results within one ongoing conversation, using ADTK’s controlled executors. The current per-operation integration retains the outer chat model. Most operations use the original two-exchange bridge; the three-read pilot uses one interpretation exchange.

**The credit objective is only partly implemented.** Switched operations use Cobuild. Under the supplied billing model, Cobuild usage credits pay for the service separately from BYO LLM token charges. The API responses used here did not expose billed credit totals, so the table does not invent a per-tool credit cost. **The outer agent chat model remains on LLM Mesh; all in-scope agent inference has not yet moved to Cobuild.** Standalone log AI analysis, failed code-environment advice and report generation remain excluded, as requested. Agent tools that inspect logs or administer code environments remain included.

The latest successful comparisons were:

| Capability | Existing seconds | Cobuild seconds | Verified scope |
|---|---:|---:|---|
| `python-run` | 0.111 | 16.184 | Exactly two approved executions of `print(6 * 7)`; output, errors, exit code and timeout checked |
| `cluster-start` | 807.776 | 793.451 | Sequential provisioning on each path; exactly one Ready t3.small node |
| `cluster-stop` | 558.887 | 561.405 | Both stop operations; subsequent independent verification of cloud-resource deletion |
| `cluster-pods-cleanup` | 3.125 | 18.200 | One completed owned pod in DSS’s current namespace per path |
| `image-delete` | 3.669 | 20.707 | The two authorized oldest akaos DSS 14.7.0 base images, one per path; other images in those repositories preserved |

The latency verdict reports **Cobuild seconds ÷ Existing seconds**: “2.3× slower” means Cobuild took 2.3 times as long in that sample. Ratios below one are shown as a fraction of Existing time, with the cache/order caveat retained.

Cluster timings exclude planning and include variable AWS provisioning/teardown time; they do not isolate Cobuild overhead. Other action timings include planning and execution. These are single successful samples, not latency guarantees. **Functional matches do not establish equal speed or explanation quality.** Simple operations were generally slower through the original bridge. The new three-read pilot has separate repeated measurements below; historical columns are retained without relabeling them as optimized results. Planning and execution each add a request/explanation exchange. A result hash acknowledgment verifies the returned identifier, not the correctness of the explanation or an independent security review. A failed explanation must never cause an already completed mutation to run again.

The retries uncovered and fixed concrete issues:

- DSS 15 stores registry destinations behind image-build configuration references. ADTK now resolves these references while retaining older layouts.
- The webapp could report an ECR image as missing while the DSS host could see it. ECR deletion plans and execution now use the selected host’s existing macro, including for the local instance. Cutoff checks, dry runs and confirmation controls remain. Local non-ECR adapters retain their existing path.
- Agent cluster start/stop now wait at least 30 minutes, honoring larger configured timeouts. Mutation timeouts report an unknown outcome and do not automatically repeat the action.
- Cobuild operation conversations are retained for up to one hour while ADTK executes, preventing unrelated requests from discarding a normal long-running operation after ten minutes.
- The pod test initially used another namespace. Correcting the fixture to DSS’s current namespace and checking eventual deletion made the comparison pass; this was not a Cobuild capability failure.

Earlier retries also resolved project deletion, continuous-activity stop after streaming was licensed, notebook-kernel shutdown, installed-plugin uninstall and store update. Installed plugins are backed up through a selected-host macro because the previous download API exports development plugins ([DSS API reference](https://doc.dataiku.com/dss/api/latest/rest/)). Uninstall tests checked binary preservation and reinstallation from the backups. Store updates use the configured long-operation timeout. Fixture setup failures and interrupted backends were tracked separately from meaningful Cobuild improvement attempts; comparison runners cap candidate attempts at three and reconcile or reset owned fixtures before repeating mutations.

**Scope limits remain explicit.** `config_inspect` covered project inventory; `toolkit_get` covered the version endpoint; `k8s_health` covered an empty cluster inventory. Docker coverage is image pruning, not builder-cache pruning. Database maintenance used a disposable 10-row table and a temporarily configured credential that was restored. Pod cleanup was tested in the current namespace; completed-job cleanup is still an untested branch. Store update replaced an owned empty placeholder with the store version, not an in-use plugin migration. Non-admin role parity and systematic explanation-quality evaluation remain untested. Audit/history were independently checked for project-variable changes; the earlier local runner could not persist audit rows. None of this is a claim of 64 complete tool certifications.

Headless’s project-oriented Flow, recipe, notebook and agent-definition capabilities could expand ADTK investigations. ADTK diagnostic procedures and evidence checklists are useful candidates for Headless skills. Execution safeguards, approval enforcement and secret handling must remain implemented controls. Application rendering, navigation and session stores have no direct use as Headless skills. Native replacement of the existing admin executors has not been established.

**Cleanup and validation are complete for this retry batch.** The two approved images were independently verified absent. Both sequential test clusters were removed, including their EKS control planes, workers, node-group stacks, control-plane stacks and DSS attachment. The temporary macro plugin, code environment and project variable were removed; touched capability settings were restored. DSS successfully rebuilt its normal shared plugin container image afterward. Historical image layers/cache were not pruned. The dedicated empty `ATKCOBUILDTEST` project remains for earlier Cobuild traces.

Validation passed: **850 backend tests and 23 subtests**, frontend typecheck, packaged build and contract checks. Chromium verified 64 table rows, 63 matched rows, one skipped row, filters, numeric sorting, route details and filtered CSV export. The maintenance audit has no errors; its pre-existing unreferenced overview screenshot warning remains. Public evidence excludes credentials, confirmation tokens, raw customer diagnostics and model transcripts.

## Read-speed pilot, 15 September 2026

Ten alternating trials per path per read were run on deployed 0.4.862. Historical timings above remain unchanged. The new spreadsheet columns separate the pilot’s data-return time, interpretation-completion time, success count, and full-output matches.

| Read | Existing median | Original bridge median | Pilot data median | Pilot complete median | Pilot successes |
|---|---:|---:|---:|---:|---:|
| `toolkit_get` | 0.756s | 11.081s | 2.285s | 8.323s | 10 / 10 |
| `list_hosts` | 0.76s | 11.095s | 2.284s | 8.312s | 10 / 10 |
| `list_capabilities` | 0.001s | 14.235s | 1.538s | 7.559s | 10 / 10 |

Full ranges, failures, cache caveats, validation boundaries, and every trial are in the [performance report](reports/adtk-cobuild-performance.md). The pilot makes data available before inference finishes; it still has HTTP overhead compared with Existing. Capability-list baselines may use a warm permission cache. These are tool-path measurements, not whole-chat benchmarks or billed credit measurements. Second Look is omitted.

The repeated timing benchmark used 0.4.862. Deployed **0.4.863** additionally tightens version-summary wording after one explanation overreached about upgrades; three follow-up reads matched full outputs and stayed within the supplied evidence. See the performance report for that limitation and correction.
