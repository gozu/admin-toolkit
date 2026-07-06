#!/usr/bin/env python3
"""Agent Interaction Logging provisioning (DSS >= 14.5, verified on the 14.7 API).

Native mechanism replacing any custom trace/conversation storage: every agent
interaction (input, answer, tool calls, dku_trace) lands as a row in an
LLM-interaction-logging dataset, async-buffered on the LLM Mesh path — the
webapp's stateless as_llm() relay needs zero changes. Trace Explorer is a
built-in VISUAL webapp pointed at that dataset; the public API (incl. 14.7's
create_webapp) can only create CODE webapps, so the webapp itself stays a
one-time manual step surfaced in the provisioning epilogue.

Importable helper (used by provision_prod.py); also runnable standalone:

    DSS_API_KEY=<admin key> .venv/bin/python scripts/agents/interaction_logging.py \
        [--url https://...] [--project AGENTOPS] [--connection <conn>]
"""

import argparse
import os
import pathlib
import sys

DATASET_NAME = 'agent_interaction_logs'
FLUSH_EVERY_S = 30
CONTENT_MODE = 'FULL'  # dku_trace only populates in FULL mode

MANUAL_WEBAPP_STEP = (
    'one-time manual step: in project %s create a webapp > Visual webapp > '
    'Trace Explorer pointed at dataset %s, then chat traces are one click away '
    '(the toolkit chat links to it automatically once it exists).'
)


def ensure_interaction_logging(project, agent_names, connection, dataset_name=DATASET_NAME):
    """Idempotently create the DAY-partitioned logging dataset and enable
    EXPLICIT FULL-content interaction logging on every named agent version.

    Instance-level interaction logging can still be disabled globally; this
    only flips the per-agent selection (same semantics as the DSS UI toggle).
    Returns {'dataset', 'created', 'agents': [names], 'traceExplorer': {...}|None}.
    """
    summary = {'dataset': dataset_name, 'created': False, 'agents': [], 'traceExplorer': None}
    existing = {d.name for d in project.list_datasets()}
    if dataset_name not in existing:
        project.create_llm_interaction_logging_dataset(
            dataset_name, connection_id=connection, time_partitioning='DAY')
        summary['created'] = True

    wanted = set(agent_names)
    for item in project.list_agents() or []:
        raw = item if isinstance(item, dict) else getattr(item, 'raw', {}) or {}
        if raw.get('name') not in wanted or not raw.get('id'):
            continue
        agent = project.get_agent(raw['id'])
        settings = agent.get_settings()
        version_id = settings.active_version or settings.get_version_ids()[0]
        selection = settings.get_version_settings(version_id).interaction_logging_selection
        selection.enable(dataset_name,
                         settings={'flushEveryS': FLUSH_EVERY_S, 'contentMode': CONTENT_MODE})
        settings.save()
        summary['agents'].append(raw['name'])

    summary['traceExplorer'] = find_trace_explorer(project)
    return summary


def find_trace_explorer(project):
    """Existing Trace Explorer webapp in the project, or None (manual creation)."""
    for item in project.list_webapps() or []:
        data = getattr(item, '_data', None) or {}
        blob = ('%s %s' % (data.get('type') or '', data.get('name') or '')).lower()
        if 'trace' in blob and data.get('id'):
            return {'id': data['id'], 'name': data.get('name') or data['id']}
    return None


def main():
    import dataikuapi

    repo = pathlib.Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='')
    ap.add_argument('--project', default='AGENTOPS')
    ap.add_argument('--connection', default='',
                    help='connection for the logging dataset; empty = plugin triage_connection, then filesystem_managed')
    args = ap.parse_args()

    url = args.url or os.environ.get('DSS_URL') or (repo / '.dss-url').read_text().strip()
    key = os.environ.get('DSS_API_KEY') or (repo / '.dss-api-key').read_text().strip()
    client = dataikuapi.DSSClient(url, key)
    project = client.get_project(args.project)

    connection = args.connection
    if not connection:
        try:
            cfg = client.get_plugin('admin-toolkit').get_settings().get_raw().get('config', {})
            connection = cfg.get('triage_connection') or ''
        except Exception:
            connection = ''
    connection = connection or 'filesystem_managed'

    from test_agent import AGENT_TYPES  # noqa: E402  (same-dir import, like provision_prod)
    summary = ensure_interaction_logging(project, set(AGENT_TYPES), connection)
    print('dataset %s on %s%s; logging enabled on %d agent(s): %s'
          % (summary['dataset'], connection,
             ' (created)' if summary['created'] else ' (already there)',
             len(summary['agents']), ', '.join(summary['agents']) or '-'))
    if summary['traceExplorer']:
        print('Trace Explorer webapp found: %(name)s (%(id)s)' % summary['traceExplorer'])
    else:
        print('NO Trace Explorer webapp — ' + MANUAL_WEBAPP_STEP % (args.project, summary['dataset']))


if __name__ == '__main__':
    main()
