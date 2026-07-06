#!/usr/bin/env python3
"""Smoke-test a plugin agent through the LLM Mesh (agent = virtual LLM).

    .venv/bin/python scripts/agents/test_agent.py [--agent "ATK Health Triage"] \
        [--project AGENTSSANDBOX] [--prompt "..."] [--llm-id <mesh id>]

Creates (or reuses) the agent instance in the sandbox project, then runs a
completion against its virtual LLM id and prints the streamed answer.
"""

import argparse
import os
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
    url = os.environ.get('DSS_URL') or (REPO / '.dss-url').read_text().strip()
    key = os.environ.get('DSS_API_KEY') or (REPO / '.dss-api-key').read_text().strip()
    return dataikuapi.DSSClient(url, key)


def set_agent_config(agent, config_updates):
    settings = agent.get_settings()
    raw = settings.get_raw()
    active = raw.get('activeVersion')
    version = next((v for v in raw.get('versions', []) if v.get('versionId') == active),
                   (raw.get('versions') or [{}])[0])
    version.setdefault('pluginAgentConfig', {}).update(config_updates)
    raw.pop('pluginAgentParams', None)
    raw.pop('params', None)
    settings.save()


def ensure_agent(project, name, component, llm_id):
    existing = None
    for a in project.list_agents() or []:
        raw = a if isinstance(a, dict) else getattr(a, 'raw', {})
        if raw.get('name') == name:
            existing = project.get_agent(raw['id'])
            break
    # plugin agent types register as agent_<pluginId>_<componentId> (AgentTypesRegistry)
    agent = existing or project.create_agent(name, 'PLUGIN_AGENT',
                                             plugin_agent_type='agent_admin-toolkit_%s' % component)
    if llm_id:
        set_agent_config(agent, {'llm_id': llm_id})
    if not existing:
        print('created agent %r (id=%s)' % (name, agent.id))
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

    llm = agent.as_llm()
    print('querying virtual LLM %s ...' % llm.llm_id)
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
