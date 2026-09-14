#!/usr/bin/env python3
"""Live Existing/Cobuild comparisons through the deployed HTTP bridge.

Run with the repository venv. Credentials remain in files/in memory. Only
sanitized verdicts are written; model responses and customer diagnostics are
not report artifacts. Action fixtures must supply reset, verify and cleanup.
An accepted model request is never counted as a successful admin action.
"""
import argparse
import contextlib
import copy
import datetime
import json
import pathlib
import sys
import time
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'python-lib'))

from atk_agent_common import action_gates, actuator, capability_routing as routing, config, tools_impl
from atk_agent_common.client import ToolkitClient


def normalize(value):
    """Ignore transport metadata, not errors or domain measurements."""
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()
                if k not in {'executionRoute', 'confirm_token', 'auditId',
                             'fetchedAt', 'generatedAt', 'timestamp',
                             'elapsedMs', 'durationMs', 'timestampMs'}}
    if isinstance(value, (list, tuple)):
        return [normalize(v) for v in value]
    return value


def failed(value):
    if not isinstance(value, dict):
        return False
    return (bool(value.get('error')) or value.get('ok') is False
            or value.get('success') is False or bool(value.get('needsPassword'))
            or value.get('status') in ('error', 'failed', 'partial'))


class Comparison:
    def __init__(self, url_file, key_file, output):
        import dataikuapi
        self.dss = dataikuapi.DSSClient(pathlib.Path(url_file).read_text().strip(),
                                       pathlib.Path(key_file).read_text().strip())
        raw = self.dss.get_plugin('admin-toolkit').get_settings().get_raw().get('config', {})
        self.tk = ToolkitClient(config.resolve(raw))
        parts = urlsplit(self.tk.base_url).path.strip('/').split('/')
        i = parts.index('web-apps-backends')
        backend = self.dss.get_project(parts[i + 1]).get_webapp(parts[i + 2]).get_backend_client()
        self.tk.base_url = backend.base_url.rstrip('/')
        self.tk.session.auth = backend.session.auth
        self.output = pathlib.Path(output)
        self.rows = []

    def save(self, row):
        self.rows.append(dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), **row))
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(self.rows, indent=2) + '\n')
        self.output.chmod(0o600)
        print(json.dumps(row), flush=True)

    @contextlib.contextmanager
    def gates(self, names):
        """Restore only touched gates; retain unrelated concurrent selections."""
        settings = self.tk.get('/api/agents/action-settings')
        original = {n: settings['gates'].get(n, n in tools_impl.SENSOR_DESCRIPTIONS) for n in names}
        original_auto = {n: settings['autonomous'].get(n, n in tools_impl.SENSOR_DESCRIPTIONS) for n in names}
        restore = {'gates': original, 'autonomous': original_auto}
        recovery = self.output.with_name(self.output.stem + '-restore-gates.json')
        recovery.parent.mkdir(parents=True, exist_ok=True)
        recovery.write_text(json.dumps(restore))
        recovery.chmod(0o600)
        try:
            self.tk.post('/api/agents/action-settings/update', red=True,
                         json={'gates': {n: True for n in names},
                               'autonomous': {n: False for n in names}})
            action_gates._cache['gates'] = None
            yield
        finally:
            for attempt in range(3):
                try:
                    self.tk.post('/api/agents/action-settings/update', red=True, json=restore)
                    recovery.unlink(missing_ok=True)
                    action_gates._cache['gates'] = None
                    break
                except Exception:
                    if attempt == 2:
                        self.save({'capability': '_gates', 'status': 'restore_pending',
                                   'recovery_file': recovery.name})
                        raise
                    time.sleep(3)

    def read(self, name, arguments):
        fn = getattr(tools_impl, name)
        t = time.monotonic()
        try:
            with routing.override('existing'):
                baseline = fn(self.tk, **arguments)
            duration = round(time.monotonic() - t, 3)
        except Exception as exc:
            self.save({'capability': name, 'status': 'blocked', 'phase': 'read',
                       'reason': 'Existing path raised ' + type(exc).__name__})
            return
        if failed(baseline):
            self.save({'capability': name, 'status': 'blocked', 'phase': 'read',
                       'reason': 'Existing path returned an error; identical errors are not parity.'})
            return
        for attempt in range(1, 4):
            t = time.monotonic()
            try:
                with routing.override('cobuild'):
                    candidate = fn(self.tk, **arguments)
                route = candidate.get('executionRoute', {})
                same = not failed(candidate) and normalize(baseline) == normalize(candidate)
                same = same and route.get('resultAcknowledged') is True
                row = {'capability': name, 'phase': 'read', 'attempt': attempt,
                       'status': 'same' if same else 'needs_review',
                       'existing_seconds': duration, 'cobuild_seconds': round(time.monotonic() - t, 3),
                       'request_and_result_verified': route.get('resultAcknowledged') is True,
                       'conversation_id': route.get('conversationId'), 'transport': 'deployed HTTP bridge'}
            except Exception as exc:
                row = {'capability': name, 'phase': 'read', 'attempt': attempt,
                       'status': 'failed', 'reason': type(exc).__name__}
            self.save(row)
            if row['status'] == 'same':
                break

    def action(self, name, target, reset, verify, scope):
        """Reset the same owned fixture for each path; verify with independent SDK reads.

        No blind execution retry: reset must inspect/recreate the known fixture.
        Three candidate attempts are the maximum, never three repeats of an
        action whose effects have not first been reconciled.
        """
        baseline = None
        baseline_seconds = None
        for provider, attempt in [('existing', 0)] + [('cobuild', i) for i in range(1, 4)]:
            phase = 'fixture'
            try:
                reset()
                t = time.monotonic()
                with routing.override(provider):
                    phase = 'plan'
                    plan = actuator.plan_admin_action(self.tk, action=name, target=target() if callable(target) else target)
                    if failed(plan) or not plan.get('confirm_token'):
                        raise RuntimeError('plan-refused')
                    phase = 'execute'
                    result = actuator.execute_admin_action(
                        self.tk, action=name, target=plan['canonicalTarget'],
                        confirm_flag=True, confirm_token=plan['confirm_token'],
                        agent_name='atk-cobuild-comparison')
                elapsed = round(time.monotonic() - t, 3)
                phase = 'postcondition'
                evidence = verify(result)
                if failed(result) or failed(result.get('result', {})) or not evidence:
                    raise RuntimeError('postcondition-not-met')
                if provider == 'existing':
                    baseline, baseline_seconds = normalize(evidence), elapsed
                    continue
                route = result.get('executionRoute', {})
                ack = route.get('resultAcknowledged') is True and plan.get('executionRoute', {}).get('resultAcknowledged') is True
                same = normalize(evidence) == baseline and ack
                row = {'capability': name, 'phase': 'execute', 'attempt': attempt,
                       'status': 'same' if same else 'needs_review', 'scope': scope,
                       'existing_seconds': baseline_seconds, 'cobuild_seconds': elapsed,
                       'postcondition': evidence, 'request_and_result_verified': ack,
                       'conversation_id': route.get('conversationId'), 'transport': 'deployed HTTP bridge'}
            except Exception as exc:
                details = self.output.with_name(self.output.stem + '-last-error.txt')
                details.write_text(type(exc).__name__ + ': ' + str(exc))
                details.chmod(0o600)
                row = {'capability': name, 'phase': phase, 'attempt': attempt,
                       'status': 'blocked' if provider == 'existing' else 'failed',
                       'scope': scope, 'reason': type(exc).__name__ + ': ' + str(exc)[:350]}
                # SDK errors may contain URLs/configuration. Keep details local
                # to the operator; public reports use the exception class only.
                row['reason'] = type(exc).__name__ + ' during ' + phase
            self.save(row)
            if provider == 'existing' or row['status'] == 'same':
                break


READS = {
    'list_hosts': {'probe': False},
    'instance_health': {'sections': ['system', 'issues'], 'top_n': 3, 'include_score': False},
    'compute_cost': {'top_n': 3},
    'config_inspect': {'domain': 'projects', 'name_filter': 'ATKCOBUILDTEST'},
    'log_errors': {'top_n': 3},
    'log_tail': {'lines': 5, 'pattern': 'ATK_COMPARISON_NO_SUCH_LINE'},
    'storage_footprint': {'top_n': 3},
    'k8s_health': {'top_n': 3},
    'db_health': {'top_n': 3},
    'toolkit_get': {'endpoint': 'version'},
    'list_capabilities': {},
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url-file', default=str(ROOT / '.dss-url'))
    parser.add_argument('--key-file', default=str(ROOT / '.dss-api-key'))
    parser.add_argument('--output', required=True)
    parser.add_argument('--read', action='append', choices=sorted(READS))
    args = parser.parse_args()
    run = Comparison(args.url_file, args.key_file, args.output)
    for name in args.read or READS:
        run.read(name, READS[name])


if __name__ == '__main__':
    main()
