#!/usr/bin/env python3
"""Agent Interaction Logging provisioning — thin wrapper over
python-lib/adk_backend/trace_explorer.py (the webapp backend imports that
module directly; this script keeps the ops-CLI entrypoint and the
provision_prod.py imports working).

Native mechanism replacing any custom trace/conversation storage: every agent
interaction (input, answer, tool calls, dku_trace) lands as a row in an
LLM-interaction-logging dataset, async-buffered on the LLM Mesh path. The
Trace Explorer plugin webapp over that dataset is now AUTO-PROVISIONED
(create_webapp() rejects plugin types, but the raw REST POST accepts them —
see trace_explorer.ensure_trace_explorer, also exposed in the webapp as the
"Set up Trace Explorer" CTA / POST /api/agents/trace-explorer/provision).

Importable helper (used by provision_prod.py); also runnable standalone:

    DSS_API_KEY=<admin key> .venv/bin/python scripts/agents/interaction_logging.py \
        [--url https://...] [--project AGENTOPS] [--connection <conn>] [--webapp]
"""

import argparse
import os
import pathlib
import sys

_REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / 'python-lib'))

from adk_backend.trace_explorer import (  # noqa: E402
    CONTENT_MODE,
    DATASET_NAME,
    FLUSH_EVERY_S,
    ensure_interaction_logging,
    ensure_trace_explorer,
    find_trace_explorer,
)

__all__ = ['CONTENT_MODE', 'DATASET_NAME', 'FLUSH_EVERY_S', 'MANUAL_WEBAPP_STEP',
           'ensure_interaction_logging', 'ensure_trace_explorer', 'find_trace_explorer']

# Kept for provision_prod.py's epilogue (same two-%s signature). Creation is
# no longer manual — this is the pointer to the automated paths.
MANUAL_WEBAPP_STEP = (
    'auto-provision it: in project %s the toolkit webapp\'s Agents page has a '
    '"Set up Trace Explorer" button (or run this script with --webapp) — it '
    'creates the traces-explorer plugin webapp over dataset %s and starts its '
    'backend.'
)


def main():
    import dataikuapi

    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='')
    ap.add_argument('--project', default='AGENTOPS')
    ap.add_argument('--connection', default='',
                    help='connection for the logging dataset; empty = plugin triage_connection, then filesystem_managed')
    ap.add_argument('--webapp', action='store_true',
                    help='also find-or-create + configure + start the Trace Explorer webapp')
    args = ap.parse_args()

    url = args.url or os.environ.get('DSS_URL') or (_REPO / '.dss-url').read_text().strip()
    key = os.environ.get('DSS_API_KEY') or (_REPO / '.dss-api-key').read_text().strip()
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

    if args.webapp:
        result = ensure_trace_explorer(client, project_key=args.project)
        for step in result['steps']:
            print('  %-24s %-14s %s' % (step['step'], step['status'],
                                        step.get('message') or ''))
        print('trace explorer %s: %s' % ('READY' if result['ok'] else 'FAILED',
                                         result.get('viewPath') or '-'))
    else:
        found = find_trace_explorer(project)
        if found:
            print('Trace Explorer webapp found: %(name)s (%(id)s)' % found)
        else:
            print('NO Trace Explorer webapp — ' + MANUAL_WEBAPP_STEP
                  % (args.project, summary['dataset']))


if __name__ == '__main__':
    main()
