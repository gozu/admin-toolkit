#!/usr/bin/env python3
"""Fresh Legacy/Headless inventory; same owned fixtures, actual backend MCP.

No historical rows are imported. Fixture-controller authorization is confined
to the comparison code, never used by the production reasoning loop.
"""
import argparse
import json
from pathlib import Path
import time
from types import SimpleNamespace

from cobuild_model_compare import ModelComparison, ROOT, READS
from cobuild_model_suite import inventory, PREREQUISITES

TRANSPORT = 'whole-task Legacy versus Dataiku Headless MCP v1'


class HeadlessConversation:
    def __init__(self, client):
        self.client, self.row = client, None

    def send_message(self, message, allow_edit_project=False):
        if allow_edit_project:
            raise ValueError('Native project writes are never allowed.')
        body = {'message': message}
        if self.row:
            body.update(taskId=self.row['taskId'], revision=self.row['revision'])
        self.row = self.client.post('/api/agents/headless/tasks', json=body)
        deadline = time.monotonic() + 315
        while self.row['status'] == 'pending':
            if time.monotonic() >= deadline:
                raise TimeoutError('Headless outcome unknown; no retry.')
            time.sleep(1)
            self.row = self.client.get('/api/agents/headless/tasks/' + self.row['taskId'])
        if self.row['status'] != 'completed':
            raise RuntimeError('Headless ' + self.row['status'])
        if self.row.get('transport') != 'dataiku-headless-mcp-in-process':
            raise RuntimeError('Comparison requires actual Headless MCP transport.')
        return SimpleNamespace(message=self.row['message'], is_error=False,
                               is_confirmation_request=False, is_question_request=False)

    def close(self):
        if self.row:
            try:
                self.client.post('/api/agents/headless/tasks/' + self.row['taskId'] + '/stop', json={})
            except Exception:
                pass


class HeadlessComparison(ModelComparison):
    providers = ('existing', 'headless')
    transport = TRANSPORT

    def save(self, row):
        row = dict(row, transport=TRANSPORT)
        for old, new in [('existing_seconds', 'legacy_seconds'), ('cobuild_seconds', 'headless_seconds'),
                         ('existing_model_calls', 'legacy_model_calls'), ('cobuild_model_calls', 'headless_model_calls'),
                         ('existing_tools', 'legacy_tools'), ('cobuild_tools', 'headless_tools')]:
            if old in row:
                row[new] = row.pop(old)
        row['status'] = {'same': 'passed', 'needs_review': 'failed', 'skipped': 'excluded'}.get(row['status'], row['status'])
        super().save(row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--read', action='append', choices=sorted(READS))
    parser.add_argument('--inventory-only', action='store_true')
    args = parser.parse_args()
    if args.inventory_only:
        rows = [{'capability': name, 'group': group, 'transport': TRANSPORT,
                 'status': 'excluded' if group == 'excluded' else 'blocked',
                 'reason': PREREQUISITES.get(group, 'Fresh Headless run not yet performed.')}
                for name, group in inventory().items()]
        args.output.write_text(json.dumps(rows, indent=2) + '\n')
        return
    run = HeadlessComparison(ROOT / '.dss-url', ROOT / '.dss-api-key', args.output)
    for name in args.read or READS:
        with run.gates([name]):
            run.read(name, READS[name])


if __name__ == '__main__':
    main()
