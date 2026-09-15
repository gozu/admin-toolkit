"""Bounded, deterministic reads eligible for one-request task execution."""
import math


def supports(name, arguments):
    if not isinstance(arguments, dict):
        return False
    if name == 'list_hosts':
        return set(arguments) <= {'probe'} and arguments.get('probe', False) is False
    if name == 'list_capabilities':
        return not arguments
    if name == 'toolkit_get':
        return (set(arguments) <= {'endpoint', 'host', 'params', 'fields', 'top_n', 'page'}
                and arguments.get('endpoint') == 'version'
                and not arguments.get('params') and not arguments.get('fields'))
    if name == 'config_inspect':
        return (set(arguments) <= {'host', 'domain', 'detail', 'name_filter', 'top_n', 'page', 'fields'}
                and arguments.get('domain') in {'connections', 'plugins', 'projects'}
                and not arguments.get('detail') and not arguments.get('fields')
                and not arguments.get('name_filter') and arguments.get('page', 1) == 1
                and _bounded_top(arguments))
    if name == 'instance_health':
        return (set(arguments) <= {'host', 'sections', 'top_n', 'include_score'}
                and arguments.get('sections') == ['system']
                and not arguments.get('include_score') and _bounded_top(arguments))
    if name == 'k8s_health':
        return (set(arguments) <= {'host', 'cluster', 'top_n'}
                and not arguments.get('cluster') and _bounded_top(arguments))
    return False


def _bounded_top(arguments):
    value = arguments.get('top_n', 10)
    return type(value) is int and 1 <= value <= 20


def facts(name, arguments, result):
    """No raw configuration, names, URLs, logs or error strings in model input."""
    from .read_interpretation import evidence
    if name in {'list_hosts', 'list_capabilities', 'toolkit_get'}:
        return evidence(name, result)[0]
    out = {'hasError': bool(result.get('error'))}
    for key in ('partial', 'truncated', 'stale'):
        if key in result:
            out[key] = bool(result[key])
    if name == 'config_inspect':
        out['domain'] = arguments['domain']
    for key in ('projectCount', 'pluginsCount', 'unavailableCount', 'totalDiscovered'):
        value = result.get(key)
        if type(value) in (int, float) and math.isfinite(value):
            out[key] = value
    for key in ('connections', 'plugins', 'projects', 'clusters', 'issues'):
        if isinstance(result.get(key), list):
            out[key + 'Returned'] = len(result[key])
    reachability = result.get('reachability')
    if isinstance(reachability, dict):
        out['reachableClusters'] = reachability.get('ok', 0)
        out['unreachableClustersReturned'] = len(reachability.get('failing') or [])
    if result.get('reachabilityError'):
        out['reachabilityUnavailable'] = True
    if name == 'instance_health':
        system = result.get('system') or {}
        out['cpuCores'] = system.get('cpu') if type(system.get('cpu')) is int else None
        out['filesystemRowsReturned'] = len(system.get('filesystems') or [])
    out['scope'] = 'Returned inventory counts only; not a security or full health assessment.'
    return out
