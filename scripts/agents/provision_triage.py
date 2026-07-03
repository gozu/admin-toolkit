#!/usr/bin/env python3
"""Ensure the 'Agents — Daily health triage' scenario exists. Idempotent —
safe to re-run. Defaults to the dev DSS (.dss-url/.dss-api-key); for prod,
pass an admin identity the same way as provision_prod.py:

    .venv/bin/python scripts/agents/provision_triage.py [--hour 7]
    DSS_API_KEY=<admin key> .venv/bin/python scripts/agents/provision_triage.py \
        --url https://tam-global.fe-aws.dkucloud-dev.com [--key-file <path>]
"""

import argparse
import json
import os
import pathlib
import sys

import dataikuapi

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'agents-plugin' / 'python-lib'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--hour', type=int, default=7)
    ap.add_argument('--url', default='')
    ap.add_argument('--key-file', default='')
    args = ap.parse_args()

    from atk_agent_common.triage import provision

    url = args.url or (REPO / '.dss-url').read_text().strip()
    key = (pathlib.Path(args.key_file).read_text().strip() if args.key_file
           else os.environ.get('DSS_API_KEY', '') or (REPO / '.dss-api-key').read_text().strip())
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
