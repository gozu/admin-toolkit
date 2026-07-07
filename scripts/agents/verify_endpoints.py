#!/usr/bin/env python3
"""Phase A gate for the agents plugin: probe the live admin-toolkit backend and
record the REAL response shape of every endpoint the agent tools will consume.

Run before writing any consuming code (user rule: never assume API shapes).

    .venv/bin/python scripts/agents/verify_endpoints.py [--base URL] [--out DIR]

Prints a shape summary (keys/types/sizes, depth-limited) per endpoint and dumps
the full raw payloads as JSON files into --out (default: a local scratch dir,
NOT committed) so consuming code can be written against ground truth.

Unlock endpoints are probed with a deliberately wrong password: a 401
`invalid-password` proves the headless contract (POST plaintext JSON) works
without needing the real secret; 400 `not-configured` means no secret is set.
"""

import argparse
import json
import pathlib
import sys
import time

import requests

DEFAULT_BASE = "https://tam-global.fe-aws.dkucloud-dev.com/web-apps-backends/DIAG_PARSER_BRANCH1/Gv9CLFn"

MAX_DEPTH = 4
MAX_KEYS = 40


def shape(value, depth=0):
    """Depth-limited structural summary: dict keys w/ child shapes, list length
    + first-element shape, scalars as type names (sample for short strings)."""
    if depth >= MAX_DEPTH:
        return type(value).__name__
    if isinstance(value, dict):
        keys = list(value.keys())
        out = {}
        for k in keys[:MAX_KEYS]:
            out[k] = shape(value[k], depth + 1)
        if len(keys) > MAX_KEYS:
            out["..."] = f"+{len(keys) - MAX_KEYS} more keys"
        return out
    if isinstance(value, list):
        if not value:
            return "list[0]"
        return {f"list[{len(value)}]": shape(value[0], depth + 1)}
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return f"{type(value).__name__}({value})" if depth <= 2 else type(value).__name__
    if isinstance(value, str):
        return f"str({value[:60]!r})" if len(value) <= 60 else f"str[{len(value)}]"
    if value is None:
        return "null"
    return type(value).__name__


def probe(session, base, name, method, path, out_dir, json_body=None, headers=None, timeout=120):
    url = base.rstrip("/") + path
    t0 = time.time()
    try:
        resp = session.request(method, url, json=json_body, headers=headers or {}, timeout=timeout)
    except Exception as exc:
        print(f"\n=== {name}: {method} {path} → EXCEPTION {type(exc).__name__}: {exc}")
        return None
    elapsed = time.time() - t0
    size = len(resp.content)
    print(f"\n=== {name}: {method} {path} → HTTP {resp.status_code}  ({elapsed:.1f}s, {size:,} bytes)")
    ctype = resp.headers.get("Content-Type", "")
    if "json" not in ctype:
        print(f"    non-JSON content-type: {ctype}; first 200 chars: {resp.text[:200]!r}")
        return None
    try:
        data = resp.json()
    except Exception:
        print(f"    JSON parse failed; first 200 chars: {resp.text[:200]!r}")
        return None
    out_path = out_dir / f"{name}.json"
    out_path.write_text(json.dumps(data, indent=2, default=str))
    print(json.dumps(shape(data), indent=2)[:6000])
    cookies = resp.headers.get("Set-Cookie")
    if cookies:
        print(f"    Set-Cookie: {cookies[:160]}")
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--out", default=None, help="dir for raw payload dumps")
    ap.add_argument("--remote-host", default="akaos-vm", help="host id for the X-DSS-Host-Id probe")
    args = ap.parse_args()

    out_dir = pathlib.Path(args.out) if args.out else pathlib.Path(__file__).parent / "shapes"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Base: {args.base}\nDumping raw payloads to: {out_dir}")

    s = requests.Session()

    hosts = probe(s, args.base, "hosts", "GET", "/api/hosts", out_dir)
    probe(s, args.base, "overview", "GET", "/api/overview", out_dir)
    probe(s, args.base, "host-summary", "GET", "/api/host/summary", out_dir)
    probe(s, args.base, "threshold-defaults", "GET", "/api/settings/threshold-defaults", out_dir)
    probe(s, args.base, "cru", "GET", "/api/cru", out_dir, timeout=420)

    # Unlock contract probes (wrong password on purpose — 401 proves the flow)
    probe(s, args.base, "red-unlock-wrongpw", "POST", "/api/auth/red/unlock", out_dir,
          json_body={"password": "definitely-wrong-probe"})
    probe(s, args.base, "keys-unlock-wrongpw", "POST", "/api/hosts/keys/unlock", out_dir,
          json_body={"password": "definitely-wrong-probe"})
    probe(s, args.base, "red-status", "GET", "/api/auth/red/status", out_dir)
    probe(s, args.base, "keys-status", "GET", "/api/hosts/keys/status", out_dir)

    # Remote-host probe: same endpoint, X-DSS-Host-Id header
    valid_ids = [h.get("id") for h in hosts] if isinstance(hosts, list) else []
    print(f"\nHost ids reported by /api/hosts: {valid_ids}")
    probe(s, args.base, f"overview-remote-{args.remote_host}", "GET", "/api/overview", out_dir,
          headers={"X-DSS-Host-Id": args.remote_host}, timeout=180)
    # And a hallucinated host id — what does the backend do?
    probe(s, args.base, "overview-bad-host", "GET", "/api/overview", out_dir,
          headers={"X-DSS-Host-Id": "no-such-host"})

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
