#!/usr/bin/env python3
"""Phase A live verification: run the admin-toolkit agent tools through the REAL DSS
agent-tool runtime (not just pure-Python impls).

    .venv/bin/python scripts/agents/test_tools.py [--project AGENTSSANDBOX]

Reads .dss-url/.dss-api-key (akaos dev server). Ensures the sandbox project +
one instance per plugin tool, then runs: list-hosts (probe), instance-health
(local + bad host id), compute-cost. Prints outputs + wall
times (the tool-timeout measurement the plan asks for).
"""

import argparse
import json
import os
import pathlib
import time

import dataikuapi

REPO = pathlib.Path(__file__).resolve().parents[2]
PLUGIN_ID = "admin-toolkit"
TOOLS = ["list-hosts", "instance-health", "compute-cost",
         "config-inspect", "log-errors", "storage-footprint",
         "k8s-health", "db-health", "plan-admin-action", "execute-admin-action"]


def get_client():
    url = os.environ.get("DSS_URL") or (REPO / ".dss-url").read_text().strip()
    key = os.environ.get("DSS_API_KEY") or (REPO / ".dss-api-key").read_text().strip()
    return dataikuapi.DSSClient(url, key)


def ensure_project(client, key):
    existing = {p["projectKey"] for p in client.list_projects()}
    if key not in existing:
        client.create_project(key, "Agents Sandbox", "admin",
                              description="Sandbox for admin-toolkit agent-tool verification")
        print(f"created project {key}")
    return client.get_project(key)


def ensure_tool(project, component):
    """Create (or reuse) an instance of a plugin agent tool; returns its handle."""
    wanted_name = f"atk {component}"
    for t in project.list_agent_tools():
        raw = t if isinstance(t, dict) else getattr(t, "raw", {})
        if raw.get("name") == wanted_name:
            return project.get_agent_tool(raw["id"]), raw.get("type")
    # Plugin tool type convention (developer guide, custom-tools tutorial):
    # Custom_agent_tool_<pluginId>_<componentId>
    tool_type = f"Custom_agent_tool_{PLUGIN_ID}_{component}"
    creator = project.new_agent_tool(tool_type, name=wanted_name)
    tool = creator.create()
    print(f"created tool {wanted_name} with type {tool_type}")
    return tool, tool_type


def run_tool(tool, label, tool_input):
    t0 = time.time()
    try:
        result = tool.run(tool_input)
    except Exception as exc:
        print(f"\n--- {label} FAILED after {time.time()-t0:.1f}s: {type(exc).__name__}: {str(exc)[:400]}")
        return None
    elapsed = time.time() - t0
    text = json.dumps(result, indent=1, default=str)
    print(f"\n--- {label} ({elapsed:.1f}s, {len(text):,} chars)")
    print(text[:2200])
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="AGENTSSANDBOX")
    args = ap.parse_args()

    client = get_client()
    project = ensure_project(client, args.project)

    handles = {}
    for component in TOOLS:
        handles[component], type_used = ensure_tool(project, component)
        print(f"tool ready: {component} (type={type_used}, id={handles[component].tool_id})")

    run_tool(handles["list-hosts"], "list-hosts probe", {"probe": True})
    run_tool(handles["instance-health"], "instance-health local", {"host": "local"})
    run_tool(handles["instance-health"], "instance-health BAD HOST", {"host": "hallucinated-host"})
    run_tool(handles["compute-cost"], "compute-cost by project", {"group_by": "project", "top_n": 5})
    run_tool(handles["config-inspect"], "config-inspect connections", {"domain": "connections", "top_n": 8})
    run_tool(handles["log-errors"], "log-errors grouped", {"top_n": 5})
    run_tool(handles["storage-footprint"], "storage-footprint", {"top_n": 5})
    run_tool(handles["k8s-health"], "k8s-health clusters", {})
    run_tool(handles["db-health"], "db-health no-connection", {"view": "tables"})
    plan = run_tool(handles["plan-admin-action"], "plan k8s-exec-config-tune (PASSWORD-param check)",
                    {"action": "k8s-exec-config-tune",
                     "target": {"configName": "eks-default", "changes": {"memRequestMB": 2048}}})
    out = (plan or {}).get("output") or {}
    token = out.get("confirm_token")
    print("\nPASSWORD-param decryption check:",
          "PASS (confirm_token minted from master_password)" if token
          else f"no token — output error: {json.dumps(out.get('error'))[:300]}")
    run_tool(handles["execute-admin-action"], "execute refusal (kill-switch off)",
             {"action": "k8s-exec-config-tune",
              "target": out.get("canonicalTarget") or {"configName": "eks-default"},
              "confirm": True, "confirm_token": token or "bogus"})


if __name__ == "__main__":
    main()
