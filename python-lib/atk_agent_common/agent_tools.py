"""LangChain tool wrappers over tools_impl for the plugin agents.

The agents call the SAME pure functions the standalone agent-tool components
call — one implementation, two surfaces. Each wrapper serializes its result to
JSON text (LangChain ToolMessage content), letting tools_impl's shaping/budget
do the token control.
"""

import inspect
import json

from langchain_core.tools import StructuredTool

from . import tools_impl
from .errors import ToolkitError


def _wrap(fn, client):
    def run(**kwargs):
        try:
            return json.dumps(fn(client, **kwargs), default=str)
        except ToolkitError as exc:
            return json.dumps(exc.to_output(), default=str)
        except Exception as exc:
            return json.dumps({'error': {'code': 'internal-error',
                                         'message': '%s: %s' % (type(exc).__name__, str(exc)[:200])}})
    # StructuredTool.from_function derives the args schema from the signature;
    # a bare **kwargs would yield an empty schema that rejects every argument.
    run.__signature__ = inspect.Signature(
        [p for name, p in inspect.signature(fn).parameters.items() if name != 'client'])
    return run


def build_langchain_tools(client, names=None):
    """StructuredTools for the given tool names (default: all sensors)."""
    specs = {
        'list_hosts': (tools_impl.list_hosts,
                       'List the DSS hosts this toolkit can reach (id, label, url). '
                       'probe=true also checks reachability. Call this before targeting a non-local host.'),
        'instance_health': (tools_impl.instance_health,
                            'Health snapshot of one DSS host (host, sections list of system/sanity/java/issues/score, '
                            'top_n, include_score). include_score=true adds the 0-100 UI health score but forces '
                            'heavy scans (may return scan_running — retry later).'),
        'compute_cost': (tools_impl.compute_cost,
                         'Compute + LLM cost from CRU audit records (host, group_by=project|user|context_type, '
                         'top_n). Span limited to audit retention — check the span field.'),
        'config_inspect': (tools_impl.config_inspect,
                           'Inspect config domain (host, domain=projects|connections|code-envs|plugins|llms|'
                           'clusters|users|api-keys|scenarios|webapps|notebooks|jobs|datasets, '
                           'detail=health|usage, name_filter, top_n). domain=projects lists projectKey+name '
                           '(name_filter = label/substring) — use it to resolve a project label to its KEY. '
                           'For scenarios/webapps/notebooks/jobs/datasets, name_filter is the PROJECT KEY '
                           '(required). datasets rows carry exposed=true when shared; detail=usage adds '
                           'per-dataset flow lineage (producing/consuming recipes, webapp/scenario name-refs) '
                           "plus summary rollups 'unreferenced' and 'deleteCandidates' — the grounding for "
                           'dataset-delete cleanup.'),
        'log_errors': (tools_impl.log_errors,
                       'Backend.log error groups (host, top_n); pattern=<regex> greps the raw tail.'),
        'storage_footprint': (tools_impl.storage_footprint,
                              'Project storage totals, largest projects, inactive+large cleanup candidates '
                              '(host, top_n, min_size_gb). Heavy scan — may return scan_running.'),
        'k8s_health': (tools_impl.k8s_health,
                       'K8s clusters for a host: states + reachability sweep; cluster=<id> runs a deep audit.'),
        'db_health': (tools_impl.db_health,
                      'RuntimeDB PostgreSQL health (host, view=overview|tables|per-project, connection, top_n).'),
    }
    wanted = names or list(specs)
    return [StructuredTool.from_function(_wrap(specs[n][0], client), name=n, description=specs[n][1])
            for n in wanted if n in specs]
