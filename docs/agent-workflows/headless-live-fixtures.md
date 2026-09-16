# Remaining Headless live fixtures

These tools compare whole tasks through Legacy LLM Mesh and the deployed actual
Dataiku Headless MCP transport. They do not use the retired per-operation Cobuild
bridge. Production approval code is unchanged; only the fixture controller can
approve the exact task-local plan. `plugin-deploy` remains excluded.

## Cloud and Python

Obtain explicit authorization for one disposable cluster containing one
`t3.small` worker and two executions of `print(6 * 7)`. Run from the repository
with its virtual environment:

```sh
.venv/bin/python scripts/agents/run_headless_remaining.py \
  --execute --spec /private/path/cluster-spec.json \
  --output /private/path/new-run-directory
```

The specification file contains an eksctl configuration under `spec`, using an
existing VPC and private subnets. Only one managed group named `one-small-node`
is accepted: `instanceType: t3.small`, desired capacity 1, maximum capacity 1,
minimum capacity 0 or 1. Extra provisioning options, unmanaged groups, Fargate,
launch templates and alternative instance types are refused. The target is
restricted to akaos. The controller assigns a unique cluster name and ownership
tags, packages an owned macro with an isolated code environment, and writes a
private recovery journal before submitting operations.

The sequence is Legacy start → both modes' ConfigMap/pod/Python checks → Legacy
stop → verified cloud absence → Headless start → Headless stop. The same cluster
definition is reused; cloud incarnations never overlap. AWS inspection and
termination-protection changes execute through a macro on akaos. Direct DSS API
reads independently verify a single Ready `t3.small` node. The Kubernetes fixture
only labels its own ConfigMap and removes its own completed pod; unexpected jobs
or completed pods stop the cleanup comparison.

The finalizer verifies absence of the owned EKS cluster, nonterminated EC2
workers, and control-plane/node-group CloudFormation stacks before deleting the
DSS definition. It then deletes the temporary plugin and its own environment.
Never attach the Admin Toolkit environment to a disposable plugin. Gates are
restored without changing the saved reasoning mode.

## Images

Fresh images cannot satisfy the production release-date cutoff. Do not falsify
timestamps or relax eligibility rules. Obtain specific approval for two eligible
pre-existing digests only after a read-only inventory and ADTK dry run. The
September 2026 fixture uses akaos's obsolete DSS 15.0.0 execution and Spark base
images and requires both current DSS 15.0.1 base images to remain present.

`run_headless_image_pair.py` accepts the approved `{ "images": [...] }` target
file and the private state of an owned read-only observer macro. That observer
must return exact repository inventories under
`images: [{repo, result: {imageDetails: [...]}}]`; it must invoke AWS on the host.
The target validator binds both repository names to DSS's **installId**, not its
node name. Each mode receives a different approved digest. Fresh plans and normal
backend age checks precede deletion; independent inventories verify the selected
digest disappeared and every other digest remains. The output directory must be
new. An earlier approval or deletion result is never reused for another pair.

## Interrupted runs and evidence

Do not rerun a controller after an uncertain write. Inspect its private
`state.json`, the macro's status, the DSS cluster definition and cloud resources.
Stopping polling is not cancellation. An active/unknown macro retains the plugin
and recovery journal so its outcome can be reconciled. Teardown remains required;
observe completion and ownership before taking the next cleanup action. Do not
remove the DSS definition while cloud resources still exist.

Keep raw results, targets, plans, transcripts and exceptions private. Publish
only capability outcomes, measured plugin version, timings, model/tool counts,
verified postconditions and cleanup status. Infrastructure creation/deletion
latency is part of cloud task timings and must not be presented as model-only
latency. The Legacy comparison uses DSS `mainLLMId`, not application agent model
overrides. Never infer Cobuild's underlying model or token credits.
