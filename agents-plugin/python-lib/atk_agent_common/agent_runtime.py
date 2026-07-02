"""Shared agent loop for the plugin agents (health-triage / scoping-architect /
ops-actuator): a hand-rolled LangChain tool-calling loop over DKUChatModel.

Hand-rolled rather than AgentExecutor so the loop is deterministic and
version-stable: bind tools → model → execute tool_calls → ToolMessages →
repeat (max_iterations) → stream the final text. Written for frontier
tool-calling models (parallel tool calls supported); no downgrade paths.
"""

import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

MAX_ITERATIONS = 12


def build_llm(llm_id):
    from dataiku.langchain.dku_llm import DKUChatModel
    return DKUChatModel(llm_id=llm_id)


def messages_from_query(query, system_prompt):
    """DSS completion query → LangChain messages, system prompt first."""
    out = [SystemMessage(content=system_prompt)]
    for msg in (query or {}).get('messages', []):
        role = msg.get('role')
        content = msg.get('content') or ''
        if role == 'user':
            out.append(HumanMessage(content=content))
        elif role == 'assistant':
            out.append(AIMessage(content=content))
        elif role == 'system':
            out.append(SystemMessage(content=content))
    return out


async def run_tool_loop(llm, tools, messages, trace=None):
    """Async generator: yields DSS LLM chunks; tool calls surfaced as events."""
    tool_map = {t.name: t for t in tools}
    llm_with_tools = llm.bind_tools(tools) if tools else llm

    for _ in range(MAX_ITERATIONS):
        response = await llm_with_tools.ainvoke(messages)
        tool_calls = getattr(response, 'tool_calls', None) or []
        if not tool_calls:
            text = response.content if isinstance(response.content, str) else str(response.content)
            if text:
                yield {'chunk': {'text': text}}
            return
        messages.append(response)
        for call in tool_calls:
            name = call.get('name')
            args = call.get('args') or {}
            yield {'chunk': {'type': 'event', 'eventKind': 'tool_call',
                             'eventData': {'name': name, 'args': args}}}
            tool = tool_map.get(name)
            if tool is None:
                result = json.dumps({'error': {'code': 'unknown-tool', 'message': 'No tool named %r' % name}})
            else:
                try:
                    result = tool.func(**args)
                except Exception as exc:
                    result = json.dumps({'error': {'code': 'tool-crash',
                                                   'message': '%s: %s' % (type(exc).__name__, str(exc)[:200])}})
            messages.append(ToolMessage(content=result, tool_call_id=call.get('id') or name))

    yield {'chunk': {'text': '\n\n[stopped: tool-call iteration limit reached — '
                             'narrow the request or ask me to continue]'}}
