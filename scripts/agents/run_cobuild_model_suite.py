#!/usr/bin/env python3
"""Run model-replacement fixtures serially in a temporary selected-host macro.

Explicit --execute authorizes fixture creation. The owned macro plugin is
removed afterward, together with its own isolated Python environment.
Interrupted runs retain a private state file for operator reconciliation.
"""
import argparse
import io
import json
import os
import pathlib
import secrets
import time
import zipfile

from cobuild_compare import ROOT
from cobuild_model_suite import GROUPS, PREREQUISITES, inventory


def package(plugin_id):
    base = ROOT / 'scripts' / 'agents'
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('plugin.json', json.dumps({'id': plugin_id, 'version': '0.0.1',
            'meta': {'label': 'Disposable model comparison', 'author': 'Admin Toolkit',
                     'description': 'Owned benchmark fixtures; no production workloads',
                     'icon': 'icon-beaker', 'licenseInfo': 'Internal test'}}))
        sources = {'cobuild_compare.py', 'cobuild_model_compare.py', 'cobuild_model_suite.py',
                   'render_cobuild_comparison.py', 'headless_model_compare.py'}
        sources.update('cobuild_' + group + '_compare.py' for group in GROUPS
                       if group not in PREREQUISITES and group != 'reads')
        for name in sorted(sources):
            z.write(base / name, 'python-lib/' + name)
        z.write(base / 'cobuild_model_macro.py', 'python-runnables/checks/runnable.py')
        z.writestr('python-runnables/checks/runnable.json', json.dumps({
            'meta': {'label': 'Model replacement checks', 'icon': 'icon-beaker'},
            'impersonate': False, 'requiresGlobalAdmin': True, 'resultType': 'JSON_OBJECT',
            'params': [{'name': 'group', 'label': 'Fixture group', 'type': 'STRING'},
                       {'name': 'reasoning', 'label': 'Comparison transport', 'type': 'STRING'},
                       {'name': 'cases', 'label': 'Selected cases', 'type': 'STRING'}]}))
        z.writestr('code-env/python/desc.json', json.dumps({
            'acceptedPythonInterpreters': ['PYTHON311'], 'forceConda': False,
            'installCorePackages': True, 'corePackagesSet': 'PANDAS23',
            'installJupyterSupport': False}))
        z.writestr('code-env/python/spec/requirements.txt',
                   'requests\npsycopg2-binary\nlangchain\nlangchain-core\npyyaml\n')
    archive.seek(0)
    return archive


def private_write(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(json.dumps(value, indent=2) + '\n')


def delete_owned_plugin(dss, plugin_id):
    plugin = dss.get_plugin(plugin_id)
    environment = plugin.get_settings().get_raw().get('codeEnvName')
    if environment and not environment.startswith('plugin_' + plugin_id + '_'):
        raise RuntimeError('Refusing plugin deletion with a foreign code environment attached')
    plugin.delete().wait_for_result()
    if any(p['id'] == plugin_id for p in dss.list_plugins()):
        raise RuntimeError('Temporary plugin removal was not verified')
    if environment and any(e['envName'] == environment for e in dss.list_code_envs()):
        raise RuntimeError('Temporary environment removal was not verified')


def main():
    import dataikuapi
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--reasoning', choices=('cobuild', 'headless'), default='cobuild')
    parser.add_argument('--group', action='append', choices=tuple(GROUPS))
    parser.add_argument('--case', action='append', choices=tuple(inventory()))
    parser.add_argument('--output', required=True, type=pathlib.Path)
    args = parser.parse_args()
    if not args.execute:
        parser.error('--execute is required for live disposable fixtures')
    args.output.mkdir(parents=True, exist_ok=True)
    args.output.chmod(0o700)
    dss = dataikuapi.DSSClient((ROOT / '.dss-url').read_text().strip(),
                              (ROOT / '.dss-api-key').read_text().strip())
    plugin_id = 'atk-model-checks-' + secrets.token_hex(5)
    state_file = args.output / 'macro-state.json'
    state = {'plugin': plugin_id, 'group': None, 'run_id': None, 'completed': []}
    private_write(state_file, state)
    installed, active = False, False
    try:
        dss.install_plugin_from_archive(package(plugin_id))
        installed = True
        plugin = dss.get_plugin(plugin_id)
        env = plugin.create_code_env(python_interpreter='PYTHON311').wait_for_result()['envName']
        if not env.startswith('plugin_' + plugin_id + '_'):
            raise RuntimeError('DSS returned an unexpected environment owner')
        if not dss.get_code_env('PYTHON', env).get_settings().get_raw().get('actualPackageList'):
            raise RuntimeError('Temporary environment dependencies were not installed')
        settings = plugin.get_settings()
        settings.set_code_env(env)
        settings.save()
        macro = dss.get_project('ADMINTOOLKIT').get_macro('pyrunnable_' + plugin_id + '_checks')
        for group in args.group or GROUPS:
            selected = [n for n in GROUPS[group] if not args.case or n in args.case]
            if not selected:
                continue
            state.update(group=group, run_id=None)
            print('Starting fixture group: ' + group, flush=True)
            state['run_id'] = macro.run(params={'group': group, 'cases': ','.join(selected),
                                                'reasoning': args.reasoning}, wait=False)
            active = True
            private_write(state_file, state)
            while macro.get_status(state['run_id']).get('running'):
                time.sleep(10)
            result = macro.get_result(state['run_id'], as_type='json')
            active = False
            private_write(args.output / (group + '.json'), result)
            for row in result.get('rows', []):
                if row.get('capability') in GROUPS[group]:
                    print(json.dumps({k: row[k] for k in ('capability', 'status', 'latency_ratio', 'reason')
                                      if k in row}), flush=True)
            if result.get('error'):
                raise RuntimeError('Fixture group needs inspection: ' + result['error'])
            state['completed'].append(group)
            private_write(state_file, state)
    finally:
        # Never uninstall a macro while its in-flight write/cleanup is unknown.
        if installed and not active:
            delete_owned_plugin(dss, plugin_id)
            state['plugin_deleted'] = True
            private_write(state_file, state)
        elif installed:
            print('Macro outcome unknown; retained plugin and private recovery state: ' + str(state_file), flush=True)


if __name__ == '__main__':
    main()
