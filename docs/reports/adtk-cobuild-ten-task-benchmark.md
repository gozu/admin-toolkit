# Cobuild: ten read-task performance measurements

Measured 2026-09-15 on internal DEV (akaos), running Admin Toolkit 0.4.864.

3 repetitions per task and route. Table values are medians in seconds. The CSV preserves every sample; the JSON includes minimum and maximum values. Completion medians use successful completions only; rejected explanations remain recorded as failures. Data medians include retained successful reads even when their explanation failed.

| Task | Existing data | Original Cobuild completion | Optimized data | Optimized completion | Data / Existing | Completion / Existing | Completion speedup |
|---|---:|---:|---:|---:|---:|---:|---:|
| Version | 0.79s | 11.08s | 0.75s | 6.92s | 0.94× | 8.71× | 1.60× |
| Hosts | 0.76s | 11.07s | 0.76s | 5.29s | 1.00× | 6.93× | 2.09× |
| Capabilities | 0.76s | 14.16s | 0.77s | 6.58s | 1.02× | 8.67× | 2.15× |
| System health | 1.51s | 13.95s | 0.78s | 7.91s | 0.52× | 5.22× | 1.76× |
| Connections | 0.76s | 12.78s | 0.77s | 6.49s | 1.01× | 8.54× | 1.97× |
| Plugins | 0.75s | 12.80s | 0.76s | 6.11s | 1.01× | 8.13× | 2.10× |
| Projects | 0.76s | 11.08s | 0.76s | 7.11s | 1.00× | 9.33× | 1.56× |
| Kubernetes reachability | 4.31s | 16.20s | 3.29s | 10.66s | 0.76× | 2.47× | 1.52× |
| Installation overview | 2.28s | 35.84s | 0.77s | 7.75s † | 0.34× | 3.40× | 4.62× |
| Estate inventory | 5.65s | 41.07s | 3.58s | 13.99s † | 0.63× | 2.47× | 2.94× |

† At least one explanation was rejected; the median uses successful completions. All attempts and validation counts appear below.

## What was measured

- Existing: deterministic ADTK tools, without Cobuild explanation. Original bridge: an operation-request turn, read execution, and result-acknowledgment turn for every read.
- Optimized: one backend task request checks permissions, dispatches existing read handlers, returns their data, and queues one Cobuild interpretation. A separate SSE connection receives the finished explanation.
- Data time ends when the calling client receives the full deterministic output. Completion time includes the explanation. Both clocks start at tool dispatch, so these are not end-to-end natural-language chat timings.
- Installation overview combines version, hosts, and plugins. Estate inventory combines connections, projects, and Kubernetes reachability. Existing and original bridge execute these reads sequentially; the task API executes up to three concurrently.
- The combined tasks explicitly exercise the batch API. Ordinary eligible single sensor calls use the fused endpoint automatically; the outer chat planner has not been changed to combine separate tool calls.
- Routes rotate order on each repetition. Shared caches are not cleared. Capability inventory may use a 30-second permission cache on Existing; the optimized route reloads permissions each time. This is a small, warm-instance sample, not a load test or a tail-latency estimate.
- The client runs from the development workstation against DEV. Network and proxy latency are included. Cobuild send-message timing includes server orchestration and response handling, not just model generation.
- All tasks are read-only. No mutations, permission changes, or provider-setting changes are part of the benchmark. Detailed scans and action execution retain their previous routing.

## Validation and failures

| Task | Existing success | Original success | Optimized success | Optimized raw output matches | Exact interpretation evidence matches |
|---|---:|---:|---:|---:|---:|
| Version | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| Hosts | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| Capabilities | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| System health | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| Connections | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| Plugins | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| Projects | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| Kubernetes reachability | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| Installation overview | 3/3 | 3/3 | 2/3 | 3/3 | 2/3 |
| Estate inventory | 3/3 | 3/3 | 2/3 | 3/3 | 2/3 |

Installation overview, optimized: interpretation/mismatch/facts.

Estate inventory, optimized: interpretation/malformed-json.

Raw output comparisons use the Existing sample from the same repetition. Live observations can change between calls; an output mismatch alone is not proof of incorrect execution. Interpretation validation checks the exact typed, allowlisted evidence and digest; it does not prove every sentence of model prose correct. Private observed data is kept outside the repository and is not included in these artifacts.

## Remaining Cobuild time

| Task | Conversation creation | Send message / response |
|---|---:|---:|
| Version | 0.01s | 6.16s |
| Hosts | 0.01s | 4.53s |
| Capabilities | 0.01s | 5.82s |
| System health | 0.01s | 7.12s |
| Connections | 0.01s | 5.72s |
| Plugins | 0.01s | 5.34s |
| Projects | 0.01s | 6.35s |
| Kubernetes reachability | 0.01s | 7.36s |
| Installation overview | 0.01s | 6.98s |
| Estate inventory | 0.01s | 10.39s |

## Verification

890 backend tests and 23 subtests passed. Three browser tests covered pending, completed, failed, and expired interpretations while retaining read data. Frontend typecheck, contracts, and packaged production build passed. Version 0.4.864 was deployed to DEV and TAMGLOBAL.

Maintenance audit: no errors; the existing unreferenced `docs/screenshots/overview.png` warning remains. The build also retains its existing bundle-size/deprecated-plugin warnings.

Reproduce with `scripts/agents/benchmark_cobuild_tasks.py --samples 3 --output-dir <private-directory>`, then publish its aggregates using `scripts/agents/render_cobuild_task_benchmark.py`.
