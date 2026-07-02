"""Minimal SSE consumption over requests (no extra dependency).

Used only as a fallback for endpoints whose blocking variant can outlive the
proxy: we read `event:`/`data:` frames from iter_lines and hand back the final
payload (last `done` event, else last event seen). Load balancers may buffer
SSE — callers must always have a non-stream fallback.
"""

import json


def read_final_event(response, done_events=('done',)):
    """Consume a streaming `requests` response as SSE; return (event, payload)
    of the last done-event (or the last event at all if none matched)."""
    event = None
    data_lines = []
    last = (None, None)
    for raw in response.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        line = raw.strip('\r')
        if line == '':
            if data_lines:
                try:
                    payload = json.loads('\n'.join(data_lines))
                except ValueError:
                    payload = {'raw': '\n'.join(data_lines)}
                last = (event or 'message', payload)
                if (event or 'message') in done_events:
                    return last
            event, data_lines = None, []
            continue
        if line.startswith('event:'):
            event = line[len('event:'):].strip()
        elif line.startswith('data:'):
            data_lines.append(line[len('data:'):].strip())
    return last
