"""LangChain tool wrappers over tools_impl for the plugin agents.

The agents call the SAME pure functions the standalone agent-tool components
call — one implementation, two surfaces. Each wrapper serializes its result to
JSON text (LangChain ToolMessage content), letting tools_impl's shaping/budget
do the token control.
"""

import inspect
import json

from langchain_core.tools import StructuredTool

from . import action_gates, tools_impl
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
    """StructuredTools for the given tool names (default: all sensors).

    Sensors an admin unchecked in Agent Settings are dropped here — the
    agent never sees a disabled tool (default: all sensors enabled)."""
    specs = tools_impl.SENSOR_DESCRIPTIONS
    wanted = names or list(specs)
    return [StructuredTool.from_function(_wrap(getattr(tools_impl, n), client),
                                         name=n, description=specs[n])
            for n in wanted
            if n in specs and action_gates.sensor_enabled(client, n)]
