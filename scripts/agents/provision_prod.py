#!/usr/bin/env python3
"""One-shot provisioning of the admin-toolkit-agents plugin on an instance
where the plugin zip is already installed (e.g. tam-global via the secure
wrapper). Idempotent — safe to re-run.

    DSS_API_KEY=<admin key> .venv/bin/python scripts/agents/provision_prod.py \
        --url https://tam-global.fe-aws.dkucloud-dev.com \
        [--key-file <path>] [--red-password <pw>] [--keys-password <pw>] \
        [--llm-id anthropic:kaosclaude:claude-opus-4-8] [--project AGENTOPS] [--no-smoke]

Does, in order:
  1. verify the plugin is installed (list_plugins)
  2. ensure code env plugin_admin-toolkit-agents_managed + install packages
  3. plugin settings: codeEnvName (kernel env resolution reads ONLY this) + params
     (backend_url, default_llm_id, optional passwords; enable_red_actions stays False)
  4. ensure the ops project (containerMode NONE) + 11 tool instances + 3 agent instances
  5. smoke: list-hosts probe + config-inspect through the real tool runtime
"""

import argparse
import json
import os
import pathlib
import sys
import time

import dataikuapi

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))

from test_agent import AGENT_TYPES, ensure_agent  # noqa: E402
from test_tools import PLUGIN_ID, TOOLS, ensure_tool, run_tool  # noqa: E402

ENV_NAME = 'plugin_%s_managed' % PLUGIN_ID
DEFAULT_BACKEND = 'https://tam-global.fe-aws.dkucloud-dev.com/web-apps-backends/DIAG_PARSER_BRANCH1/Gv9CLFn'


def wait_future(obj):
    if hasattr(obj, 'wait_for_result'):
        return obj.wait_for_result()
    return obj


def ensure_code_env(client, plugin):
    names = {e.get('envName') for e in client.list_code_envs()}
    if ENV_NAME not in names:
        print('creating code env %s ...' % ENV_NAME)
        wait_future(plugin.create_code_env())
    env = client.get_code_env('PYTHON', ENV_NAME)
    print('installing packages into %s (a few minutes on first run) ...' % ENV_NAME)
    t0 = time.time()
    # /plugins/<id>/code-env/actions/update can throw ERR_PLUGIN_WITHOUT_CODEENV
    # even with a valid spec — update_packages on the env itself works.
    env.update_packages()
    print('packages installed (%.0fs)' % (time.time() - t0))


def apply_plugin_settings(plugin, args):
    settings = plugin.get_settings()
    raw = settings.get_raw()
    raw['codeEnvName'] = ENV_NAME
    cfg = raw.setdefault('config', {})
    cfg['backend_url'] = args.backend_url
    cfg['default_llm_id'] = args.llm_id
    if args.red_password:
        cfg['red_actions_password'] = args.red_password
    if args.keys_password:
        cfg['host_keys_password'] = args.keys_password
    cfg.setdefault('enable_red_actions', False)  # kill switch stays OFF
    settings.save()
    print('plugin settings saved: codeEnvName=%s backend_url=%s llm=%s red_pw=%s keys_pw=%s'
          % (ENV_NAME, args.backend_url, args.llm_id,
             'set' if args.red_password else 'EMPTY (actuator locked)',
             'set' if args.keys_password else 'empty'))


def ensure_project(client, key):
    if key not in {p['projectKey'] for p in client.list_projects()}:
        client.create_project(key, 'Agent Ops', 'admin',
                              description='admin-toolkit-agents: tools + agents for Agent Hub')
        print('created project %s' % key)
    project = client.get_project(key)
    ps = project.get_settings()
    ps.get_raw()['container'] = {'containerMode': 'NONE'}
    ps.save()
    return project


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='https://tam-global.fe-aws.dkucloud-dev.com')
    ap.add_argument('--key-file', default='')
    ap.add_argument('--backend-url', default=DEFAULT_BACKEND)
    ap.add_argument('--llm-id', default='anthropic:kaosclaude:claude-opus-4-8')
    ap.add_argument('--red-password', default='')
    ap.add_argument('--keys-password', default='')
    ap.add_argument('--project', default='AGENTOPS')
    ap.add_argument('--no-smoke', action='store_true')
    args = ap.parse_args()

    key = (pathlib.Path(args.key_file).read_text().strip() if args.key_file
           else os.environ.get('DSS_API_KEY', ''))
    if not key:
        sys.exit('need DSS_API_KEY env var or --key-file')
    client = dataikuapi.DSSClient(args.url, key)

    installed = {p['id']: p for p in client.list_plugins()}
    if PLUGIN_ID not in installed:
        sys.exit('plugin %s is NOT installed on %s — run the secure wrapper first' % (PLUGIN_ID, args.url))
    print('plugin %s v%s installed on %s' % (PLUGIN_ID, installed[PLUGIN_ID].get('version'), args.url))

    plugin = client.get_plugin(PLUGIN_ID)
    ensure_code_env(client, plugin)
    apply_plugin_settings(plugin, args)

    project = ensure_project(client, args.project)
    handles = {}
    for component in TOOLS:
        handles[component], _ = ensure_tool(project, component)
    print('%d tool instances ready in %s' % (len(handles), args.project))
    for name, component in AGENT_TYPES.items():
        ensure_agent(project, name, component, args.llm_id)
    print('%d agent instances ready (llm=%s)' % (len(AGENT_TYPES), args.llm_id))

    if not args.no_smoke:
        run_tool(handles['list-hosts'], 'smoke: list-hosts probe', {'probe': True})
        run_tool(handles['config-inspect'], 'smoke: config-inspect plugins',
                 {'domain': 'plugins', 'name_filter': 'admin-toolkit', 'top_n': 5})
    print('\nDONE — next: run test_tools.py/golden_check.py pointed at this instance, '
          'then publish the agents in Agent Hub.')


if __name__ == '__main__':
    main()
