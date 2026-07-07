"""config_inspect domain='projects': resolves a project label to its KEY, the
grounding the per-project domains (scenarios/webapps/notebooks/jobs/datasets)
need. name_filter matches projectKey OR name (lowercased substring), same style
as the other config domains.
"""

import conftest  # noqa: F401  (installs the python-lib path + DSS stubs)

from atk_agent_common import tools_impl


class _FakeClient:
    """Canned inventory GET; records the params it was called with."""

    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def get(self, path, host=None, params=None, **kw):
        self.calls.append((path, params))
        return {'ok': True, 'projects': list(self._rows)}


_ROWS = [{'projectKey': 'MARKETING', 'name': 'Marketing Ops', 'owner': 'alice'},
         {'projectKey': 'FINANCE', 'name': 'Finance', 'owner': 'bob'},
         {'projectKey': 'DIAGPARSE', 'name': 'Diag Parser', 'owner': 'carol'}]


def test_projects_domain_hits_inventory_and_shapes():
    c = _FakeClient(_ROWS)
    out = tools_impl.config_inspect(c, domain='projects')
    assert c.calls == [('/api/tools/admin-actions/inventory', {'domain': 'projects'})]
    assert out['domain'] == 'projects'
    assert out['projectCount'] == 3
    assert out['projects'][0]['projectKey'] == 'MARKETING'


def test_projects_filter_matches_name_substring():
    c = _FakeClient(_ROWS)
    out = tools_impl.config_inspect(c, domain='projects', name_filter='diag parser')
    assert out['projectCount'] == 1
    assert out['projects'][0]['projectKey'] == 'DIAGPARSE'


def test_projects_filter_matches_key():
    c = _FakeClient(_ROWS)
    out = tools_impl.config_inspect(c, domain='projects', name_filter='finance')
    assert out['projectCount'] == 1
    assert out['projects'][0]['projectKey'] == 'FINANCE'


def test_project_scoped_bad_input_points_to_projects_domain():
    c = _FakeClient(_ROWS)
    out = tools_impl.config_inspect(c, domain='datasets')  # no name_filter
    assert out['error']['code'] == 'bad-input'
    assert "domain='projects'" in out['error']['message']
