"""
Tests for the exhaustive trends comparison system.

Covers: compare_registry helpers, TrackingDB.compare_runs_full,
TrackingDB.get_compare_dataset_detail.

Steps 59-65 from the implementation checklist.
"""

import json
import os
import sqlite3
import tempfile
import unittest

# Ensure python-lib is on path
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'python-lib'))

from compare_registry import (
    DATASET_REGISTRY, REGISTRY_BY_ID, CATEGORY_ORDER,
    SUPPORT_FULL, SUPPORT_LIFECYCLE, SUPPORT_CURRENT_ONLY,
    KIND_SCALAR, KIND_KEYED_TABLE, KIND_INTERVAL_EVENTS, KIND_METADATA,
    compare_scalar, diff_keyed_tables, diff_keyed_table_detail,
    classify_issues, classify_interval_events, classify_issue_notes,
    reconstruct_as_of, classify_known_entities,
    prepare_text_diff, normalize_json_blob, compare_json_blobs,
    build_summary_stats, build_coverage_warnings,
    check_run_has_v6_data, check_run_has_v7_data, dataset_available_for_run,
    JSON_COLUMNS, TEXT_COLUMNS,
)
from tracking import TrackingDB


def _make_db() -> tuple:
    """Create a temp TrackingDB and return (db, path)."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    db = TrackingDB(path)
    return db, path


def _ingest_minimal_run(db, run_id_offset=0, user_count=5, project_count=3,
                        health_score=85.0, plugin_count=2, connection_count=4):
    """Ingest a minimal run into the database."""
    conn = db._get_conn()
    # Instance
    conn.execute(
        """INSERT OR IGNORE INTO instances
           (instance_id, instance_url, install_id, node_id, first_seen_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ('inst1', 'http://dss.local', 'install1', 'node1', '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')
    )
    # Run
    conn.execute(
        """INSERT INTO runs (instance_id, run_at, dss_version, python_version,
           health_score, health_status, user_count, enabled_user_count,
           project_count, code_env_count, plugin_count, connection_count,
           cluster_count, coverage_status, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ('inst1', f'2025-01-0{1 + run_id_offset}T00:00:00Z', '13.4.0', '3.9.7',
         health_score, 'healthy', user_count, user_count - 1,
         project_count, 3, plugin_count, connection_count,
         1, 'complete', None)
    )
    run_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]

    # Health metrics
    conn.execute(
        """INSERT INTO run_health_metrics (run_id, cpu_cores, memory_total_mb, memory_used_mb,
           memory_available_mb, backend_heap_mb)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (run_id, 8, 16384, 12000, 4384, 4096)
    )

    # Run sections
    conn.execute(
        "INSERT INTO run_sections (run_id, section_key, status, is_complete) VALUES (?, ?, ?, ?)",
        (run_id, 'users', 'completed', 1)
    )
    conn.execute(
        "INSERT INTO run_sections (run_id, section_key, status, is_complete) VALUES (?, ?, ?, ?)",
        (run_id, 'projects', 'completed', 1)
    )

    conn.commit()
    return run_id


# =============================================================================
# Step 59: Tests for scalar diffs
# =============================================================================

class TestScalarDiffs(unittest.TestCase):

    def test_numeric_diff(self):
        row1 = {'cpu_cores': 8, 'memory_total_mb': 16384}
        row2 = {'cpu_cores': 4, 'memory_total_mb': 16384}
        fields, changed, unchanged = compare_scalar(row1, row2, ['cpu_cores', 'memory_total_mb'])
        self.assertEqual(changed, 1)
        self.assertEqual(unchanged, 1)
        cpu = next(f for f in fields if f['field'] == 'cpu_cores')
        self.assertEqual(cpu['status'], 'changed')
        self.assertEqual(cpu['delta'], 4)
        self.assertEqual(cpu['pctDelta'], 100.0)
        mem = next(f for f in fields if f['field'] == 'memory_total_mb')
        self.assertEqual(mem['status'], 'same')

    def test_string_diff(self):
        row1 = {'dss_version': '13.4.0', 'python_version': '3.9.7'}
        row2 = {'dss_version': '13.3.0', 'python_version': '3.9.7'}
        fields, changed, unchanged = compare_scalar(row1, row2, ['dss_version', 'python_version'])
        self.assertEqual(changed, 1)
        self.assertEqual(unchanged, 1)

    def test_null_handling(self):
        row1 = {'health_score': 85.0, 'notes': None}
        row2 = {'health_score': None, 'notes': None}
        fields, changed, unchanged = compare_scalar(row1, row2, ['health_score', 'notes'])
        self.assertEqual(changed, 1)  # health_score changed
        self.assertEqual(unchanged, 1)  # notes both null

    def test_json_column_diff(self):
        row1 = {'plugins_json': '{"a": 1}'}
        row2 = {'plugins_json': '{"a": 2}'}
        fields, changed, unchanged = compare_scalar(row1, row2, ['plugins_json'])
        self.assertEqual(changed, 1)
        self.assertEqual(fields[0]['kind'], 'json')

    def test_text_column_diff(self):
        row1 = {'java_memory_raw': '-Xmx4g'}
        row2 = {'java_memory_raw': '-Xmx8g'}
        fields, changed, unchanged = compare_scalar(row1, row2, ['java_memory_raw'])
        self.assertEqual(changed, 1)
        self.assertEqual(fields[0]['kind'], 'text')

    def test_none_rows(self):
        fields, changed, unchanged = compare_scalar(None, None, ['cpu_cores'])
        self.assertEqual(changed, 0)
        self.assertEqual(unchanged, 1)

    def test_compare_runs_full_scalar(self):
        db, path = _make_db()
        try:
            r1 = _ingest_minimal_run(db, 0, health_score=85.0, user_count=10)
            r2 = _ingest_minimal_run(db, 1, health_score=70.0, user_count=8)
            result = db.compare_runs_full(r1, r2)
            self.assertNotIn('error', result)
            runs_ds = next(d for d in result['datasets'] if d['datasetId'] == 'runs')
            self.assertEqual(runs_ds['kind'], 'scalar')
            self.assertGreater(runs_ds['changed'], 0)
            hm_ds = next(d for d in result['datasets'] if d['datasetId'] == 'run_health_metrics')
            self.assertEqual(hm_ds['kind'], 'scalar')
        finally:
            os.unlink(path)


# =============================================================================
# Step 60: Tests for keyed-table diffs
# =============================================================================

class TestKeyedTableDiffs(unittest.TestCase):

    def test_basic_diff(self):
        rows1 = [
            {'plugin_id': 'a', 'version': '1.0'},
            {'plugin_id': 'b', 'version': '2.0'},
            {'plugin_id': 'c', 'version': '3.0'},
        ]
        rows2 = [
            {'plugin_id': 'a', 'version': '1.0'},
            {'plugin_id': 'b', 'version': '1.5'},
            {'plugin_id': 'd', 'version': '4.0'},
        ]
        result = diff_keyed_tables(rows1, rows2, ['plugin_id'], ['plugin_id', 'version'])
        self.assertEqual(result['added'], 1)    # c
        self.assertEqual(result['removed'], 1)  # d
        self.assertEqual(result['changed'], 1)  # b
        self.assertEqual(result['unchanged'], 1)  # a

    def test_compound_key(self):
        rows1 = [
            {'project_key': 'P1', 'dataset_name': 'D1', 'dataset_type': 'FS'},
            {'project_key': 'P1', 'dataset_name': 'D2', 'dataset_type': 'SQL'},
        ]
        rows2 = [
            {'project_key': 'P1', 'dataset_name': 'D1', 'dataset_type': 'FS'},
        ]
        result = diff_keyed_tables(rows1, rows2, ['project_key', 'dataset_name'],
                                   ['project_key', 'dataset_name', 'dataset_type'])
        self.assertEqual(result['added'], 1)
        self.assertEqual(result['unchanged'], 1)

    def test_detail_pagination(self):
        rows1 = [{'id': str(i), 'val': 'a'} for i in range(150)]
        rows2 = [{'id': str(i), 'val': 'a'} for i in range(100)]
        detail = diff_keyed_table_detail(rows1, rows2, ['id'], ['id', 'val'],
                                         page=1, page_size=50)
        self.assertEqual(detail['pageSize'], 50)
        self.assertEqual(len(detail['rows']), 50)
        self.assertEqual(detail['totalRows'], 150)  # 50 added + 100 unchanged

    def test_detail_filter(self):
        rows1 = [{'k': 'a', 'v': '1'}, {'k': 'b', 'v': '2'}]
        rows2 = [{'k': 'a', 'v': '1'}, {'k': 'c', 'v': '3'}]
        detail = diff_keyed_table_detail(rows1, rows2, ['k'], ['k', 'v'],
                                         change_type='added')
        self.assertEqual(detail['totalRows'], 1)
        self.assertEqual(detail['rows'][0]['status'], 'added')

    def test_detail_search(self):
        rows1 = [{'k': 'alpha', 'v': 'x'}, {'k': 'beta', 'v': 'y'}]
        rows2 = []
        detail = diff_keyed_table_detail(rows1, rows2, ['k'], ['k', 'v'],
                                         search='alph')
        self.assertEqual(detail['totalRows'], 1)

    def test_detail_sort(self):
        rows1 = [{'k': 'b'}, {'k': 'a'}, {'k': 'c'}]
        rows2 = []
        detail = diff_keyed_table_detail(rows1, rows2, ['k'], ['k'],
                                         sort='k:asc')
        keys = [r['key']['k'] for r in detail['rows']]
        self.assertEqual(keys, ['a', 'b', 'c'])

    def test_empty_tables(self):
        result = diff_keyed_tables([], [], ['k'], ['k', 'v'])
        self.assertEqual(result['added'], 0)
        self.assertEqual(result['removed'], 0)
        self.assertEqual(result['run1Count'], 0)

    def test_compare_runs_full_keyed(self):
        db, path = _make_db()
        try:
            r1 = _ingest_minimal_run(db, 0)
            r2 = _ingest_minimal_run(db, 1)
            conn = db._get_conn()
            # Add some plugins to both runs
            conn.execute("INSERT INTO run_plugins (run_id, plugin_id, label, version, is_dev) VALUES (?, ?, ?, ?, ?)",
                         (r1, 'plug-a', 'Plugin A', '1.0', 0))
            conn.execute("INSERT INTO run_plugins (run_id, plugin_id, label, version, is_dev) VALUES (?, ?, ?, ?, ?)",
                         (r2, 'plug-a', 'Plugin A', '0.9', 0))
            conn.execute("INSERT INTO run_plugins (run_id, plugin_id, label, version, is_dev) VALUES (?, ?, ?, ?, ?)",
                         (r2, 'plug-b', 'Plugin B', '2.0', 0))
            conn.commit()

            result = db.compare_runs_full(r1, r2)
            plugins_ds = next(d for d in result['datasets'] if d['datasetId'] == 'run_plugins')
            self.assertEqual(plugins_ds['added'], 1)     # plug-b
            self.assertEqual(plugins_ds['changed'], 1)    # plug-a version changed
            self.assertEqual(plugins_ds['removed'], 0)
        finally:
            os.unlink(path)


# =============================================================================
# Step 61: Tests for lifecycle diff semantics
# =============================================================================

class TestLifecycleDiffs(unittest.TestCase):

    def test_classify_issues(self):
        issues = [
            {'first_detected_run': 1, 'last_detected_run': 3, 'resolved_run': None, 'status': 'open'},       # existed_in_both
            {'first_detected_run': 2, 'last_detected_run': 2, 'resolved_run': None, 'status': 'open'},       # opened_between_runs
            {'first_detected_run': 1, 'last_detected_run': 1, 'resolved_run': 2, 'status': 'resolved'},      # resolved_between_runs
            {'first_detected_run': 1, 'last_detected_run': 3, 'resolved_run': None, 'status': 'regressed'},  # regressed
        ]
        classified, counts = classify_issues(issues, run_id_1=3, run_id_2=1)
        self.assertEqual(len(classified), 4)
        self.assertGreaterEqual(counts.get('opened_between_runs', 0), 1)
        self.assertGreaterEqual(counts.get('resolved_between_runs', 0), 1)

    def test_classify_interval_events(self):
        emails = [
            {'email_id': 1, 'run_id': 1, 'sent_at': '2025-01-01'},
            {'email_id': 2, 'run_id': 2, 'sent_at': '2025-01-02'},
            {'email_id': 3, 'run_id': 3, 'sent_at': '2025-01-03'},
        ]
        classified, counts = classify_interval_events(emails, run_id_1=3, run_id_2=1, run_field='run_id')
        self.assertEqual(counts['event_between_runs'], 2)  # run_id=2 and run_id=3 (both > 1 and <= 3)
        self.assertEqual(counts['before_run2'], 1)          # run_id=1

    def test_classify_issue_notes(self):
        notes = [
            {'note_id': 1, 'created_at': '2025-01-01T00:00:00Z', 'note': 'old note'},
            {'note_id': 2, 'created_at': '2025-01-05T00:00:00Z', 'note': 'between note'},
            {'note_id': 3, 'created_at': '2025-01-15T00:00:00Z', 'note': 'new note'},
        ]
        classified, counts = classify_issue_notes(
            notes,
            run1_at='2025-01-10T00:00:00Z',
            run2_at='2025-01-02T00:00:00Z',
        )
        self.assertEqual(counts['visible_at_run2'], 1)
        self.assertEqual(counts['created_between_runs'], 1)
        self.assertEqual(counts['visible_at_run1'], 2)

    def test_reconstruct_as_of(self):
        rows = [
            {'login': 'a', 'first_seen_run': 1, 'last_seen_run': 5},
            {'login': 'b', 'first_seen_run': 3, 'last_seen_run': 5},
            {'login': 'c', 'first_seen_run': 1, 'last_seen_run': 2},
        ]
        as_of_3 = reconstruct_as_of(rows, 3)
        logins = {r['login'] for r in as_of_3}
        self.assertIn('a', logins)
        self.assertIn('b', logins)
        self.assertNotIn('c', logins)

    def test_classify_known_entities(self):
        rows = [
            {'login': 'a', 'first_seen_run': 1, 'last_seen_run': 5},
            {'login': 'b', 'first_seen_run': 3, 'last_seen_run': 5},
            {'login': 'c', 'first_seen_run': 1, 'last_seen_run': 2},
        ]
        result = classify_known_entities(rows, run_id_1=4, run_id_2=2, key_fields=['login'])
        self.assertEqual(result['added'], 1)        # b (not in run2, in run1)
        self.assertEqual(result['removed'], 1)      # c (in run2, not in run1)
        self.assertEqual(result['existed_in_both'], 1)  # a


# =============================================================================
# Step 62: Tests for dataset availability flags
# =============================================================================

class TestAvailabilityFlags(unittest.TestCase):

    def test_v1_always_available(self):
        entry = REGISTRY_BY_ID['runs']
        self.assertTrue(
            dataset_available_for_run(entry, {}, {}, False, False)
        )

    def test_v6_unavailable_when_no_data(self):
        entry = REGISTRY_BY_ID['user_snapshots']
        run = {'user_count': 10}  # has users but no V6 data
        self.assertFalse(
            dataset_available_for_run(entry, run, {}, False, False)
        )

    def test_v6_available_when_has_data(self):
        entry = REGISTRY_BY_ID['user_snapshots']
        run = {'user_count': 10}
        self.assertTrue(
            dataset_available_for_run(entry, run, {'user_snapshots': 10}, True, False)
        )

    def test_v7_unavailable_pre_v7(self):
        entry = REGISTRY_BY_ID['run_datasets']
        run = {}
        # Has V6 data but no V7
        self.assertFalse(
            dataset_available_for_run(entry, run, {}, True, False)
        )

    def test_v7_available_when_has_data(self):
        entry = REGISTRY_BY_ID['run_datasets']
        run = {}
        self.assertTrue(
            dataset_available_for_run(entry, run, {'run_datasets': 5}, True, True)
        )

    def test_current_only_always_available(self):
        entry = REGISTRY_BY_ID['instances']
        self.assertTrue(
            dataset_available_for_run(entry, {}, {}, False, False)
        )

    def test_check_v6_data(self):
        self.assertTrue(check_run_has_v6_data({'run_plugins': 3}))
        self.assertFalse(check_run_has_v6_data({'run_plugins': 0}))
        self.assertFalse(check_run_has_v6_data({}))

    def test_check_v7_data(self):
        self.assertTrue(check_run_has_v7_data({'run_datasets': 1}))
        self.assertFalse(check_run_has_v7_data({}))

    def test_manifest_flags_in_compare(self):
        db, path = _make_db()
        try:
            r1 = _ingest_minimal_run(db, 0)
            r2 = _ingest_minimal_run(db, 1)
            result = db.compare_runs_full(r1, r2)
            # V1 datasets should be available
            runs_ds = next(d for d in result['datasets'] if d['datasetId'] == 'runs')
            self.assertTrue(runs_ds['availableInRun1'])
            self.assertTrue(runs_ds['availableInRun2'])
            # V6 datasets with no data should not be available (runs have user_count > 0)
            users_ds = next(d for d in result['datasets'] if d['datasetId'] == 'user_snapshots')
            self.assertFalse(users_ds['availableInRun1'])
        finally:
            os.unlink(path)


# =============================================================================
# Step 63: Tests for empty dataset rendering
# =============================================================================

class TestEmptyDatasets(unittest.TestCase):

    def test_empty_keyed_table(self):
        result = diff_keyed_tables([], [], ['k'], ['k', 'v'])
        self.assertEqual(result['run1Count'], 0)
        self.assertEqual(result['run2Count'], 0)
        self.assertEqual(result['added'], 0)

    def test_empty_detail(self):
        detail = diff_keyed_table_detail([], [], ['k'], ['k', 'v'])
        self.assertEqual(detail['totalRows'], 0)
        self.assertEqual(detail['rows'], [])

    def test_manifest_includes_empty_datasets(self):
        db, path = _make_db()
        try:
            r1 = _ingest_minimal_run(db, 0)
            r2 = _ingest_minimal_run(db, 1)
            result = db.compare_runs_full(r1, r2)
            # All 26 datasets should be in the manifest
            self.assertEqual(len(result['datasets']), 26)
            # run_campaign_summaries should be present even with 0 rows
            rcs = next(d for d in result['datasets'] if d['datasetId'] == 'run_campaign_summaries')
            self.assertEqual(rcs['run1Count'], 0)
            self.assertEqual(rcs['run2Count'], 0)
        finally:
            os.unlink(path)


# =============================================================================
# Step 64: Tests for current-only metadata rendering
# =============================================================================

class TestCurrentOnlyMetadata(unittest.TestCase):

    def test_metadata_datasets_labeled(self):
        for did in ('instances', 'schema_version', 'campaign_settings', 'campaign_exemptions'):
            entry = REGISTRY_BY_ID[did]
            self.assertEqual(entry['support'], SUPPORT_CURRENT_ONLY)
            self.assertEqual(entry['kind'], KIND_METADATA)

    def test_metadata_in_manifest(self):
        db, path = _make_db()
        try:
            r1 = _ingest_minimal_run(db, 0)
            r2 = _ingest_minimal_run(db, 1)
            result = db.compare_runs_full(r1, r2)
            inst_ds = next(d for d in result['datasets'] if d['datasetId'] == 'instances')
            self.assertTrue(inst_ds['availableInRun1'])
            self.assertEqual(inst_ds['support'], 'current_only')
            self.assertIn('Current-only', inst_ds['notes'])
        finally:
            os.unlink(path)

    def test_metadata_detail(self):
        db, path = _make_db()
        try:
            r1 = _ingest_minimal_run(db, 0)
            r2 = _ingest_minimal_run(db, 1)
            detail = db.get_compare_dataset_detail(r1, r2, 'instances')
            self.assertEqual(detail['kind'], 'metadata')
            self.assertIn('Current-only', detail['notes'])
            self.assertGreater(detail['totalRows'], 0)
        finally:
            os.unlink(path)


# =============================================================================
# Step 65: Tests for pagination, filtering, sorting
# =============================================================================

class TestPaginationFilterSort(unittest.TestCase):

    def test_pagination(self):
        rows1 = [{'k': str(i), 'v': 'x'} for i in range(250)]
        rows2 = []
        p1 = diff_keyed_table_detail(rows1, rows2, ['k'], ['k', 'v'], page=1, page_size=100)
        self.assertEqual(len(p1['rows']), 100)
        self.assertEqual(p1['totalRows'], 250)
        p3 = diff_keyed_table_detail(rows1, rows2, ['k'], ['k', 'v'], page=3, page_size=100)
        self.assertEqual(len(p3['rows']), 50)

    def test_filtering(self):
        rows1 = [{'k': 'a'}, {'k': 'b'}]
        rows2 = [{'k': 'a'}, {'k': 'c'}]
        added = diff_keyed_table_detail(rows1, rows2, ['k'], ['k'], change_type='added')
        removed = diff_keyed_table_detail(rows1, rows2, ['k'], ['k'], change_type='removed')
        self.assertEqual(added['totalRows'], 1)
        self.assertEqual(removed['totalRows'], 1)

    def test_search(self):
        rows1 = [{'k': 'alpha-one'}, {'k': 'beta-two'}]
        rows2 = []
        result = diff_keyed_table_detail(rows1, rows2, ['k'], ['k'], search='alpha')
        self.assertEqual(result['totalRows'], 1)

    def test_sort_asc(self):
        rows1 = [{'k': 'c'}, {'k': 'a'}, {'k': 'b'}]
        rows2 = []
        result = diff_keyed_table_detail(rows1, rows2, ['k'], ['k'], sort='k:asc')
        keys = [r['key']['k'] for r in result['rows']]
        self.assertEqual(keys, ['a', 'b', 'c'])

    def test_sort_desc(self):
        rows1 = [{'k': 'c'}, {'k': 'a'}, {'k': 'b'}]
        rows2 = []
        result = diff_keyed_table_detail(rows1, rows2, ['k'], ['k'], sort='k:desc')
        keys = [r['key']['k'] for r in result['rows']]
        self.assertEqual(keys, ['c', 'b', 'a'])

    def test_detail_endpoint_pagination(self):
        db, path = _make_db()
        try:
            r1 = _ingest_minimal_run(db, 0)
            r2 = _ingest_minimal_run(db, 1)
            detail = db.get_compare_dataset_detail(r1, r2, 'run_sections', page=1, page_size=1)
            self.assertEqual(detail['pageSize'], 1)
            self.assertLessEqual(len(detail['rows']), 1)
        finally:
            os.unlink(path)


# =============================================================================
# Additional helper tests
# =============================================================================

class TestHelpers(unittest.TestCase):

    def test_text_diff(self):
        result = prepare_text_diff('-Xmx4g', '-Xmx8g')
        self.assertEqual(result['status'], 'changed')

    def test_text_diff_same(self):
        result = prepare_text_diff('hello', 'hello')
        self.assertEqual(result['status'], 'same')

    def test_json_normalize(self):
        result = normalize_json_blob('{"a": 1, "b": [1,2]}')
        self.assertEqual(result['type'], 'object')
        self.assertIn('a', result['summary']['keys'])

    def test_json_compare(self):
        result = compare_json_blobs('{"a": 1}', '{"a": 2}')
        self.assertEqual(result['status'], 'changed')
        result2 = compare_json_blobs('{"a": 1}', '{"a": 1}')
        self.assertEqual(result2['status'], 'same')

    def test_summary_stats(self):
        r1 = {'health_score': 85, 'user_count': 10, 'project_count': 5,
               'plugin_count': 3, 'connection_count': 4, 'code_env_count': 2,
               'enabled_user_count': 9, 'cluster_count': 1, 'coverage_status': 'complete'}
        r2 = {'health_score': 70, 'user_count': 8, 'project_count': 5,
               'plugin_count': 3, 'connection_count': 3, 'code_env_count': 2,
               'enabled_user_count': 7, 'cluster_count': 1, 'coverage_status': 'complete'}
        stats = build_summary_stats(r1, r2)
        self.assertEqual(stats['healthScore']['delta'], 15)
        self.assertEqual(stats['userCount']['delta'], 2)
        self.assertEqual(stats['projectCount']['delta'], 0)

    def test_coverage_warnings(self):
        r1 = {'run_id': 1, 'coverage_status': 'partial'}
        r2 = {'run_id': 2, 'coverage_status': 'complete'}
        s1 = [{'section_key': 'code_envs', 'is_complete': 0, 'error_message': 'timeout'}]
        s2 = []
        warnings = build_coverage_warnings(r1, r2, s1, s2)
        self.assertGreater(len(warnings), 0)
        types = [w['type'] for w in warnings]
        self.assertIn('coverage_status', types)
        self.assertIn('incomplete_section', types)

    def test_registry_completeness(self):
        self.assertEqual(len(DATASET_REGISTRY), 26)
        for entry in DATASET_REGISTRY:
            self.assertIn('dataset_id', entry)
            self.assertIn('label', entry)
            self.assertIn('category', entry)
            self.assertIn('kind', entry)
            self.assertIn('support', entry)
            self.assertIn('key_fields', entry)
            self.assertIn('columns', entry)
            self.assertIn(entry['category'], CATEGORY_ORDER)


if __name__ == '__main__':
    unittest.main()
