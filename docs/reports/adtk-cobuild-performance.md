# Cobuild read-speed pilot — measured results

15 September 2026 · akaos · ADTK 0.4.862. Ten trials per operation per variant, with order rotated across repetitions. All 90 attempts are retained; no automatic inference retries, gate changes, provider-setting changes, mutations or cache clears.

| Read | Path | Successes / attempts | Data returned, median (range), seconds | Complete, median (range), seconds | Full outputs matched |
|---|---|---:|---:|---:|---:|
| `toolkit_get` | existing | 10 / 10 | 0.756 (0.747–0.763) | 0.756 (0.748–0.764) | 10 / 10 |
| `toolkit_get` | bridge | 10 / 10 | 11.081 (11.036–12.815) | 11.081 (11.037–12.815) | 10 / 10 |
| `toolkit_get` | compact | 10 / 10 | 2.285 (2.234–2.299) | 8.323 (8.241–10.054) | 10 / 10 |
| `list_hosts` | existing | 10 / 10 | 0.760 (0.745–0.769) | 0.760 (0.745–0.769) | 10 / 10 |
| `list_hosts` | bridge | 10 / 10 | 11.095 (9.329–16.376) | 11.095 (9.329–16.376) | 10 / 10 |
| `list_hosts` | compact | 10 / 10 | 2.284 (2.261–2.294) | 8.312 (8.285–10.102) | 10 / 10 |
| `list_capabilities` | existing | 10 / 10 | 0.000 (0.000–0.790) | 0.001 (0.000–0.790) | 10 / 10 |
| `list_capabilities` | bridge | 10 / 10 | 14.235 (12.073–15.613) | 14.235 (12.073–15.613) | 10 / 10 |
| `list_capabilities` | compact | 10 / 10 | 1.538 (1.516–2.294) | 7.559 (5.785–8.319) | 10 / 10 |

Existing is the deterministic ADTK read, without an LLM explanation. Bridge is the original operation-request/read/result-acknowledgment path. Compact returns the full read result after a live permission check and bounded job submission; it then receives a separate, single Cobuild interpretation. Complete includes client HTTP transport and one-second polling, not just model generation.

The version pilot reads the ADTK plugin version; it is not a DSS version test. Host listing uses `probe=False`. Capability interpretation summarizes inventory and enabled counts; the full catalog remains available to the caller. Other arguments and actions retain the original bridge.

Compact interpretation must preserve typed facts, request ID and evidence hash and supply a nonempty summary. These checks establish fidelity of the structured facts, not a guarantee about free-form prose or independent verification of the instance or a security review. Full output comparisons remove only the new executionRoute metadata. Baselines use the latest Existing read for the same operation; settings stayed unchanged during testing.

Existing capability-list reads can use the process’s 30-second permission cache and may finish below one millisecond. The compact path always checks live permissions. We did not clear shared caches or force equal cache states. Cache/order effects and uncontrolled shared-instance load limit causal comparisons; a near-zero baseline does not support a useful latency ratio.

The compact path still pays for HTTP permission checks and job submission, so it does not match the speed of a plain Existing read. Returning data before interpretation removes inference from that wait. Background explanations are shown separately from the outer chat answer; whole-chat timing and quality were not measured.

Second Look reviews are omitted at the user’s request. Existing action permissions, confirmations, exact-target tokens and audit behavior remain in place. Cobuild service calls continue without this extra review. The outer chat model remains on LLM Mesh; this pilot does not move all agent inference to Cobuild.

Cobuild usage credits remain separate from BYO LLM token billing under the supplied billing model. The API did not expose a billed credit total, so these trials quantify calls and timing, not credits.

Backend validation: 873 tests and 23 subtests passed. Browser tests cover pending, completed, failed and expired interpretation states while retaining read data. Frontend typecheck, build and UI contracts passed. Production uses four workers, at most eight pending jobs, host-bound read-only status tickets, and no batching or conversation reuse.

Historical 63/64 functional matches remain scoped evidence from the earlier comparison. This pilot adds evidence for three read variants only; `plugin-deploy` remains skipped at the user’s request.

[All trial rows](adtk-cobuild-performance-trials.csv) · [Aggregate data](adtk-cobuild-performance.json)

## Wording correction and follow-up

One version explanation in the 0.4.862 benchmark inferred that no upgrade was indicated from matching installed/running versions. That inference exceeds the evidence. Version 0.4.863 tightens the instruction to report only installed/running versions and backend staleness, and was deployed to akaos and TAMGLOBAL. Three subsequent live version-read comparisons all preserved the complete output and produced explanations consistent with those limits. This is a small follow-up check, not a general guarantee about model prose. The 90-trial timing distribution above remains explicitly measured on 0.4.862.

The benchmark exercised 60 Cobuild-backed trials: 30 original-bridge trials with two SDK message exchanges each, and 30 compact trials with one each. Those 90 SDK exchanges demonstrate use of the service without Second Look; they are not billable credit units. The wording follow-up added three compact interpretation calls.

Maintenance audit: no errors. The existing `docs/screenshots/overview.png` reference image remains unused by README and produces the same pre-existing warning.
