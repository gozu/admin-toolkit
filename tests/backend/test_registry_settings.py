"""DSS 15 separates registry destinations from execution configurations."""
import conftest  # noqa: F401
import pytest

from atk_agent_common.registry_settings import registry_hint


def test_default_execution_config_resolves_its_own_build_config():
    settings = {'containerSettings': {
        'defaultExecutionConfig': 'production',
        'executionConfigs': [
            {'name': 'staging', 'imageBuildConfig': 'stage-build'},
            {'name': 'production', 'imageBuildConfig': 'prod-build'}],
        'buildConfigs': [
            {'name': 'stage-build', 'dockerBuilderConfig': {'pushConfigs': [
                {'repositoryURL': 'stage.azurecr.io'}]}},
            {'name': 'prod-build', 'dockerBuilderConfig': {'pushConfigs': [
                {'repositoryURL': '123.dkr.ecr.us-west-2.amazonaws.com'}]}}]}}
    assert registry_hint(settings) == {
        'provider': 'ecr', 'registryUrl': '123.dkr.ecr.us-west-2.amazonaws.com'}


def test_legacy_registry_still_takes_precedence_on_the_default_config():
    settings = {'containerSettings': {'defaultExecutionConfig': 'legacy',
        'executionConfigs': [{'name': 'legacy', 'repositoryURL': 'old.azurecr.io',
                              'imageBuildConfig': 'new'}],
        'buildConfigs': [{'name': 'new', 'dockerBuilderConfig': {'pushConfigs': [
            {'repositoryURL': 'gcr.io/project/image'}]}}]}}
    assert registry_hint(settings)['registryUrl'] == 'old.azurecr.io'


def test_missing_build_reference_does_not_select_an_unrelated_build():
    settings = {'containerSettings': {'executionConfigs': [{'imageBuildConfig': 'absent'}],
        'buildConfigs': [{'name': 'unused', 'dockerBuilderConfig': {'pushConfigs': [
            {'repositoryURL': 'other.azurecr.io'}]}}]}}
    assert registry_hint(settings) is None


def test_in_cluster_builder_push_destination():
    settings = {'containerSettings': {'executionConfigs': [{'imageBuildConfig': 'build'}],
        'buildConfigs': [{'name': 'build', 'dkuInClusterBuilderConfig': {'pushConfigs': [
            {'repositoryURL': 'us-central1-docker.pkg.dev/project/repository'}]}}]}}
    assert registry_hint(settings)['provider'] == 'gar'


@pytest.mark.parametrize('settings', [None, {}, {'containerSettings': 'bad'},
    {'containerSettings': {'executionConfigs': 'bad'}},
    {'containerSettings': {'executionConfigs': [{'imageBuildConfig': {}}]}},
    {'containerSettings': {'executionConfigs': [{'repositoryURL': 'host.azurecr.io.evil.test'}]}}])
def test_bad_or_unrecognized_settings_do_not_supply_a_registry(settings):
    assert registry_hint(settings) is None
