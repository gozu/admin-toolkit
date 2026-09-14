# Dataiku Headless integration assessment

Investigated 14 September 2026. Recommendation: reuse Headless for standard DSS
inspection and project-building workflows; keep Admin Toolkit's diagnostic,
fleet-routing, and action-control layers. Add Cobuild as an optional specialist
behind the existing agent, rather than replacing the whole agent runtime.

This assessment distinguishes Headless's MCP tools, Cobuild's own tools, external
SKILL.md instructions, and DSS agent tools/skills. They are different surfaces:
a capability exposed by one is not automatically callable from another.

## Evidence and scope

- Inspected the private `dataiku/dataiku-headless` repository at
  [`9d7f6cc`](https://github.com/dataiku/dataiku-headless/tree/9d7f6cc29a9406f347708c8811b5689ab257c6c8),
  package version 0.6.0, alongside this Toolkit checkout at `f0f51cd`.
- Enumerated **128 MCP tools** from the running server. The capability matrix
  classifies them as 93 inspection tools, 21 direct DSS writes, six Cobuild tools,
  four execution tools, three local-profile operations, and one connection test.
  Two Cobuild status/list tools also carry `readOnlyHint`, so an annotation count
  yields 95 rather than 93. [Upstream capability matrix](https://github.com/dataiku/dataiku-headless/blob/9d7f6cc29a9406f347708c8811b5689ab257c6c8/docs/capabilities.md).
- Found two entry skills and 55 Markdown reference files. The Python wheel packages
  the MCP implementation; installing that wheel does not itself install the
  companion harness skills. [Package definition](https://github.com/dataiku/dataiku-headless/blob/9d7f6cc29a9406f347708c8811b5689ab257c6c8/pyproject.toml).
- Ran upstream instance-pinning and Cobuild lifecycle tests: **18 passed**.
- Exercised the actual upstream FastMCP server through an in-process MCP client
  in an isolated local Python environment, using the launcher's direct dependency
  versions: FastMCP 3.4.5, dataiku-api-client 14.7.2, python-dotenv 1.2.2.
  This was not a desktop-plugin installation or an end-to-end stdio packaging test.
- Connected to akaos DSS **15.0.1** as `admin`, then TAMGLOBAL DSS **14.7.3** as
  non-admin `kaosdex`, a FULL_DESIGNER with no admin groups.

On akaos, all **17 direct read checks passed**: instance/version, project count and
listing, users, groups, code environments, plugins, licensing, connections,
container execution configurations, project settings, agents, agent tools,
scenarios, datasets, recipes, and Flow graph. The project count was 27. These were
small live checks, not an exhaustive compatibility suite or a fleet benchmark.
Median observed call duration was 0.77 seconds; connection listing took 11.11
seconds. No latency comparison with Toolkit was performed.

Cobuild rejected the akaos requests with an AI Services organization-credit error,
including retries after the user changed credits/license/provider settings. That
does not establish the root cause of akaos's credit accounting. The same Headless
code successfully completed a read-only Cobuild turn on TAMGLOBAL in 16.87 seconds.
The empty `KAOSHEADLESS` project had zero datasets, recipes, agents, and scenarios,
matching the direct inventory checks.

For kaosdex, Headless could list basic users/groups, code environments, and plugins;
the licensing tool correctly refused access because the account is not admin.
Initially the account saw no projects; the user then supplied `KAOSHEADLESS`.
Tests used only this personal key on TAMGLOBAL, not the stored administrator key.

The user's separate Cobuild inspection of TAMGLOBAL's `ADMINTOOLKIT` project found
one plugin agent and 13 registered Toolkit tools. Its capability report agrees
with the independent KAOSHEADLESS conversation: Cobuild can discover agent-tool
definitions, but has no exposed general invocation tool for those plugin tools,
no macro runner, and no DSS Agent Skill-object management operation.

In the dedicated KAOSHEADLESS test project, Cobuild created a native
`GRELCalculator` tool, `jyQRegd`, in 24.97 seconds, then a native
`TOOLS_USING_AGENT`, `LxcOUtyv`, in 26.40 seconds. Direct Headless reads verified
the tool type and description, agent system prompt, selected model, and sole
attached tool ID. A separate SDK read verified active version `v1`. These are
real stored DSS objects, not a generated setup script or an unverified claim.
They remain in the test project for review; no existing project assets were
modified. [Test project](https://tam-global.fe-aws.dkucloud-dev.com/projects/KAOSHEADLESS/flow/).

**The subsequent headless execution test did not succeed.** Asked to test the
agent on `17 * 23`, Cobuild called `navigate_to_page`, followed by
`agent_building_chat_control` with `operation=sendMessageAndWaitForCompletion`.
The retained turn stayed in progress across three 240-second waits. Inspection
of the DSS UI's conversation events confirmed the pending chat-control call;
the native agent's test-chat pane was still empty. This is evidence of a stalled
UI-oriented testing path on this DSS 14.7.3 instance, not proof that all native
agents or all Cobuild versions fail to execute.

The test was stopped through the Cobuild UI after it displayed roughly 14 minutes
of work. Headless then returned `status=completed`, `is_error=false`, and the
message `Operation was aborted by the user.` Thus a terminal "completed" turn is
not sufficient evidence that the requested task succeeded. The adapter must
distinguish operation outcome from turn completion. The inspected MCP surface has
no cancel-Cobuild-turn tool; cancellation required the UI in this test.

After cancelling, one independent DSS SDK `agent.as_llm()` execution returned in
2.46 seconds with a Bedrock HTTP 403: kaosdex's assumed role lacked
`bedrock:InvokeModelWithResponseStream` for the chosen Sonnet model. Discovery in
`list_llms` therefore did not establish that this user's downstream provider
identity could invoke that model. The agent is configuration-verified, not
runtime-verified. This provider-permission failure is recorded separately from
the stalled Cobuild UI-control call; the available evidence does not establish
that one caused the other. No provider or administrator permissions were changed.

Final project inventory was one agent and one agent tool, with zero datasets,
recipes, and scenarios. The Cobuild run was terminal after UI cancellation and
the local test server was shut down.

Sanitized call results and timings are preserved in
[headless-integration-evidence.json](headless-integration-evidence.json).

## What can replace or reduce our code

Our source currently has 11 sensor entry points, 19 `config_inspect` domains,
21 curated `toolkit_get` endpoints, and separate planning/execution orchestration.
Comparing raw tool counts would substantially overstate replacement coverage.
[Sensor implementations](../python-lib/atk_agent_common/tools_impl.py),
[domain registry](../python-lib/atk_agent_common/domain_registry.py),
[read registry](../python-lib/atk_agent_common/read_registry.py).

| Area | Upstream coverage | Recommended use |
| --- | --- | --- |
| Projects, datasets, recipes, Flow, scenarios, jobs, webapps, LLMs, agents | Broad direct inspection; selected execution operations; creation/editing largely delegated to Cobuild | Use upstream for new generic DSS capabilities and external agent access |
| Users/groups, code environments, plugins, license, execution configs | Direct MCP inspection and selected administration writes, subject to DSS permissions | Reuse selected reads; retain Toolkit policies around writes |
| Project/Flow/agent authoring | Cobuild specialist reached through retained conversations | Optional delegated capability; verify produced objects through direct reads |
| External agent knowledge | Two skills and a substantial reference library | Reuse upstream domain guides; retain a small Toolkit-specific companion skill |
| Health scoring and triage | No equivalent to the Toolkit's deterministic score and fleet sweep | Keep Toolkit implementation |
| Disk footprint, host resources, backend.log, Kubernetes, PostgreSQL, compute/cost attribution | No equivalent diagnostic surface in the inspected Headless tool catalog | Keep sensors, scans, macros, and derived data |
| Admin action plans, exact-target approval, gates, audit, remediation checklist | Some upstream validation and Cobuild deletion confirmations, but no equivalent end-to-end Toolkit protocol | Keep Toolkit controls and executors |
| Fleet sessions, caching, UI progress, snapshots/trends | Not supplied as a Toolkit-compatible system | Keep current contracts and stores |

**Ten of the 19 config-inspection domains have some generic inventory overlap**:
projects, connections, code environments, plugins, LLMs, users, scenarios, webapps,
jobs, and datasets. This is a domain overlap count, not 53% implementation parity.
For example, listing code environments does not replace disk-size scans, usage
analysis, consolidation planning, or cleanup-candidate logic.

None of the seven specialized diagnostic sensors has a complete replacement:
`instance_health`, `compute_cost`, `log_errors`, `log_tail`, `storage_footprint`,
`k8s_health`, and `db_health`. The other four sensor entry points provide host
discovery, configuration inspection, curated reads, and capability discovery.
Replacing their simplest API calls would still leave output shaping, host routing,
and the webapp's own need for the existing backend datasets.

The largest likely saving is avoiding new parallel implementations of standard DSS
object tools and domain instructions. Savings from deleting today's core admin
logic are much smaller. No defensible percentage of engineering hours saved has
been measured.

## Integration constraints

**Host and identity isolation.** Headless stores a process-wide active instance.
Its middleware pins the active instance for each request, and its blocking executor
propagates context, protecting an already-started call from a later switch.
However, separate users' sequences of `switch_instance` followed by tool calls can
still interleave. A shared process is not a substitute for Toolkit's explicit
per-call host routing. Use isolated workers per host/credential identity, or an
upstream-supported explicit client/instance-binding adapter. Never change global
environment variables between concurrent requests. [Configuration](https://github.com/dataiku/dataiku-headless/blob/9d7f6cc29a9406f347708c8811b5689ab257c6c8/dataiku_mcp/config.py),
[middleware](https://github.com/dataiku/dataiku-headless/blob/9d7f6cc29a9406f347708c8811b5689ab257c6c8/dataiku_mcp/__init__.py).

**Write controls.** `allow_edit_project=false` is Cobuild's default. Its retained
turn machinery checks project/instance pairing and current turn IDs for questions
and deletion confirmations. Direct administration tools have their own checks;
for example, code-env deletion refuses reported usages. But a tool such as
`delete_user` directly performs the operation after checking administrator access.
It has no Toolkit HMAC plan token or Toolkit audit row. MCP annotations are not our
authorization enforcement. Filter exposed tools on the server, and keep any reused
write behind existing plan/execute gates. A Cobuild editing permission is broader
than an approval bound to one exact canonical change; it should not be represented
as the same guarantee. [Cobuild implementation](https://github.com/dataiku/dataiku-headless/blob/9d7f6cc29a9406f347708c8811b5689ab257c6c8/dataiku_mcp/tools/cobuild.py),
[user deletion](https://github.com/dataiku/dataiku-headless/blob/9d7f6cc29a9406f347708c8811b5689ab257c6c8/dataiku_mcp/tools/users.py),
[Toolkit confirmation binding](../python-lib/atk_agent_common/confirm.py).

**Progress and durability.** Cobuild calls wait up to 240 seconds before returning a
retained pending turn. Conversations/turns are held in the MCP process and cannot
be recovered through its registry after a restart. Its response/status model is
not Toolkit's token/tool-event SSE protocol, and cancelling a waiting client does
not necessarily cancel DSS-side work. An integration needs immediate lifecycle
updates, heartbeat/polling, question handling, explicit uncertain-result handling,
and read-back before retrying a mutation. [Cobuild workflow](https://github.com/dataiku/dataiku-headless/blob/9d7f6cc29a9406f347708c8811b5689ab257c6c8/skills/dataiku-headless/references/cobuild.md).

**Skills are instructions, not executable tool plumbing.** Loading the upstream
SKILL.md does not register MCP tools in our LangChain toolset or create native DSS
Skill objects. Its broad routing rules include stopping on missing MCP/Cobuild
coverage rather than falling back to arbitrary APIs. Scope that guidance to the
upstream specialist; keep Toolkit's authorized macros and tools available through
our own companion instructions. Native DSS Visual Agent skills are another
feature with their own objects and resources. [Headless entry skill](https://github.com/dataiku/dataiku-headless/blob/9d7f6cc29a9406f347708c8811b5689ab257c6c8/skills/dataiku-headless/SKILL.md),
[DSS Agent Skills documentation](https://doc.dataiku.com/dss/latest/agents/skills.html).

**Runtime and packaging.** The checked launcher uses Python 3.10+, pinned
dependencies, and stdio MCP. The source says the package is not published to a
package index. Pin an upstream revision instead of assuming a stable pip release
or internal helper API. The repository currently contains an Apache-2.0 license;
its GitHub visibility was private when inspected. The local uv was 0.11.31, below
the README's 0.12.0 launcher recommendation; the investigation used an isolated
venv rather than changing the user's uv installation. [Launcher](https://github.com/dataiku/dataiku-headless/blob/9d7f6cc29a9406f347708c8811b5689ab257c6c8/runtime/run_mcp.py).

**AI Services dependency.** Direct inventory tools continued working when Cobuild
was credit-blocked. Dataiku documents per-Designer included usage and a shared
AI-credit balance used after that allowance. A higher user limit is not itself
evidence that the organization has available credits. Bring-your-own-LLM is an
AI Services mode, not evidence that all AI Services metering disappears. Preserve
useful direct-tool behavior when Cobuild is unavailable. No credit rate or operating
cost estimate was established by these tests. [AI Services usage](https://doc.dataiku.com/dss/latest/ai-assistants/usage.html),
[AI Services setup](https://doc.dataiku.com/dss/latest/ai-assistants/setup.html).

## Proposed adoption sequence

1. **External coding agents:** use upstream Headless tools and guides for standard
   DSS work. Supply a small Toolkit companion skill for fleet triage, scans, and
   gated remediation, backed by an explicit bridge to Toolkit's existing tools.
   Cobuild cannot invoke those tools merely because they are registered in DSS.
2. **Embedded agent pilot:** expose a small reviewed read-tool allowlist behind a
   host/identity adapter. Start with project/Flow/agent inspection. Normalize
   columnar outputs, preserve the output budget, and compare results with current
   sensors. Do not advertise all 128 tools to every turn.
3. **Cobuild specialist:** add one optional delegation surface for bounded
   project-level work. For a DSS-hosted implementation, evaluate the public
   `new_cobuild_conversation()` SDK surface against operating a separate MCP
   service; the same Cobuild backend is available without launching a desktop
   harness. Any managed-host OS work continues through Toolkit macros.
4. **Native agent provisioning:** the live create/read-back test demonstrates
   that Cobuild can assemble a simple visual agent with a native tool. Pilot this
   for simple cases instead of writing another custom plugin agent. Keep the
   Toolkit's special runtime behavior until trace, cancellation, approval, and UI
   event parity have been demonstrated.
5. **Retire code only after parity:** cover overlapping outputs, non-admin
   refusals, two-host concurrency, credit failure, restart/timeout behavior, exact
   approval enforcement, and the existing frontend/backend contracts. The present
   investigation does not establish that any production subsystem can be removed.

The current runtime already defaults to the DSS-managed agent path, with native
execution as a choice/fallback. Replacing that is a separate decision from reusing
Headless tools. The older native-default paragraph in `docs/agents-reference.md`
is stale relative to [the runtime code](../python-lib/adk_backend/agent_native.py).
