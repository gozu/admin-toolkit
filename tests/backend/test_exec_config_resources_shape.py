"""Exec-config K8s resources live NESTED at
containerSettings.executionConfigs[].kubernetesRuntimeConfig.kubernetesResources
(shape verified against a live DSS general-settings payload). Regression for the
false 'exec-config-resources-group' finding on configs that DID have limits set.
"""

from atk_agent_common import health


def _cfg(name, resources):
    krc = {'kubernetesNamespace': 'default'}
    if resources is not None:
        krc['kubernetesResources'] = resources
    return {'name': name, 'type': 'KUBERNETES', 'kubernetesRuntimeConfig': krc}


RAW = {'containerSettings': {'executionConfigs': [
    _cfg('eks-default', {'memRequestMB': 2048, 'memLimitMB': 4096,
                         'cpuRequest': 0.5, 'cpuLimit': 2.0,
                         'customLimits': [], 'customRequests': []}),
    _cfg('eks-gpu', {'memRequestMB': 4096, 'memLimitMB': 8192,
                     'cpuRequest': 1.0, 'cpuLimit': 4.0,
                     'customLimits': [{'key': 'nvidia.com/gpu', 'value': '1'}]}),
]}}


def test_nested_resources_are_seen_and_score_100():
    configs = health._extract_exec_resource_configs(RAW)
    assert [c['memRequestMB'] for c in configs] == [2048, 4096]
    assert [c['cpuLimit'] for c in configs] == [2.0, 4.0]
    score, issues = health._score_exec_config_resources(configs, lambda rule, item: False)
    assert (score, issues) == (100, [])


def test_missing_kubernetes_resources_still_fires():
    raw = {'containerSettings': {'executionConfigs': [_cfg('bare', None)]}}
    configs = health._extract_exec_resource_configs(raw)
    score, issues = health._score_exec_config_resources(configs, lambda rule, item: False)
    assert score < 100
    assert issues[0]['id'] == 'exec-config-resources-group'
    assert issues[0]['whitelistItems'] == ['bare']
