"""In-product provisioning of the agents layer — the no-CLI twin of
scripts/agents/provision_prod.py.

Wired into three flows so no host ever needs a terminal:
  • POST /api/agents/provision — the Agents page "Set up agents" empty-state CTA,
  • the one-click remote installer (routes/hosts.py install-toolkit, agents step),
  • the ADMINTOOLKIT bootstrap (routes/hosts.py macro-project) right after the
    support project is created.

Scope vs the CLI script: only what runs idempotently in seconds through the
caller's client (local or the active remote host) — select an already-built
plugin code env, fill blank settings (backend_url discovery), ensure the
support project + tool instances + agent instances, enable interaction
logging. It never BUILDS a code env (the webapp/installer already guarantee
one exists) and never picks an LLM — default_llm_id is a cost/model decision,
surfaced as an `action_needed` step pointing at Agent Tuning; the agent
runtime's resolve_llm_id has the same remediation at chat time.

Returns the trace_explorer.ensure_trace_explorer steps-trail shape:
{'ok', 'steps': [{'step','status','message'}], 'summary'}.
"""

import logging

from adk_backend.trace_explorer import ensure_interaction_logging

_LOGGER = logging.getLogger(__name__)

PLUGIN_ID = 'admin-toolkit'
ENV_BASE = 'plugin_%s_managed' % PLUGIN_ID
WEBAPP_TYPE = 'webapp_%s_%s' % (PLUGIN_ID, PLUGIN_ID)

# Canonical component ids. scripts/agents/{test_tools,test_agent}.py import
# these, so the CLI harnesses and in-product provisioning cannot drift.
TOOL_COMPONENTS = ['list-hosts', 'instance-health', 'compute-cost',
                   'config-inspect', 'log-errors', 'log-tail', 'toolkit-get',
                   'list-capabilities', 'storage-footprint',
                   'k8s-health', 'db-health', 'plan-admin-action',
                   'execute-admin-action']
AGENT_COMPONENTS = {'ATK Health Triage': 'health-triage',
                    'ATK Scoping Architect': 'scoping-architect',
                    'ATK Ops Actuator': 'ops-actuator'}


def _resolve_env_name(client, settings_raw):
    """DSS auto-renames plugin code envs on recreate (…_managed_1, _2, …).
    Prefer the env the plugin settings already point at, else the newest
    family member, else '' — same rule as provision_prod.py."""
    names = {e.get('envName') for e in client.list_code_envs() or []}
    current = (settings_raw.get('codeEnvName') or '').strip()
    if current in names:
        return current
    family = sorted((n for n in names
                     if n and (n == ENV_BASE or n.startswith(ENV_BASE + '_'))),
                    key=lambda n: (len(n), n))
    return family[-1] if family else ''


def _discover_backend_url(client):
    """Backend base of this host's own admin-toolkit webapp
    (<base>/web-apps-backends/<projectKey>/<webappId>), or ''. Base prefers
    studioExternalUrl (kernels resolve it host-wide) over the API client's
    host. Same sweep as atk_agent_common.config's runtime fallback — set once
    here so kernels skip the per-start project scan."""
    # Deferred import: scripts/agents import this module for the component
    # constants and must stay runnable without flask (utils pulls it in).
    from adk_backend.utils import studio_external_url
    base = (studio_external_url(client) or getattr(client, 'host', '') or '').rstrip('/')
    if not base:
        return ''
    for proj in client.list_projects() or []:
        key = proj.get('projectKey')
        try:
            webapps = client.get_project(key).list_webapps() or []
        except Exception:
            continue
        for wa in webapps:
            raw = wa if isinstance(wa, dict) else getattr(wa, 'raw', {}) or {}
            if raw.get('type') == WEBAPP_TYPE and raw.get('id'):
                return '%s/web-apps-backends/%s/%s' % (base, key, raw['id'])
    return ''


def ensure_agents_provisioned(client, project_key='ADMINTOOLKIT'):
    """Idempotently provision the full agents layer on the client's host."""
    steps = []

    def fail(step, exc_or_msg):
        msg = exc_or_msg if isinstance(exc_or_msg, str) else (
            '%s: %s' % (type(exc_or_msg).__name__, str(exc_or_msg)[:300]))
        steps.append({'step': step, 'status': 'error', 'message': msg})
        return {'ok': False, 'steps': steps}

    # ── plugin installed? ──
    try:
        installed = {p.get('id'): p for p in client.list_plugins() or []
                     if isinstance(p, dict)}
    except Exception as exc:
        return fail('plugin', exc)
    if PLUGIN_ID not in installed:
        return fail('plugin', 'The %s plugin is not installed on this host — '
                    'install it first (host picker → Install).' % PLUGIN_ID)
    steps.append({'step': 'plugin', 'status': 'ok',
                  'message': '%s v%s' % (PLUGIN_ID, installed[PLUGIN_ID].get('version') or '?')})

    # ── plugin settings: code env selection + backend_url, one save ──
    try:
        plugin_settings = client.get_plugin(PLUGIN_ID).get_settings()
        raw = plugin_settings.get_raw()
        cfg = raw.setdefault('config', {})
        dirty = False

        env_name = _resolve_env_name(client, raw)
        if not env_name:
            return fail('codeenv', 'No %s code env on this host — build it once in '
                        'Plugins → Admin Toolkit → Settings (the one-click host '
                        'installer does this automatically), then retry.' % ENV_BASE)
        if (raw.get('codeEnvName') or '').strip() != env_name:
            raw['codeEnvName'] = env_name
            dirty = True
        steps.append({'step': 'codeenv', 'status': 'ok', 'message': env_name})

        if not (cfg.get('backend_url') or '').strip():
            backend_url = _discover_backend_url(client)
            if backend_url:
                cfg['backend_url'] = backend_url
                dirty = True
                steps.append({'step': 'backend_url', 'status': 'ok', 'message': backend_url})
            else:
                # Agents' tools need the toolkit webapp ON THIS HOST; runtime
                # discovery re-checks per kernel start, so this heals itself
                # once the webapp exists there.
                steps.append({'step': 'backend_url', 'status': 'action_needed',
                              'message': 'no admin-toolkit webapp found on this host — '
                                         'create one there so agent tools can reach a backend'})
        if dirty:
            plugin_settings.save()
    except Exception as exc:
        return fail('settings', exc)

    # ── support project (agent kernels run on the DSS host: containerMode NONE) ──
    try:
        existing = {p['projectKey'] for p in client.list_projects() or []}
        if project_key not in existing:
            client.create_project(project_key, 'Admin Toolkit', 'admin',
                                  description='admin-toolkit: macros + agent tools + agent instances')
            steps.append({'step': 'project', 'status': 'created', 'message': project_key})
        else:
            steps.append({'step': 'project', 'status': 'already_exists', 'message': project_key})
        project = client.get_project(project_key)
        project_settings = project.get_settings()
        if (project_settings.get_raw().get('container') or {}).get('containerMode') != 'NONE':
            project_settings.get_raw()['container'] = {'containerMode': 'NONE'}
            project_settings.save()
    except Exception as exc:
        return fail('project', exc)

    # ── tool instances ──
    try:
        have = {}
        for t in project.list_agent_tools() or []:
            t_raw = t if isinstance(t, dict) else getattr(t, 'raw', {}) or {}
            have[t_raw.get('name')] = t_raw
        created = 0
        for component in TOOL_COMPONENTS:
            if 'atk %s' % component in have:
                continue
            project.new_agent_tool('Custom_agent_tool_%s_%s' % (PLUGIN_ID, component),
                                   name='atk %s' % component).create()
            created += 1
        steps.append({'step': 'tools', 'status': 'ok',
                      'message': '%d created, %d already there'
                                 % (created, len(TOOL_COMPONENTS) - created)})
    except Exception as exc:
        return fail('tools', exc)

    # ── agent instances (no llm_id override — resolve_llm_id's chain applies) ──
    try:
        have = set()
        for a in project.list_agents() or []:
            a_raw = a if isinstance(a, dict) else getattr(a, 'raw', {}) or {}
            have.add(a_raw.get('name'))
        created = 0
        for name, component in AGENT_COMPONENTS.items():
            if name in have:
                continue
            project.create_agent(name, 'PLUGIN_AGENT',
                                 plugin_agent_type='agent_%s_%s' % (PLUGIN_ID, component))
            created += 1
        steps.append({'step': 'agents', 'status': 'ok',
                      'message': '%d created, %d already there'
                                 % (created, len(AGENT_COMPONENTS) - created)})
    except Exception as exc:
        return fail('agents', exc)

    # ── interaction logging (DSS >= 14.5; older hosts degrade, never fatal) ──
    try:
        connection = (cfg.get('toolkit_db_connection')
                      or cfg.get('triage_connection') or '').strip() or 'filesystem_managed'
        logging_summary = ensure_interaction_logging(project, set(AGENT_COMPONENTS), connection)
        steps.append({'step': 'logging', 'status': 'ok',
                      'message': 'dataset %s on %s, FULL logging on %d agent(s)'
                                 % (logging_summary['dataset'], connection,
                                    len(logging_summary['agents']))})
    except Exception as exc:
        _LOGGER.info('interaction logging not provisioned: %s', exc)
        steps.append({'step': 'logging', 'status': 'skipped',
                      'message': 'needs DSS >= 14.5 — %s' % str(exc)[:150]})

    # ── LLM: never auto-picked (cost/model decision stays with the admin) ──
    if (cfg.get('default_llm_id') or '').strip():
        steps.append({'step': 'llm', 'status': 'ok', 'message': cfg['default_llm_id']})
    else:
        steps.append({'step': 'llm', 'status': 'action_needed',
                      'message': 'no default LLM configured — pick a model on the '
                                 'Agent Tuning page (or set default_llm_id in the '
                                 'plugin settings)'})

    return {'ok': True, 'steps': steps,
            'summary': '%d agents · %d tools ready in %s'
                       % (len(AGENT_COMPONENTS), len(TOOL_COMPONENTS), project_key)}
