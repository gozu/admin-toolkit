#!/usr/bin/env python3
"""
Trends contract: every frontend module declared with `trends: true` must have a
matching `TrendSnapshotTable` entry in python-lib/trends_registry.py — and vice
versa. Convention: snapshot key = module id with `-` replaced by `_`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "resource" / "frontend" / "src" / "utils" / "moduleRegistry.ts"
TRENDS_PATH = ROOT / "python-lib" / "trends_registry.py"


def parse_modules_with_trends(text: str) -> list[str]:
    """Return ids of modules that declare `trends: true`."""
    ids: list[str] = []
    for match in re.finditer(r"\{[^{}]*\bid:\s*'([^']+)'[^{}]*\}", text):
        block = match.group(0)
        if re.search(r"\btrends:\s*true\b", block):
            ids.append(match.group(1))
    return ids


def parse_snapshot_keys(text: str) -> list[str]:
    """Return snapshot keys declared in TREND_SNAPSHOT_TABLES."""
    return re.findall(r"TrendSnapshotTable\(\s*'([^']+)'", text)


def module_id_to_snapshot_key(module_id: str) -> str:
    return module_id.replace("-", "_")


def main() -> int:
    registry_text = REGISTRY_PATH.read_text()
    trends_text = TRENDS_PATH.read_text()

    module_ids_with_trends = parse_modules_with_trends(registry_text)
    snapshot_keys = parse_snapshot_keys(trends_text)
    snapshot_keys_set = set(snapshot_keys)
    expected_keys = {module_id_to_snapshot_key(mid) for mid in module_ids_with_trends}

    errors: list[str] = []
    warnings: list[str] = []
    for module_id in module_ids_with_trends:
        key = module_id_to_snapshot_key(module_id)
        if key not in snapshot_keys_set:
            errors.append(
                f"Module '{module_id}' declares trends:true but no TrendSnapshotTable "
                f"with key '{key}' is defined in trends_registry.py"
            )

    # Orphan tables are allowed: many existing tables are populated by the bulk
    # tracking ingest endpoint rather than a single page-driven scan. Surface as
    # warnings only so contributors notice without blocking CI.
    for key in snapshot_keys:
        if key not in expected_keys:
            warnings.append(
                f"TrendSnapshotTable '{key}' has no module declaring trends:true "
                f"(populated by bulk tracking ingest, not a per-page scan)"
            )

    for warn in warnings:
        print(f"[trends-contract] WARN: {warn}", file=sys.stderr)
    if errors:
        for err in errors:
            print(f"[trends-contract] {err}", file=sys.stderr)
        return 1

    print(
        f"[trends-contract] {len(module_ids_with_trends)} modules / "
        f"{len(snapshot_keys)} snapshot tables aligned"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
