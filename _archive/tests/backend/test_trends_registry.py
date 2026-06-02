import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
PYLIB = os.path.join(ROOT, 'python-lib')
if PYLIB not in sys.path:
    sys.path.insert(0, PYLIB)

from compare_registry import DATASET_REGISTRY
from trends_registry import TREND_SNAPSHOT_TABLES


def test_trends_registry_has_compare_dataset_for_each_snapshot_table():
    compare_ids = {item['dataset_id'] for item in DATASET_REGISTRY}
    missing = [
        spec.compare_dataset_id
        for spec in TREND_SNAPSHOT_TABLES
        if spec.compare_dataset_id not in compare_ids
    ]
    assert missing == []


def test_trends_registry_keys_and_tables_are_unique():
    keys = [spec.key for spec in TREND_SNAPSHOT_TABLES]
    tables = [spec.table for spec in TREND_SNAPSHOT_TABLES]
    assert len(keys) == len(set(keys))
    assert len(tables) == len(set(tables))


def test_trends_registry_columns_match_compare_registry():
    by_dataset = {item['dataset_id']: item for item in DATASET_REGISTRY}
    for spec in TREND_SNAPSHOT_TABLES:
        compare_columns = set(by_dataset[spec.compare_dataset_id]['columns'])
        assert set(spec.columns).issubset(compare_columns)
