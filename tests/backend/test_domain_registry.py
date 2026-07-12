"""Domain-registry integrity: every registered domain is fully wired — a real
tools_impl handler, a fix path (actions or waiver), no ParsedData field
claimed twice — and the generated config_inspect surface (description, list
manifest, dispatch) derives from the registry. The frontend half of the
contract (every ParsedData field / module accounted for) lives in
scripts/check_agent_domain_coverage.mjs.
"""

import conftest  # noqa: F401  (installs the python-lib path + DSS stubs)

from atk_agent_common import actions, domain_registry, tools_impl


def test_every_domain_has_a_handler():
    for row in domain_registry.DOMAINS:
        assert row['name'] in tools_impl._DOMAIN_HANDLERS
        assert callable(tools_impl._DOMAIN_HANDLERS[row['name']])


def test_fix_actions_are_catalogued():
    catalog = set(actions.REQUIRED_TARGET_KEYS)
    for row in domain_registry.DOMAINS:
        assert set(row['fix_actions']) <= catalog, row['name']
        assert row['fix_actions'] or row['fix_waiver'], row['name']
    for field, entry in domain_registry.PARSED_FIELD_COVERAGE.items():
        assert set(entry['fix_actions']) <= catalog, field
        assert entry['fix_actions'] or entry['fix_waiver'], field


def test_description_names_every_domain():
    desc = tools_impl.SENSOR_DESCRIPTIONS['config_inspect']
    for row in domain_registry.DOMAINS:
        assert row['name'] in desc, row['name']


def test_list_mode_returns_manifest():
    out = tools_impl.config_inspect(None, domain='list')
    names = {d['name'] for d in out['domains']}
    assert names == set(tools_impl._DOMAIN_HANDLERS)
    for d in out['domains']:
        assert d['summary'] and isinstance(d['fixActions'], list)


def test_unknown_domain_points_to_list():
    out = tools_impl.config_inspect(None, domain='nope')
    assert out['error']['code'] == 'bad-input'
    assert "'list'" in out['error']['message']


def test_fields_projection():
    class _FakeClient:
        def get(self, path, host=None, params=None, **kw):
            return {'ok': True, 'projects': [{'projectKey': 'A', 'name': 'A'}]}

    out = tools_impl.config_inspect(_FakeClient(), domain='projects',
                                    fields=['projectCount'])
    assert 'projectCount' in out and 'projects' not in out
    assert out['domain'] == 'projects'


def test_project_scoped_page_slices():
    rows = [{'id': 'j%d' % i} for i in range(50)]

    class _FakeClient:
        def get(self, path, host=None, params=None, **kw):
            return {'ok': True, 'jobs': rows}

    first = tools_impl.config_inspect(_FakeClient(), domain='jobs',
                                      name_filter='PROJ', top_n=10)
    second = tools_impl.config_inspect(_FakeClient(), domain='jobs',
                                       name_filter='PROJ', top_n=10, page=2)
    assert first['jobs'] == rows[:20]
    assert second['jobs'] == rows[20:40] and second['page'] == 2
