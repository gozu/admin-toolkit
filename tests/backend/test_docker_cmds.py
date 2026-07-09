"""Unit tests for the fixed-argv docker command policy."""

import pytest

from atk_agent_common.policies import docker_cmds


def test_df_exact_argv():
    assert docker_cmds.build_command('df') == \
        ['docker', 'system', 'df', '--format', '{{json .}}']


def test_info_exact_argv():
    assert docker_cmds.build_command('info') == \
        ['docker', 'info', '--format', '{{json .}}']


def test_builder_prune_exact_argv():
    assert docker_cmds.build_command('builder-prune', keep_storage_gb=20) == \
        ['docker', 'builder', 'prune', '--force', '--keep-storage=20GB']


def test_image_prune_without_filter():
    assert docker_cmds.build_command('image-prune') == \
        ['docker', 'image', 'prune', '--force']


def test_image_prune_with_filter():
    assert docker_cmds.build_command('image-prune', filter_until_hours=72) == \
        ['docker', 'image', 'prune', '--force', '--filter', 'until=72h']


@pytest.mark.parametrize('op', [
    'system-prune', 'volume-prune', 'network-prune', 'rm', 'rmi', 'exec',
    'run', '', None, 'df; rm -rf /', 'builder prune --all',
])
def test_unknown_ops_raise(op):
    with pytest.raises(ValueError):
        docker_cmds.build_command(op)


@pytest.mark.parametrize('bad', ['20; rm -rf /', '-1', 0, 'GB', None, 1e9])
def test_injection_and_bad_ints_raise(bad):
    with pytest.raises(ValueError):
        docker_cmds.build_command('builder-prune', keep_storage_gb=bad)


def test_image_prune_bad_filter_raises():
    with pytest.raises(ValueError):
        docker_cmds.build_command('image-prune', filter_until_hours='24h --all')


def test_no_all_flag_anywhere():
    for op, kw in (('df', {}), ('info', {}),
                   ('builder-prune', {'keep_storage_gb': 5}),
                   ('image-prune', {'filter_until_hours': 24})):
        argv = docker_cmds.build_command(op, **kw)
        assert '--all' not in argv and '-a' not in argv


def test_daemon_script_contains_validated_values():
    script = docker_cmds.daemon_json_script(keep_storage_gb=15)
    assert '15GB' in script
    assert 'systemctl restart docker' in script
    assert 'cp -a' in script  # backup first
    assert 'os.replace' in script  # atomic write


def test_daemon_script_rejects_bad_ints():
    with pytest.raises(ValueError):
        docker_cmds.daemon_json_script(keep_storage_gb='20; reboot')
