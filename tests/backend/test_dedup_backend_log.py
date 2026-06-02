"""Tests for scripts/dedup_backend_log.py."""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import dedup_backend_log as dedup


SAMPLE_LOG = textwrap.dedent(
    """\
    preface text
    backend.log

    [2026/04/23-20:25:15.018] [qtp123-1] [DEBUG] [dku.tracing]  - [ct: 1] Start call: /publicapi/projects/{projectKey}/settings [GET] [projectKey=ADMININSIGHTS]
    [2026/04/23-20:25:15.019] [qtp123-2] [DEBUG] [dku.tracing]  - [ct: 2] Start call: /publicapi/projects/{projectKey}/settings [GET] [projectKey=USERINSIGHTS]
    [2026/04/23-20:25:29.088] [qtp123-3] [WARN] [dku.code.envs.usages]  - Failed to check usages in saved models. Skipping
    java.io.FileNotFoundException: File '/data/dataiku/dss_data/saved_models/FOO/A1/versions/v1/core_params.json' does not exist
    \tat com.example.One.go(One.java:10)
    [2026/04/23-20:25:29.089] [qtp123-4] [WARN] [dku.code.envs.usages]  - Failed to check usages in saved models. Skipping
    java.io.FileNotFoundException: File '/data/dataiku/dss_data/saved_models/BAR/B2/versions/v1/core_params.json' does not exist
    \tat com.example.One.go(One.java:10)
    [2026/04/23-20:27:05.965] [process-resource-monitor-3748494-631604] [DEBUG] [dku.resource]  - Process stats for pid 3748494: {"pid":3748494,"cpuTotalMS":43200}
    [2026/04/23-20:28:05.969] [process-resource-monitor-3748494-631604] [DEBUG] [dku.resource]  - Process stats for pid 3748494: {"pid":3748494,"cpuTotalMS":43210}
    """
)


class DedupBackendLogTest(unittest.TestCase):
    def _write_temp_log(self, content: str = SAMPLE_LOG) -> str:
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        self.addCleanup(lambda: os.path.exists(handle.name) and os.unlink(handle.name))
        handle.write(content)
        handle.close()
        return handle.name

    def test_load_backend_slice_uses_literal_anchor(self):
        path = self._write_temp_log()
        backend_lines, anchor_line, first_backend_line = dedup.load_backend_slice(path, "backend.log")

        self.assertEqual(anchor_line, 2)
        self.assertEqual(first_backend_line, 4)
        self.assertEqual(len(backend_lines), 10)
        self.assertTrue(backend_lines[0].startswith("[2026/04/23-20:25:15.018]"))

    def test_build_bundle_groups_similar_blocks(self):
        path = self._write_temp_log()
        bundle = dedup.build_bundle(path)

        self.assertEqual(bundle.total_blocks, 6)
        self.assertEqual(bundle.total_backend_lines, 10)
        self.assertEqual(len(bundle.families), 3)

        tracing_family = bundle.families[0]
        self.assertEqual(tracing_family.count, 2)
        self.assertIn("projectKey=<PROJECT_KEY_1>", tracing_family.template)
        self.assertIn("TIMESTAMP_1", tracing_family.placeholder_names)
        self.assertIn("PROJECT_KEY_1", tracing_family.placeholder_names)

        exception_family = bundle.families[1]
        self.assertEqual(exception_family.count, 2)
        self.assertIn("<SAVED_MODEL_PATH_1>", exception_family.template)
        self.assertTrue(exception_family.template.endswith("\tat com.example.One.go(One.java:10)"))

        stats_family = bundle.families[2]
        self.assertEqual(stats_family.count, 2)
        self.assertIn("<PROCESS_STATS_JSON_1>", stats_family.template)

    def test_markdown_round_trips_exact_backend_blocks(self):
        path = self._write_temp_log()
        bundle = dedup.build_bundle(path)
        markdown = dedup.render_markdown(bundle)

        reconstructed = dedup.reconstruct_backend_from_markdown(markdown)

        self.assertEqual(reconstructed, [block.raw_text for block in bundle.blocks])

    def test_main_writes_markdown_file(self):
        path = self._write_temp_log()
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".md") as output:
            output_path = output.name
        self.addCleanup(lambda: os.path.exists(output_path) and os.unlink(output_path))

        rc = dedup.main(["--input", path, "--output", output_path])

        self.assertEqual(rc, 0)
        with open(output_path, "r", encoding="utf-8") as handle:
            rendered = handle.read()
        self.assertIn("# Backend Log LLM Zip", rendered)
        self.assertIn("### F0001", rendered)
        self.assertIn("```jsonl", rendered)


if __name__ == "__main__":
    unittest.main()
