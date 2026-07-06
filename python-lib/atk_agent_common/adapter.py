"""Glue between DSS agent-tool components and tools_impl.

Every tool.py is: set_config stores plugin_config → invoke calls
run_tool(impl_fn, plugin_config, **args). All ToolkitErrors become structured
{"error": ...} outputs (agents read them and act; they never see tracebacks).
"""

import logging

from . import config as config_mod
from .client import ToolkitClient
from .errors import ToolkitError

logger = logging.getLogger('atk-agents')


def build_client(plugin_config):
    return ToolkitClient(config_mod.resolve(plugin_config))


def run_tool(fn, plugin_config, **kwargs):
    """Call a tools_impl function, mapping every failure to an error payload."""
    try:
        client = build_client(plugin_config)
        return {'output': fn(client, **kwargs)}
    except ToolkitError as exc:
        logger.warning('tool %s → %s: %s', getattr(fn, '__name__', '?'), exc.code, exc.message)
        return {'output': exc.to_output()}
    except Exception as exc:  # last resort: still no traceback in the output
        logger.exception('tool %s failed unexpectedly', getattr(fn, '__name__', '?'))
        return {'output': {'error': {'code': 'internal-error',
                                     'message': '%s: %s' % (type(exc).__name__, str(exc)[:200])}}}


HOST_PROPERTY = {
    'type': 'string',
    'description': "Target DSS host id (default 'local'). Use the list-hosts tool for valid ids.",
    'default': 'local',
}
