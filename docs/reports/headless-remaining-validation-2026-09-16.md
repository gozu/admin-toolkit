# Remaining Headless checks on akaos — 2026-09-16 UTC

Measured plugin: **0.4.868**. Whole-task Legacy LLM Mesh versus actual Dataiku Headless MCP.

**5 passed**, **0 failed**, **1 blocked** among the six follow-up checks. `plugin-deploy` remains excluded.

| Capability | Outcome | Legacy task | Headless task | Headless / Legacy |
|---|---|---:|---:|---:|
| cluster-start | passed | 835.18s | 874.25s | 1.05× |
| cluster-stop | passed | 544.08s | 557.58s | 1.02× |
| cluster-pods-cleanup | passed | 14.73s | 14.18s | 0.96× |
| k8s-apply-fix | passed | 15.35s | 17.18s | 1.12× |
| image-delete | blocked | — | — | — |
| python-run | passed | 12.77s | 14.16s | 1.11× |

Task timings include model turns and ADTK execution, but exclude fixture reset and independent verification after the final answer. Model-turn timings include transport and polling, not just provider inference. Cloud stop can return before AWS finishes deleting the control plane; the controller waited for that deletion separately. These are single paired trials, not a reliability benchmark.

## What was exercised

- Cluster lifecycle: the same definition was provisioned and deleted sequentially by both modes. Each start was independently checked for exactly one Ready `t3.small` worker. EKS, EC2 and both CloudFormation stacks had to be absent before the next start.
- Kubernetes: both modes labeled an owned ConfigMap and removed an owned finished pod. The pod fixture refused cleanup if any unowned completed pod or job was present.
- Python: exactly `print(6 * 7)` once per mode, with stdout `42`, empty stderr, exit code zero and no timeout verified.
- Images: No image deleted. Eligible old-image dry run passed; current-image age guard rejected deletion. Specific approval for the two pre-existing DSS 15.0.0 base images is pending.

Headless selected tools and generated its answer through actual MCP conversation calls. The deterministic executor used ADTK's existing execution path, with no nested per-operation Cobuild bridge. Legacy used DSS `mainLLMId`; application model overrides were not applied. No underlying Cobuild model identity, credits, or same-model comparison is inferred. Fixture authorization remains confined to the test harness; production approval code was not changed.

## Cloud timing

- cluster-start, legacy: 835.18s total; 11.60s in model turns and 823.58s in ADTK tools.
- cluster-start, headless: 874.25s total; 19.08s in model turns and 855.16s in ADTK tools.
- cluster-stop, legacy: 544.08s total; 10.01s in model turns and 534.07s in ADTK tools.
- cluster-stop, headless: 557.58s total; 13.06s in model turns and 544.52s in ADTK tools.

During Legacy teardown, CoreDNS and metrics-server disruption budgets allowed zero further disruptions and replacement pods were pending. That observation is consistent with time spent draining a single-node group. AWS documents a drain grace period before forced termination in its [managed node-group deletion procedure](https://docs.aws.amazon.com/eks/latest/userguide/delete-managed-node-group.html). Cloud lifecycle time must not be interpreted as model overhead.

## Cleanup and controls

- No test EKS cluster, nonterminated worker or control-plane/node-group stack remains. No volumes carrying the checked cluster ownership tags remain.
- The DSS cluster definition, temporary comparison plugin, its isolated environment, and the read-only observer plugin were removed.
- Existing EKS clusters remain present. No customer instance was targeted.
- Capability gates and autonomous selections match the preflight snapshot. The saved reasoning mode remains **Legacy**.
- Fresh successful audit records were verified: cluster-pods-cleanup: 2, cluster-start: 2, cluster-stop: 2, k8s-apply-fix: 2, python-run: 2.
- Backend verification: **946 passed + 23 subtests**. Maintenance audit: version metadata aligned at 0.4.868; only the pre-existing unreferenced overview screenshot warning.

One fixture asset-path error was fixed and one broad read-only registry preflight was stopped before cloud creation. Later inventory used akaos's DSS installation ID. These setup attempts are retained in the private journal; no uncertain write was resubmitted.

## Coverage and remaining limit

**62 passed**, **0 failed**, **1 blocked**, **1 excluded** across the two dated reports. Original unaffected cases were measured on 0.4.867; this follow-up measured only the six previously blocked cases on 0.4.868. This is not a full rerun on one release.

The [original report](headless-validation-2026-09-15.md) is preserved. The original 240-second Headless stall still has no established root cause, so these successful tasks do not establish equal reliability or justify changing the saved default automatically.

See the [live-fixture workflow](../agent-workflows/headless-live-fixtures.md). Public evidence contains only outcomes, timings and verification summaries; raw transcripts, targets, exceptions and credentials are not included.
