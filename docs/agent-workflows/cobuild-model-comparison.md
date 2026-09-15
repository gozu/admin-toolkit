# Whole-task Cobuild comparison

The 64-check suite now uses `cobuild_model_compare.ModelComparison`. Each task
has one LLM Mesh conversation and one Cobuild conversation. Both receive the
same ADTK system instructions, complete enabled tool catalog and user task.
The model selects tools and writes the final answer. Cobuild uses a JSON
adapter because its conversation SDK does not accept Mesh's native function
calling interface.

ADTK validates and executes the selected tools using the Existing executor
route. This prevents nested calls to the old per-operation Cobuild bridge.
The executor binds calls to the current fixture, rechecks permissions, retains
real confirmation tokens privately and permits at most one attempted write
per task. The model receives an opaque reference. A test-controller approval
message authorizes only the scoped disposable fixture after its plan exists.
An uncertain write is never retried automatically.
Models may omit nullable fields when copying a plan; the controller restores
those fields from the retained canonical target before execution. Other target
changes remain rejected. A rejected follow-up read does not erase evidence of
an already completed write or cause that write to run again.

`cobuild_model_suite.GROUPS` assigns all 11 sensors and 53 actions exactly
once. A coverage test fails when the capability inventory changes without
updating the suite. Existing fixture implementations provide reset,
independent verification and cleanup; their old comparison methods are not
used. Image deletion, Python execution and cloud lifecycle cases require new
fixtures/approvals because their previous targets and approvals were consumed.
`plugin-deploy` retains the user's exclusion. Prerequisite rows never inherit
historical passes.

The existing `cobuild_compare.py` read command now defaults to replacement
mode. Historical operation-bridge CLI runs require `--legacy-operation-bridge`;
the old local action entry points direct operators to the host-macro runner.

Run the suite on the internal development instance configured in `.dss-url`
and `.dss-api-key`:

```sh
.venv/bin/python scripts/agents/run_cobuild_model_suite.py \
  --execute --output /tmp/adtk-model-comparison
```

Use repeated `--group fixture --group data` arguments to choose fixture groups,
or `--case project-delete` to run a specific check. Action groups run serially
inside a temporary `ADMINTOOLKIT` host macro, with their
own Python environment. **Never attach the ADTK environment to a temporary
plugin:** DSS plugin deletion also deletes its attached environment. The
runner uses a unique plugin and environment and waits for removal to finish.
Managed-host filesystem, Docker, SQL and subprocess work remains on that host.
The database fixture temporarily restarts the development webapp; run it during
a quiet window. No customer instance is selected automatically.

The private output directory includes aggregate results and `macro-state.json`.
If the controller loses contact with an active macro, it retains the plugin
and state file; reconcile the run and its fixtures before removing anything.
No second mutation is launched to resolve an unknown outcome. A cleanup or
gate-restoration failure stops subsequent groups. Runtime transcripts remain
private; do not add them to Git or a public report.

Publish explicit aggregate files with:

```sh
.venv/bin/python scripts/agents/render_cobuild_model_suite.py \
  --input /tmp/adtk-model-comparison/fixture.json \
  --input /tmp/adtk-model-comparison/data.json \
  --output-dir docs/reports
```

The renderer accepts only the new transport, retains all supplied attempts,
uses the latest outcome, and publishes an allowlist of measurement fields.
An old successful retry cannot hide a newer failure. Ratios are Cobuild total
time divided by Mesh total time. Both totals include selection, deterministic
execution and final generation. Fixture setup/reset and independent checks
are excluded. One pair per task, always Mesh first, does not establish a
latency distribution or eliminate cache/order effects. Matching tool outputs
or independently verified postconditions do not certify every sentence of an
answer. The configured main model is recorded; internal Cobuild routing is
not inferred from its label.

This tooling changes the comparison suite. The live application's provider
routing and the historical operation-bridge reports are separate.
