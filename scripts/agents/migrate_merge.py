#!/usr/bin/env python3
"""Live migration for the agents-plugin merge (admin-toolkit-agents → admin-toolkit).

Idempotent — safe to re-run. Two modes:

  migrate (default):
    .venv/bin/python scripts/agents/migrate_merge.py --url <dss url> [--key-file <path>]
        [--project AGENTOPS] [--also-project AGENTSSANDBOX] [--no-smoke]
        [--baseline <phase0 actualPackages.txt>] [--hour 7]
        [--triage-connection X] [--triage-recipient X] [--triage-mail-channel X]
    a. verify merged admin-toolkit is installed (and old plugin still present)
    b. update_packages() on plugin_admin-toolkit_managed (adds langchain et al.),
       diff vs baseline, restart the webapp backend
    c. copy old plugin settings → merged plugin config (by name, full declared
       config, incl. PASSWORD fields) + codeEnvName=plugin_admin-toolkit_managed
    d. clean recreate: per ops project, create new-type agents/tools, delete
       old-type instances (old localStorage chat threads go stale — accepted)
    e. shutdown agent kernels (kernel-pinning trap)
    f. repoint triage scenario via provision_all (runnableType → merged macro)
    g. smokes: list-hosts probe + config-inspect + one short chat per agent +
       plan-admin-action probe (kill switch untouched)

  decommission (run only after live verification passes):
    ... migrate_merge.py --url <dss url> --decommission
    uninstalls admin-toolkit-agents and deletes plugin_admin-toolkit-agents_managed.

On locked-down instances (tam-global) steps that need plugin-settings or
plugin-admin rights may fail with UnauthorizedException — the script keeps
going and ends with a MANUAL ACTIONS checklist instead of dying mid-way.
"""

import argparse
import json
import os
import pathlib
import sys
import time

import dataikuapi

HERE = pathlib.Path(__file__).parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / 'python-lib'))

from test_agent import AGENT_TYPES, set_agent_config  # noqa: E402
from test_tools import TOOLS, run_tool  # noqa: E402

OLD_PLUGIN = 'admin-toolkit-agents'
NEW_PLUGIN = 'admin-toolkit'
OLD_ENV = 'plugin_admin-toolkit-agents_managed'
NEW_ENV = 'plugin_admin-toolkit_managed'
OLD_TOOL_PREFIX = 'Custom_agent_tool_%s_' % OLD_PLUGIN
NEW_TOOL_TYPE = 'Custom_agent_tool_%s_%%s' % NEW_PLUGIN
OLD_AGENT_PREFIX = 'agent_%s_' % OLD_PLUGIN
NEW_AGENT_TYPE = 'agent_%s_%%s' % NEW_PLUGIN

# the 17 params that moved onto the merged plugin's settings page
AGENT_PARAM_KEYS = [
    'backend_url', 'red_actions_password', 'host_keys_password', 'host_allowlist',
    'verify_tls', 'http_timeout_s', 'heavy_timeout_s', 'default_llm_id',
    'enable_red_actions', 'triage_connection', 'triage_score_threshold',
    'triage_mail_channel', 'triage_recipient',
]

MANUAL = []  # collected manual follow-ups for locked-down instances


def manual(msg):
    MANUAL.append(msg)
    print('  [MANUAL NEEDED] %s' % msg)


def step(title):
    print('\n=== %s' % title)


def get_client(args):
    key = (pathlib.Path(args.key_file).read_text().strip() if args.key_file
           else os.environ.get('DSS_API_KEY', ''))
    if not key:
        sys.exit('need DSS_API_KEY env var or --key-file')
    return dataikuapi.DSSClient(args.url, key)


def verify_plugins(client, need_old=True):
    step('a. plugin versions')
    plugins = {p['id']: p.get('version') for p in client.list_plugins()}
    new_v = plugins.get(NEW_PLUGIN)
    if not new_v:
        sys.exit('%s not installed' % NEW_PLUGIN)
    parts = [int(x) for x in new_v.split('.')]
    if parts < [0, 4, 641]:
        sys.exit('%s v%s < 0.4.641 — deploy the merged plugin first' % (NEW_PLUGIN, new_v))
    print('  %s v%s OK; %s %s' % (NEW_PLUGIN, new_v, OLD_PLUGIN,
                                  'v%s still installed' % plugins[OLD_PLUGIN]
                                  if OLD_PLUGIN in plugins else 'NOT installed'))
    if need_old and OLD_PLUGIN not in plugins:
        print('  (old plugin gone — settings copy will be skipped, recreate still runs)')
    return plugins


def update_env(client, baseline_path):
    step('b. code env %s update_packages' % NEW_ENV)
    env = client.get_code_env('PYTHON', NEW_ENV)
    before = (env.get_definition().get('actualPackageList') or '')
    t0 = time.time()
    try:
        env.update_packages()
    except Exception as exc:
        manual('code env update failed (%s: %s) — update %s from the DSS UI'
               % (type(exc).__name__, str(exc)[:200], NEW_ENV))
        return
    after = (env.get_definition().get('actualPackageList') or '')
    base = before
    if baseline_path:
        base = pathlib.Path(baseline_path).read_text()
    new_pkgs = sorted(set(after.splitlines()) - set(base.splitlines()))
    print('  done in %.0fs — %d new packages vs baseline' % (time.time() - t0, len(new_pkgs)))
    for p in new_pkgs:
        print('    + %s' % p)
    missing = [p for p in ('langchain', 'langchain-core', 'requests')
               if not any(l.split('==')[0].lower() == p for l in after.splitlines())]
    if missing:
        print('  [WARN] expected packages not in actualPackageList: %s' % missing)


def restart_webapp(client, args):
    step('b2. restart webapp backend %s/%s' % (args.webapp_project, args.webapp_id))
    try:
        webapp = client.get_project(args.webapp_project).get_webapp(args.webapp_id)
        fut = webapp.restart_backend()
        if hasattr(fut, 'wait_for_result'):
            fut.wait_for_result()
        print('  restarted')
    except Exception as exc:
        manual('webapp backend restart failed (%s: %s) — restart it (akaos: make '
               'deploy does it; tam: sudo dss_webapp_restart_DIAG_PARSER_BRANCH1 Gv9CLFn)'
               % (type(exc).__name__, str(exc)[:200]))


def copy_settings(client):
    """Old plugin config → merged plugin config, by param name. Returns the
    merged resolved config dict (best effort) for later steps."""
    step('c. settings copy %s → %s' % (OLD_PLUGIN, NEW_PLUGIN))
    old_cfg = {}
    try:
        old_raw = client.get_plugin(OLD_PLUGIN).get_settings().get_raw()
        old_cfg = (old_raw or {}).get('config') or {}
        print('  old config read: %d keys (%s)' % (len(old_cfg), ', '.join(sorted(old_cfg))))
    except Exception as exc:
        manual('cannot READ old plugin settings (%s: %s) — copy the 13 agents params '
               '+ both PASSWORD fields from the old plugin settings page by hand'
               % (type(exc).__name__, str(exc)[:150]))
    try:
        settings = client.get_plugin(NEW_PLUGIN).get_settings()
        raw = settings.get_raw()
        cfg = raw.setdefault('config', {})
        copied = []
        for k in AGENT_PARAM_KEYS:
            if k in old_cfg and old_cfg[k] not in (None, ''):
                cfg[k] = old_cfg[k]
                copied.append(k)
        raw['codeEnvName'] = NEW_ENV
        settings.save()
        print('  merged config saved: copied %s; codeEnvName=%s' % (copied or 'nothing', NEW_ENV))
        return cfg
    except Exception as exc:
        manual('cannot WRITE merged plugin settings (%s: %s) — on the %s settings '
               'page set the agents params (values from the old plugin page) and '
               'select code env %s' % (type(exc).__name__, str(exc)[:150], NEW_PLUGIN, NEW_ENV))
        return dict(old_cfg)


def recreate_in_project(client, project_key, llm_fallback):
    step('d. clean recreate in %s' % project_key)
    if project_key not in {p['projectKey'] for p in client.list_projects()}:
        print('  project absent — skipped')
        return {}
    project = client.get_project(project_key)

    handles = {}
    for component in TOOLS:
        wanted = 'atk %s' % component
        new_type = NEW_TOOL_TYPE % component
        keep, old_ids = None, []
        for t in project.list_agent_tools() or []:
            raw = t if isinstance(t, dict) else getattr(t, 'raw', {})
            if raw.get('name') != wanted:
                continue
            if raw.get('type') == new_type:
                keep = raw['id']
            elif str(raw.get('type', '')).startswith(OLD_TOOL_PREFIX):
                old_ids.append(raw['id'])
        if keep is None:
            tool = project.new_agent_tool(new_type, name=wanted).create()
            keep = tool.id
            print('  tool %-24s created (%s)' % (wanted, keep))
        else:
            tool = project.get_agent_tool(keep)
            print('  tool %-24s exists  (%s)' % (wanted, keep))
        for oid in old_ids:
            project.get_agent_tool(oid).delete()
            print('    deleted old-type instance %s' % oid)
        handles[component] = tool

    for name, component in AGENT_TYPES.items():
        new_type = NEW_AGENT_TYPE % component
        keep, old = None, []
        for a in project.list_agents() or []:
            raw = a if isinstance(a, dict) else getattr(a, 'raw', {})
            if raw.get('name') != name:
                continue
            agent = project.get_agent(raw['id'])
            araw = agent.get_settings().get_raw()
            av = araw.get('activeVersion')
            ver = next((v for v in araw.get('versions', []) if v.get('versionId') == av),
                       (araw.get('versions') or [{}])[0])
            atype = ver.get('pluginAgentType', '')
            if atype == new_type:
                keep = agent
            elif atype.startswith(OLD_AGENT_PREFIX):
                old.append((agent, dict(ver.get('pluginAgentConfig') or {})))
        old_cfg = old[0][1] if old else {}
        if keep is None:
            keep = project.create_agent(name, 'PLUGIN_AGENT', plugin_agent_type=new_type)
            cfg = old_cfg or ({'llm_id': llm_fallback} if llm_fallback else {})
            if cfg:
                set_agent_config(keep, cfg)
            print('  agent %-24s created (%s) config=%s' % (name, keep.id, cfg))
        else:
            print('  agent %-24s exists  (%s)' % (name, keep.id))
        for agent, _ in old:
            try:
                agent.shutdown()
            except Exception:
                pass
            agent.delete()
            print('    deleted old-type agent %s' % agent.id)
        try:
            keep.shutdown()  # force fresh kernel on merged plugin code
        except Exception:
            pass
    return handles


def repoint_triage(client, merged_cfg, args):
    step('f. triage scenario repoint (provision_all)')
    from atk_agent_common import config as config_mod
    from atk_agent_common.triage import provision
    cfg = dict(merged_cfg or {})
    for k, v in (('triage_connection', args.triage_connection),
                 ('triage_recipient', args.triage_recipient),
                 ('triage_mail_channel', args.triage_mail_channel)):
        if v:
            cfg[k] = v
    settings = config_mod.resolve(cfg)
    if not settings.get('triage_connection') or not settings.get('triage_recipient'):
        manual('triage provisioning skipped — merged config lacks triage_connection/'
               'triage_recipient (pass --triage-connection/--triage-recipient or fix settings, '
               'then re-run: .venv/bin/python scripts/agents/provision_triage.py --url %s)' % args.url)
        return
    result = provision.provision_all(client, settings, hour=args.hour)
    print(json.dumps(result, indent=1, default=str))
    if not result.get('ok'):
        manual('provision_all reported not-ok — inspect output above')


def smokes(client, handles, args):
    step('g. smokes')
    if not handles:
        print('  no tool handles (project skipped?) — no smokes')
        return
    run_tool(handles['list-hosts'], 'smoke: list-hosts probe', {'probe': True})
    run_tool(handles['config-inspect'], 'smoke: config-inspect plugins',
             {'domain': 'plugins', 'name_filter': 'admin-toolkit', 'top_n': 5})
    plan = run_tool(handles['plan-admin-action'], 'smoke: plan k8s-exec-config-tune (red path, plan only)',
                    {'action': 'k8s-exec-config-tune',
                     'target': {'configName': 'eks-default', 'changes': {'memRequestMB': 2048}}})
    out = (plan or {}).get('output') or {}
    if out.get('confirm_token'):
        print('  red_actions_password check: PASS (confirm token minted)')
    else:
        manual('plan-admin-action minted no confirm token — red_actions_password not working '
               'on the merged settings page: %s' % json.dumps(out.get('error'))[:200])
    project = client.get_project(args.project)
    for name in AGENT_TYPES:
        agent = None
        for a in project.list_agents() or []:
            raw = a if isinstance(a, dict) else getattr(a, 'raw', {})
            if raw.get('name') == name:
                agent = project.get_agent(raw['id'])
                break
        if agent is None:
            manual('agent %r missing for chat smoke' % name)
            continue
        llm = agent.as_llm()
        t0 = time.time()
        comp = llm.new_completion()
        comp.with_message('Health check: reply with the single word OK. Do not call any tools.')
        resp = comp.execute()
        ok = bool(resp.success)
        text = (resp.text or '')[:80] if ok else str(getattr(resp, 'raw', ''))[:200]
        print('  chat %-24s %s (%.0fs) %r' % (name, 'OK' if ok else 'FAILED', time.time() - t0, text))
        if not ok:
            manual('chat smoke failed for %s' % name)


def decommission(client):
    step('DECOMMISSION %s' % OLD_PLUGIN)
    plugins = {p['id'] for p in client.list_plugins()}
    if OLD_PLUGIN in plugins:
        try:
            res = client.get_plugin(OLD_PLUGIN).delete(force=True)
            if hasattr(res, 'wait_for_result'):
                res.wait_for_result()
            print('  plugin %s uninstalled' % OLD_PLUGIN)
        except Exception as exc:
            manual('plugin uninstall failed (%s: %s) — remove %s from the DSS plugins page'
                   % (type(exc).__name__, str(exc)[:200], OLD_PLUGIN))
    else:
        print('  plugin already gone')
    try:
        if any(e.get('envName') == OLD_ENV for e in client.list_code_envs()):
            client.get_code_env('PYTHON', OLD_ENV).delete()
            print('  code env %s deleted' % OLD_ENV)
        else:
            print('  code env already gone')
    except Exception as exc:
        manual('code env delete failed (%s: %s) — delete %s from Administration → Code envs'
               % (type(exc).__name__, str(exc)[:200], OLD_ENV))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', required=True)
    ap.add_argument('--key-file', default='')
    ap.add_argument('--project', default='AGENTOPS')
    ap.add_argument('--also-project', default='', help='extra ops project to clean-recreate (e.g. AGENTSSANDBOX)')
    ap.add_argument('--webapp-project', default='')
    ap.add_argument('--webapp-id', default='')
    ap.add_argument('--baseline', default='', help='phase0 actualPackageList file for the env diff')
    ap.add_argument('--hour', type=int, default=7)
    ap.add_argument('--triage-connection', default='')
    ap.add_argument('--triage-recipient', default='')
    ap.add_argument('--triage-mail-channel', default='')
    ap.add_argument('--no-smoke', action='store_true')
    ap.add_argument('--skip-env', action='store_true')
    ap.add_argument('--decommission', action='store_true',
                    help='ONLY uninstall the old plugin + delete its code env (run after verification)')
    args = ap.parse_args()

    client = get_client(args)

    if args.decommission:
        decommission(client)
    else:
        verify_plugins(client)
        if not args.skip_env:
            update_env(client, args.baseline)
            if args.webapp_project and args.webapp_id:
                restart_webapp(client, args)
        merged_cfg = copy_settings(client)
        llm_fallback = (merged_cfg or {}).get('default_llm_id', '')
        handles = recreate_in_project(client, args.project, llm_fallback)
        if args.also_project:
            recreate_in_project(client, args.also_project, llm_fallback)
        repoint_triage(client, merged_cfg, args)
        if not args.no_smoke:
            smokes(client, handles, args)

    print('\n================ RESULT ================')
    if MANUAL:
        print('MANUAL ACTIONS REQUIRED:')
        for i, m in enumerate(MANUAL, 1):
            print(' %d. %s' % (i, m))
        return 1
    print('all steps completed clean')
    return 0


if __name__ == '__main__':
    sys.exit(main())
