"""Small, explicit read pilot. Full results remain with ADTK, not in the prompt."""
import hashlib
import json


def supports(name, arguments):
    if name == 'list_hosts':
        return arguments.get('probe', False) is False
    if name == 'list_capabilities':
        return not arguments
    if name == 'toolkit_get':
        return (arguments.get('endpoint') == 'version'
                and not arguments.get('fields') and not arguments.get('params'))
    return False


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def evidence(name, result):
    if not isinstance(result, dict):
        raise ValueError('Expected an object read result.')
    # No free-text errors, URLs, arbitrary logs or configuration enter this
    # deliberately narrow projection. Raw errors are retained in the tool result.
    facts = {'hasError': bool(result.get('error'))}
    for key in ('partial', 'truncated', 'stale', 'backendStale'):
        if key in result:
            facts[key] = bool(result[key])
    if name == 'toolkit_get':
        for key in ('version', 'runningVersion'):
            value = result.get(key)
            facts[key] = value if isinstance(value, str) else None
    elif name == 'list_hosts':
        hosts = result.get('hosts', [])
        facts.update(count=result.get('count'), hostIds=[h.get('id') for h in hosts])
    elif name == 'list_capabilities':
        for key in ('sensors', 'actions'):
            rows = result.get(key, [])
            facts[key] = {'count': len(rows), 'enabled': sum(r.get('enabled') is True for r in rows)}
    else:
        raise ValueError('Unsupported read projection.')
    raw = canonical(facts)
    if len(raw) > 16000:
        raise ValueError('Read evidence exceeds the interpretation budget.')
    return facts, hashlib.sha256(raw.encode()).hexdigest()
