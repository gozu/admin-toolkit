"""K8S Apply macro — the gated kubectl mutation engine.

A deliberately SEPARATE macro from k8s-insights (which stays provably
read-only): different blast radius, different label, different timeout.
Every command is re-validated by atk_agent_common.policies.kubectl_policy
inside this macro immediately before running — the plan-side validation is
UX only. Policy refusals return {'ok': False, 'refused': [...]}, never raise.

`apply` manifests arrive via manifest_yaml, are policy-validated per document,
and written to a tempfile whose path replaces the {manifest} placeholder —
`cluster.run_kubectl` has no stdin, which is fine because this macro and the
DSS host share a filesystem (LOCAL-only v1).
"""
import json
import os
import shlex
import tempfile

from dataiku.runnables import Runnable

from atk_agent_common.kubectl_runner import make_kubectl_runner
from atk_agent_common.policies import kubectl_policy

_OUTPUT_CAP = 4000  # head-truncate kubectl output per command (macro JSON size)


def _bool(value, default=False):
    if value is None or value == '':
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('true', '1', 'yes', 'y')


def _validate_all(commands, manifest_yaml):
    """Validate every command + the manifest. Returns (parsed_cmds, refused,
    manifest_docs). parsed_cmds entries: {'args', 'parsed'}."""
    refused = []
    parsed_cmds = []
    needs_manifest = False
    for i, args in enumerate(commands):
        ok, reason, parsed = kubectl_policy.validate(args)
        if not ok:
            refused.append({'command': str(args)[:300], 'reason': reason})
            continue
        if parsed['verb'] == 'apply':
            needs_manifest = True
        parsed_cmds.append({'args': args, 'parsed': parsed})
    manifest_docs = []
    if needs_manifest:
        ok, reason, manifest_docs = kubectl_policy.validate_manifest(manifest_yaml)
        if not ok:
            refused.append({'command': 'apply -f {manifest}', 'reason': reason})
    return parsed_cmds, refused, manifest_docs


def _substitute_manifest(argv, manifest_path):
    return [manifest_path if tok == kubectl_policy.MANIFEST_PLACEHOLDER else tok for tok in argv]


def _join(argv):
    return ' '.join(shlex.quote(tok) for tok in argv)


def _run_one(kubectl, argv):
    rc, out, err = kubectl(_join(argv))
    return {'command': _join(argv), 'rc': rc, 'ok': rc == 0,
            'stdout': (out or '')[:_OUTPUT_CAP], 'stderr': (err or '')[:_OUTPUT_CAP]}


def _preview_one(kubectl, entry, manifest_path):
    """Read-only twins for one validated command: current object JSON (capped)
    + a server dry-run / diff of the mutation itself."""
    parsed = entry['parsed']
    argv = parsed['argv']
    result = {'command': entry['args'], 'verb': parsed['verb'], 'kind': parsed['kind'],
              'name': parsed['name'], 'namespace': parsed['namespace']}
    if parsed['verb'] == 'apply':
        diff = _run_one(kubectl, _substitute_manifest(['diff', '-f', kubectl_policy.MANIFEST_PLACEHOLDER,
                                                       '-n', parsed['namespace']], manifest_path))
        # kubectl diff exits 1 when differences exist — that's the happy path.
        diff['ok'] = diff['rc'] in (0, 1)
        result['diff'] = diff
        result['serverDryRun'] = _run_one(
            kubectl, _substitute_manifest(argv, manifest_path) + ['--dry-run=server'])
        return result
    get = _run_one(kubectl, ['get', parsed['kind'], parsed['name'],
                             '-n', parsed['namespace'], '-o', 'json'])
    result['current'] = get
    result['serverDryRun'] = _run_one(kubectl, list(argv) + ['--dry-run=server'])
    return result


class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        operation = str(self.config.get('operation') or '').strip().lower()
        cluster_id = str(self.config.get('cluster_id') or '').strip()
        manifest_yaml = self.config.get('manifest_yaml') or ''
        dry_run = _bool(self.config.get('dry_run'), default=True)
        try:
            commands = json.loads(self.config.get('commands_json') or '[]')
        except json.JSONDecodeError as exc:
            return json.dumps({'ok': False, 'error': 'commands_json is not valid JSON: %s' % exc})
        if not isinstance(commands, list) or not commands:
            return json.dumps({'ok': False, 'error': 'commands_json must be a non-empty JSON array'})
        if not cluster_id:
            return json.dumps({'ok': False, 'error': 'cluster_id is required'})
        if operation not in ('preview', 'execute'):
            return json.dumps({'ok': False, 'error': 'Unknown operation: %s' % operation})
        if dry_run:
            operation = 'preview'

        try:
            parsed_cmds, refused, manifest_docs = _validate_all(commands, manifest_yaml)
            if refused:
                return json.dumps({'ok': False, 'refused': refused,
                                   'note': 'Policy enforced inside the macro — nothing ran.'})
            try:
                kubectl = make_kubectl_runner(cluster_id)
            except Exception as exc:
                return json.dumps({'ok': False,
                                   'error': 'cannot reach cluster %r via DSS API: %s: %s'
                                            % (cluster_id, type(exc).__name__, str(exc)[:300])})

            manifest_path = None
            try:
                if manifest_docs:
                    fd, manifest_path = tempfile.mkstemp(prefix='atk-k8s-apply-', suffix='.yaml')
                    with os.fdopen(fd, 'w') as fh:
                        fh.write(manifest_yaml)

                results = []
                if operation == 'preview':
                    for entry in parsed_cmds:
                        results.append(_preview_one(kubectl, entry, manifest_path))
                    return json.dumps({'ok': True, 'operation': 'preview',
                                       'manifestDocs': manifest_docs, 'results': results})

                # execute: in order, stop at first failure
                for entry in parsed_cmds:
                    argv = entry['parsed']['argv']
                    if manifest_path:
                        argv = _substitute_manifest(argv, manifest_path)
                    res = _run_one(kubectl, argv)
                    results.append(res)
                    if not res['ok']:
                        return json.dumps({'ok': False, 'operation': 'execute',
                                           'error': 'command %d failed — execution stopped'
                                                    % (len(results) - 1),
                                           'manifestDocs': manifest_docs, 'results': results})
                return json.dumps({'ok': True, 'operation': 'execute',
                                   'manifestDocs': manifest_docs, 'results': results})
            finally:
                if manifest_path:
                    try:
                        os.unlink(manifest_path)
                    except OSError:
                        pass
        except Exception as exc:
            return json.dumps({'ok': False, 'error': '%s: %s' % (type(exc).__name__, str(exc))})
