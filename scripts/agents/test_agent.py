#!/usr/bin/env python3
"""Smoke-test a plugin agent through the LLM Mesh (agent = virtual LLM).

    .venv/bin/python scripts/agents/test_agent.py [--agent "ATK Health Triage"] \
        [--project AGENTSSANDBOX] [--prompt "..."] [--llm-id <mesh id>]

Creates (or reuses) the agent instance in the sandbox project, then runs a
completion against its virtual LLM id and prints the streamed answer.
"""

import argparse
import pathlib
import sys

import dataikuapi

REPO = pathlib.Path(__file__).resolve().parents[2]

AGENT_TYPES = {
    'ATK Health Triage': 'health-triage',
    'ATK Scoping Architect': 'scoping-architect',
    'ATK Ops Actuator': 'ops-actuator',
}


def get_client():
    url = (REPO / '.dss-url').read_text().strip()
    key = (REPO / '.dss-api-key').read_text().strip()
    return dataikuapi.DSSClient(url, key)


def ensure_agent(project, name, component, llm_id):
    for a in project.list_agents() or []:
        raw = a if isinstance(a, dict) else getattr(a, 'raw', {})
        if raw.get('name') == name:
            return project.get_agent(raw['id'])
    agent = project.create_agent(name, 'PLUGIN_AGENT',
                                 plugin_agent_type='admin-toolkit-agents_%s' % component)
    settings = agent.get_settings()
    raw = settings.get_raw()
    params = raw.setdefault('pluginAgentParams', raw.setdefault('params', {}))
    if isinstance(params, dict):
        params.setdefault('config', {})['llm_id'] = llm_id or ''
    settings.save()
    print('created agent %r (id=%s)' % (name, agent.agent_id if hasattr(agent, 'agent_id') else '?'))
    return agent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--agent', default='ATK Health Triage')
    ap.add_argument('--project', default='AGENTSSANDBOX')
    ap.add_argument('--prompt', default='Run a fleet health sweep and give me the triage report.')
    ap.add_argument('--llm-id', default='')
    args = ap.parse_args()

    client = get_client()
    project = client.get_project(args.project)
    agent = ensure_agent(project, args.agent, AGENT_TYPES[args.agent], args.llm_id)

    agent_id = getattr(agent, 'agent_id', None) or getattr(agent, 'id', None)
    llm_id = 'agent:%s:%s' % (args.project, agent_id)
    print('querying virtual LLM %s ...' % llm_id)
    llm = project.get_llm(llm_id)
    completion = llm.new_completion()
    completion.with_message(args.prompt)
    resp = completion.execute()
    if resp.success:
        print('\n----- agent answer -----\n')
        print(resp.text)
    else:
        print('FAILED:', getattr(resp, 'errorMessage', None) or getattr(resp, '_raw', None))


if __name__ == '__main__':
    main()
