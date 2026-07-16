"""Read-registry integrity: the toolkit_get bridge dispatches only registered
endpoints, whitelists params, windows lists, and the log_tail sensor clamps
its inputs. The route-existence / page-map halves of the contract live in
scripts/check_agent_read_coverage.mjs.
"""

import conftest  # noqa: F401  (installs the python-lib path + DSS stubs)

from atk_agent_common import read_registry, tools_impl


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, path, host='local', params=None, heavy=False, progress_path=None):
        self.calls.append({'path': path, 'host': host, 'params': params,
                           'heavy': heavy, 'progress_path': progress_path})
        return self.payload


def test_description_names_every_endpoint():
    desc = tools_impl.SENSOR_DESCRIPTIONS['toolkit_get']
    for row in read_registry.ENDPOINTS:
        assert row['name'] in desc, row['name']


def test_list_mode_returns_manifest():
    out = tools_impl.toolkit_get(None, endpoint='list')
    names = {e['name'] for e in out['endpoints']}
    assert names == {row['name'] for row in read_registry.ENDPOINTS}


def test_unknown_endpoint_rejected():
    out = tools_impl.toolkit_get(None, endpoint='nope')
    assert out['error']['code'] == 'bad-input'


def test_param_whitelist_enforced():
    client = _FakeClient({'ok': True})
    out = tools_impl.toolkit_get(client, endpoint='users-churn', params={'evil': '1'})
    assert out['error']['code'] == 'bad-input'
    assert not client.calls  # rejected before any fetch


def test_local_only_endpoint_rejects_remote_host():
    out = tools_impl.toolkit_get(None, endpoint='audit-timeline', host='remote-1')
    assert out['error']['code'] == 'bad-input'


def test_fetch_uses_registry_row_and_pages_lists():
    rows = [{'i': i} for i in range(40)]
    client = _FakeClient({'items': rows, 'total': 40})
    out = tools_impl.toolkit_get(client, endpoint='users-churn', top_n=10, page=2)
    call = client.calls[0]
    assert call['path'] == '/api/users/churn'
    assert out['items'] == rows[10:20]
    assert out['itemsCount'] == 40
    assert out['page'] == 2
    assert out['total'] == 40


def test_fields_projection_keeps_counts():
    client = _FakeClient({'items': [1, 2, 3], 'other': 'x'})
    out = tools_impl.toolkit_get(client, endpoint='users-churn', fields=['items'], top_n=2)
    assert 'other' not in out
    assert out['items'] == [1, 2]
    assert out['itemsCount'] == 3


def test_log_tail_clamps_and_greps():
    text = '\n'.join('line %d ERROR' % i if i % 2 else 'line %d ok' % i
                     for i in range(50))
    client = _FakeClient({'text': text, 'chars': len(text)})
    out = tools_impl.log_tail(client, lines=5)
    assert len(out['lines']) == 5
    grep = tools_impl.log_tail(client, pattern='ERROR', lines=3)
    assert grep['grep']['matchCount'] == 25
    assert len(grep['grep']['lines']) == 3
    assert tools_impl.log_tail(client, log='frontend.log')['error']['code'] == 'bad-input'
    assert tools_impl.log_tail(client, pattern='[')['error']['code'] == 'bad-input'


def test_raw_tail_reads_json_text_field():
    """/api/logs/raw-tail is JSON ({text, chars}); the raw path must read the
    text field, not the serialized JSON (log_errors and log_tail share this)."""
    client = _FakeClient({'text': 'a\nb\nc', 'chars': 5})
    assert tools_impl._raw_tail_text(client, 'local') == 'a\nb\nc'
