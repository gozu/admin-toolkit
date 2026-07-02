#!/usr/bin/env python3
"""Ensure the 'Agents — Daily health triage' scenario exists on the dev DSS
(reads .dss-url/.dss-api-key). Idempotent — safe to re-run.

    .venv/bin/python scripts/agents/provision_triage.py [--hour 7]
"""

import argparse
import json
import pathlib
import sys

import dataikuapi

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'agents-plugin' / 'python-lib'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--hour', type=int, default=7)
    args = ap.parse_args()

    from atk_agent_common.triage import provision

    url = (REPO / '.dss-url').read_text().strip()
    key = (REPO / '.dss-api-key').read_text().strip()
    client = dataikuapi.DSSClient(url, key)

    raw = client.get_plugin('admin-toolkit-agents').get_settings().get_raw()
    plugin_config = (raw or {}).get('config') or {}
    from atk_agent_common import config as config_mod
    settings = config_mod.resolve(plugin_config)

    result = provision.provision_all(client, settings, hour=args.hour)
    print(json.dumps(result, indent=1, default=str))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
