"""Trace Explorer auto-provisioning (DSS >= 14.5, ground-truthed on 14.7).

Makes a chat turn's trace one click away: DAY-partitioned interaction-logging
dataset + FULL-mode logging on every ADMINTOOLKIT agent + the Dataiku
`traces-explorer` plugin webapp found-or-created and pointed at that dataset.

The plugin webapp CAN be created programmatically — but only by creating a
STANDARD webapp and flipping its type via the settings PUT (verified live on
14.7: both create paths reject plugin types server-side; the settings save
accepts the change and re-materializes the plugin webapp shape). The plugin
declares autoStartBackend:false, so the backend is started explicitly.

Runs against the caller's client (ADMINTOOLKIT may live on the active remote
host). Returns a provision_all-style steps trail
(atk_agent_common/triage/provision.py shape): {'ok', 'steps', 'webAppId'?,
'viewPath'?}. Also imported by scripts/agents/interaction_logging.py.
"""

import logging
import re
import time

_LOGGER = logging.getLogger(__name__)

TRACE_EXPLORER_PLUGIN_ID = 'traces-explorer'
TRACE_EXPLORER_WEBAPP_TYPE = 'webapp_traces-explorer_traces-explorer'
TRACE_EXPLORER_WEBAPP_NAME = 'Agent Trace Explorer'

DATASET_NAME = 'agent_interaction_logs'
FLUSH_EVERY_S = 30
CONTENT_MODE = 'FULL'  # dku_trace only populates in FULL mode
# The dku-trace column name in interaction-logging datasets — ground truth
# from the live 14.7 dataset schema (2026-07-07): the column is `dku_trace`
# (not `trace`); populated only in FULL mode.
TRACE_COLUMN = 'dku_trace'

_BACKEND_START_TIMEOUT_S = 30


def ensure_interaction_logging(project, agent_names, connection,
                               dataset_name=DATASET_NAME):
    """Idempotently create the DAY-partitioned logging dataset and enable
    EXPLICIT FULL-content interaction logging on every named agent version.

    Instance-level interaction logging can still be disabled globally; this
    only flips the per-agent selection (same semantics as the DSS UI toggle).
    Returns {'dataset', 'created', 'agents': [names]}.
    """
    summary = {'dataset': dataset_name, 'created': False, 'agents': []}
    existing = {d.name for d in project.list_datasets()}
    if dataset_name not in existing:
        project.create_llm_interaction_logging_dataset(
            dataset_name, connection_id=connection, time_partitioning='DAY')
        summary['created'] = True

    wanted = set(agent_names) if agent_names is not None else None
    for item in project.list_agents() or []:
        raw = item if isinstance(item, dict) else getattr(item, 'raw', {}) or {}
        if not raw.get('id'):
            continue
        if wanted is not None and raw.get('name') not in wanted:
            continue
        agent = project.get_agent(raw['id'])
        settings = agent.get_settings()
        version_id = settings.active_version or settings.get_version_ids()[0]
        selection = settings.get_version_settings(version_id).interaction_logging_selection
        selection.enable(dataset_name,
                         settings={'flushEveryS': FLUSH_EVERY_S, 'contentMode': CONTENT_MODE})
        settings.save()
        summary['agents'].append(raw.get('name') or raw['id'])
    return summary


def find_trace_explorer(project):
    """Existing Trace Explorer webapp in the project ({'id','name','type'}) or
    None. Any webapp whose type/name mentions 'trace' counts — never create a
    duplicate next to a manually-made one."""
    for item in project.list_webapps() or []:
        data = getattr(item, '_data', None) or {}
        blob = ('%s %s' % (data.get('type') or '', data.get('name') or '')).lower()
        if 'trace' in blob and data.get('id'):
            return {'id': data['id'], 'name': data.get('name') or data['id'],
                    'type': data.get('type') or ''}
    return None


def view_path(project_key, webapp_id, name):
    """DSS-relative shell path of the webapp (the frontend prefixes the
    active host's base URL — multi-instance rule)."""
    slug = re.sub(r'[^a-z0-9]+', '-', (name or '').lower()).strip('-') or 'trace-explorer'
    return '/projects/%s/webapps/%s_%s/view' % (project_key, webapp_id, slug)


def _plugin_installed(client):
    try:
        return any((p.get('id') if isinstance(p, dict) else None) == TRACE_EXPLORER_PLUGIN_ID
                   for p in client.list_plugins() or [])
    except Exception as exc:
        _LOGGER.debug('list_plugins failed: %s', exc)
        return False


def _logging_connection(client):
    """Connection for the logging dataset: the plugin's triage_connection,
    falling back to filesystem_managed (same rule as scripts/agents)."""
    try:
        cfg = client.get_plugin('admin-toolkit').get_settings().get_raw().get('config', {})
        conn = (cfg.get('triage_connection') or '').strip()
    except Exception:
        conn = ''
    return conn or 'filesystem_managed'


def _create_webapp_raw(client, project_key):
    """Create the plugin webapp in two steps (proven live on 14.7): POST a
    STANDARD webapp, then flip its type via the settings PUT — both the
    create_webapp() helper AND the raw create endpoint reject plugin types
    server-side ("Webapp type not supported"), but the settings save accepts
    the type change and DSS re-materializes the plugin webapp's params (the
    Trace Explorer html/backend shape) around it. Returns the new webAppId."""
    created = client._perform_json(
        'POST', '/projects/%s/webapps/' % project_key,
        body={'name': TRACE_EXPLORER_WEBAPP_NAME, 'type': 'STANDARD'})
    webapp_id = (created or {}).get('webAppId') or (created or {}).get('id')
    if not webapp_id:
        return None
    settings = client.get_project(project_key).get_webapp(webapp_id).get_settings()
    settings.get_raw()['type'] = TRACE_EXPLORER_WEBAPP_TYPE
    settings.save()
    return webapp_id


def _configure_webapp(webapp):
    """Set ONLY the three v1.3.1-known config keys; newer plugin versions'
    extra params (e.g. the Data Storage selector) are left untouched.

    Plugin-webapp config lives at the TOP LEVEL of the settings raw
    (`raw['config']`) on DSS 14.7 — a `params.config` write is silently
    dropped by the settings save (ground truth 2026-07-07: re-provisioned
    explorers came back with config {} and loaded no traces)."""
    settings = webapp.get_settings()
    raw = settings.get_raw()
    config = raw.get('config')
    if not isinstance(config, dict):
        config = {}
        raw['config'] = config
    config['llm_responses_dataset'] = DATASET_NAME
    config['llm_responses_column'] = TRACE_COLUMN
    config['log_level'] = config.get('log_level') or 'INFO'
    # plugin meta has hasBackend; the explicit start still needs the flag
    raw.setdefault('params', {})['backendEnabled'] = True
    settings.save()


def _start_backend(webapp):
    """Start (or restart) the webapp backend and wait until it reports
    running — the plugin ships autoStartBackend:false."""
    webapp.start_or_restart_backend()
    deadline = time.monotonic() + _BACKEND_START_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            if webapp.get_state().running:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def ensure_trace_explorer(client, project_key='ADMINTOOLKIT'):
    """Find-or-create + configure + start the Trace Explorer webapp, plus the
    interaction-logging dataset it reads. Idempotent; every step lands in the
    steps trail even on failure."""
    steps = []

    def fail(step, exc_or_msg):
        msg = exc_or_msg if isinstance(exc_or_msg, str) else (
            '%s: %s' % (type(exc_or_msg).__name__, str(exc_or_msg)[:300]))
        steps.append({'step': step, 'status': 'error', 'message': msg})
        return {'ok': False, 'steps': steps}

    if not _plugin_installed(client):
        return fail('plugin', 'The Dataiku "Traces Explorer" plugin (id '
                    'traces-explorer) is not installed on this host — install '
                    'it from the plugin store, then provision again.')
    steps.append({'step': 'plugin', 'status': 'ok', 'message': TRACE_EXPLORER_PLUGIN_ID})

    try:
        project = client.get_project(project_key)
        project.get_summary()
    except Exception as exc:
        return fail('project:%s' % project_key, exc)

    try:
        logging_summary = ensure_interaction_logging(
            project, agent_names=None, connection=_logging_connection(client))
        steps.append({'step': 'dataset:%s' % DATASET_NAME,
                      'status': 'created' if logging_summary['created'] else 'already_exists',
                      'message': 'FULL logging on %d agent(s): %s' % (
                          len(logging_summary['agents']),
                          ', '.join(logging_summary['agents']) or '-')})
    except Exception as exc:
        return fail('dataset:%s' % DATASET_NAME, exc)

    try:
        found = find_trace_explorer(project)
        if found:
            webapp_id = found['id']
            webapp_name = found['name']
            steps.append({'step': 'webapp', 'status': 'already_exists',
                          'message': '%s (%s)' % (webapp_name, webapp_id)})
        else:
            webapp_id = _create_webapp_raw(client, project_key)
            if not webapp_id:
                return fail('webapp', 'webapp creation returned no id')
            webapp_name = TRACE_EXPLORER_WEBAPP_NAME
            steps.append({'step': 'webapp', 'status': 'created',
                          'message': '%s (%s)' % (webapp_name, webapp_id)})
    except Exception as exc:
        return fail('webapp', exc)

    webapp = project.get_webapp(webapp_id)
    try:
        _configure_webapp(webapp)
        steps.append({'step': 'config', 'status': 'ok',
                      'message': 'llm_responses_dataset=%s column=%s'
                                 % (DATASET_NAME, TRACE_COLUMN)})
    except Exception as exc:
        return fail('config', exc)

    try:
        running = _start_backend(webapp)
        steps.append({'step': 'backend',
                      'status': 'ok' if running else 'unverified',
                      'message': 'running' if running else
                                 'start requested; not confirmed running within %ss'
                                 % _BACKEND_START_TIMEOUT_S})
    except Exception as exc:
        return fail('backend', exc)

    return {'ok': True, 'steps': steps, 'webAppId': webapp_id,
            'projectKey': project_key,
            'viewPath': view_path(project_key, webapp_id, webapp_name)}
