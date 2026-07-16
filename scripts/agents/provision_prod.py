#!/usr/bin/env python3
"""One-shot provisioning of the admin-toolkit agents layer on an instance
where the plugin zip is already installed (e.g. tam-global via the secure
wrapper). Idempotent — safe to re-run.

No longer required for normal installs: the webapp self-provisions via
adk_backend/agent_provision.py (Agents page "Set up agents" CTA, the one-click
host installer, and the ADMINTOOLKIT bootstrap). This script remains the
headless ops path — it additionally builds the code env, sets llm-id /
master-password, and runs smoke checks.

    DSS_API_KEY=<admin key> .venv/bin/python scripts/agents/provision_prod.py \
        --url https://tam-global.fe-aws.dkucloud-dev.com \
        [--key-file <path>] [--red-password <pw>] [--keys-password <pw>] \
        [--llm-id anthropic:kaosclaude:claude-opus-4-8] [--project ADMINTOOLKIT] [--no-smoke]

Does, in order:
  1. verify the plugin is installed (list_plugins)
  2. ensure code env plugin_admin-toolkit_managed + install packages
  3. plugin settings: codeEnvName (kernel env resolution reads ONLY this) + params
     (backend_url, default_llm_id, optional passwords; enable_red_actions stays False)
  4. ensure the ops project (containerMode NONE) + 11 tool instances + 3 agent instances
  5. agent interaction logging (DSS >= 14.5): logging dataset + FULL-content
     logging on all 3 agents; Trace Explorer webapp is a one-time manual step
  6. smoke: list-hosts probe + config-inspect through the real tool runtime
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

from interaction_logging import MANUAL_WEBAPP_STEP, ensure_interaction_logging  # noqa: E402
from test_agent import AGENT_TYPES, ensure_agent  # noqa: E402
from test_tools import PLUGIN_ID, TOOLS, ensure_tool, run_tool  # noqa: E402

ENV_BASE = 'plugin_%s_managed' % PLUGIN_ID
WEBAPP_TYPE = 'webapp_%s_%s' % (PLUGIN_ID, PLUGIN_ID)


def discover_backend_url(client, base_url):
    """Backend URL of the deployed admin-toolkit webapp on this instance
    (…/web-apps-backends/<projectKey>/<webappId>), found via the DSS API —
    the plugin's backend_url setting must point at the instance's own webapp,
    never a hardcoded one."""
    hits = []
    for p in client.list_projects():
        try:
            webapps = client.get_project(p['projectKey']).list_webapps() or []
        except Exception:
            continue
        for wa in webapps:
            raw = wa if isinstance(wa, dict) else getattr(wa, 'raw', {}) or {}
            if raw.get('type') == WEBAPP_TYPE:
                hits.append((p['projectKey'], raw.get('id'), raw.get('name')))
    if not hits:
        return ''
    pk, webapp_id, name = hits[0]
    if len(hits) > 1:
        print('WARNING: %d admin-toolkit webapps on this instance — using %s/%s (%r)'
              % (len(hits), pk, webapp_id, name))
    return '%s/web-apps-backends/%s/%s' % (base_url.rstrip('/'), pk, webapp_id)


def wait_future(obj):
    if hasattr(obj, 'wait_for_result'):
        return obj.wait_for_result()
    return obj


def resolve_env_name(client, plugin):
    """DSS auto-renames plugin code envs on recreate (…_managed_1, _2, …).
    Prefer the env the plugin settings already point at, else the newest
    family member, else '' (caller creates one). Never assume the base name."""
    names = {e.get('envName') for e in client.list_code_envs()}
    current = (plugin.get_settings().get_raw().get('codeEnvName') or '').strip()
    if current in names:
        return current
    family = sorted((n for n in names
                     if n and (n == ENV_BASE or n.startswith(ENV_BASE + '_'))),
                    key=lambda n: (len(n), n))
    return family[-1] if family else ''


def ensure_code_env(client, plugin):
    env_name = resolve_env_name(client, plugin)
    if not env_name:
        print('creating code env %s ...' % ENV_BASE)
        wait_future(plugin.create_code_env())
        env_name = resolve_env_name(client, plugin) or ENV_BASE
    env = client.get_code_env('PYTHON', env_name)
    print('installing packages into %s (a few minutes on first run) ...' % env_name)
    t0 = time.time()
    # /plugins/<id>/code-env/actions/update can throw ERR_PLUGIN_WITHOUT_CODEENV
    # even with a valid spec — update_packages on the env itself works.
    env.update_packages()
    print('packages installed (%.0fs)' % (time.time() - t0))
    return env_name


def apply_plugin_settings(plugin, args, env_name):
    settings = plugin.get_settings()
    raw = settings.get_raw()
    raw['codeEnvName'] = env_name
    cfg = raw.setdefault('config', {})
    cfg['backend_url'] = args.backend_url
    cfg['default_llm_id'] = args.llm_id
    if args.master_password:
        cfg['master_password'] = args.master_password
    cfg.setdefault('enable_red_actions', False)  # kill switch stays OFF
    settings.save()
    print('plugin settings saved: codeEnvName=%s backend_url=%s llm=%s master_pw=%s'
          % (env_name, args.backend_url, args.llm_id,
             'set' if args.master_password else 'EMPTY (actuator locked)'))


def ensure_project(client, key):
    if key not in {p['projectKey'] for p in client.list_projects()}:
        # Same identity the webapp's macro-project bootstrap creates — agents
        # live in the toolkit's own ADMINTOOLKIT project.
        client.create_project(key, 'Admin Toolkit', 'admin',
                              description='admin-toolkit: macros + agent tools + agent instances')
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
    ap.add_argument('--backend-url', default='',
                    help='empty = auto-discover the admin-toolkit webapp backend on the instance')
    ap.add_argument('--llm-id', default='anthropic:kaosclaude:claude-opus-4-8')
    ap.add_argument('--master-password', default='')
    ap.add_argument('--project', default='ADMINTOOLKIT')
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

    if not args.backend_url:
        args.backend_url = discover_backend_url(client, args.url)
        if not args.backend_url:
            sys.exit('no %s webapp found on %s — create the toolkit webapp first, '
                     'or pass --backend-url' % (WEBAPP_TYPE, args.url))
        print('discovered backend_url: %s' % args.backend_url)

    plugin = client.get_plugin(PLUGIN_ID)
    env_name = ensure_code_env(client, plugin)
    apply_plugin_settings(plugin, args, env_name)

    project = ensure_project(client, args.project)
    handles = {}
    for component in TOOLS:
        handles[component], _ = ensure_tool(project, component)
    print('%d tool instances ready in %s' % (len(handles), args.project))
    for name, component in AGENT_TYPES.items():
        ensure_agent(project, name, component, args.llm_id)
    print('%d agent instances ready (llm=%s)' % (len(AGENT_TYPES), args.llm_id))

    # Native interaction logging (DSS >= 14.5); a pre-14.5 instance degrades
    # with a message — everything provisioned above still works without it.
    try:
        cfg = plugin.get_settings().get_raw().get('config', {})
        connection = cfg.get('triage_connection') or 'filesystem_managed'
        logging_summary = ensure_interaction_logging(project, set(AGENT_TYPES), connection)
        print('interaction logging: dataset %s on %s%s, enabled on %d agent(s)'
              % (logging_summary['dataset'], connection,
                 ' (created)' if logging_summary['created'] else '',
                 len(logging_summary['agents'])))
        if logging_summary['traceExplorer']:
            print('Trace Explorer webapp: %(name)s (%(id)s)' % logging_summary['traceExplorer'])
        else:
            print('NO Trace Explorer webapp — ' + MANUAL_WEBAPP_STEP
                  % (args.project, logging_summary['dataset']))
    except Exception as exc:
        print('interaction logging NOT provisioned (needs DSS >= 14.5): %s' % exc)

    if not args.no_smoke:
        run_tool(handles['list-hosts'], 'smoke: list-hosts probe', {'probe': True})
        run_tool(handles['config-inspect'], 'smoke: config-inspect plugins',
                 {'domain': 'plugins', 'name_filter': 'admin-toolkit', 'top_n': 5})
    print('\nDONE — next: run test_tools.py/golden_check.py pointed at this instance, '
          'then publish the agents in Agent Hub.')


if __name__ == '__main__':
    main()
