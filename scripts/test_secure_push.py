"""Local regression tests: python3 -m unittest discover -s scripts -p test_secure_push.py."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class SecurePushPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        scripts = self.root / "scripts"
        scripts.mkdir()
        for name in ("secure-push.sh", "secure-push.schema.json", "security-review-report-excludes.txt"):
            shutil.copy(Path(__file__).parent / name, scripts / name)
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("add", "scripts")
        self.git("commit", "-qm", "baseline")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        # These must never run during preflight, even outside --inspect mode.
        for name in ("claude", "codex", "timeout"):
            stub = self.bin / name
            stub.write_text('#!/bin/sh\ntouch "$REVIEW_CALLED"\nexit 99\n')
            stub.chmod(0o755)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def commit_files(self, files):
        for name, content in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            self.git("add", "--", name)
        self.git("commit", "-qm", "fixture")

    def run_gate(self, *args, cap="950000"):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                   SECURE_PUSH_MAX_PAYLOAD_BYTES=cap,
                   REVIEW_CALLED=str(self.root / "review-called"))
        result = subprocess.run(["bash", "scripts/secure-push.sh", *args],
                                cwd=self.root, env=env, capture_output=True, text=True)
        self.assertFalse((self.root / "review-called").exists())
        return result.returncode, result.stdout + result.stderr

    def test_large_reports_excluded_but_source_extensions_retained(self):
        self.commit_files({
            "docs/headless-capability-matrix.json": "report\n" * 180000,
            "docs/reports/future.html": "report\n" * 180000,
            "src/page.html": "<script>app()</script>\n",
            "src/config.json": '{"enabled": true}\n',
            "AGENTS.md": "Agent instructions\n",
            "package-lock.json": '{"lockfileVersion": 3}\n',
        })
        code, output = self.run_gate("--inspect")
        self.assertEqual(code, 0, output)
        included, excluded = output.split("Excluded from LLM content review")
        for name in ("src/page.html", "src/config.json", "AGENTS.md", "package-lock.json"):
            self.assertIn(name, included)
        self.assertNotIn("headless-capability-matrix.json", included)
        self.assertIn("headless-capability-matrix.json", excluded)
        self.assertIn("docs/reports/future.html", excluded)

    def test_oversize_stops_before_review_and_identifies_file(self):
        self.commit_files({"src/big.py": "print('source')\n" * 200})
        code, output = self.run_gate(cap="1000")
        self.assertEqual(code, 3, output)
        self.assertIn("src/big.py", output)
        self.assertIn("no LLM reviewers ran", output)
        self.assertIn("investigate the largest files", output)
        self.assertNotIn("PAYLOAD TRUNCATED", output)

    def test_excluded_report_secrets_still_block(self):
        self.commit_files({"docs/reports/leak.txt": "ghp_" + "a" * 36 + "\n"})
        code, output = self.run_gate("--inspect")
        self.assertEqual(code, 1, output)
        self.assertIn("local secret pre-scan", output)
        self.assertNotIn("a" * 36, output)

    def test_report_only_inspection_succeeds(self):
        self.commit_files({"docs/reports/result.csv": "reference output\n"})
        code, output = self.run_gate("--inspect")
        self.assertEqual(code, 0, output)
        self.assertIn("docs/reports/result.csv", output)

    def test_bundle_only_changes_still_rejected(self):
        self.commit_files({"resource/dist/app.js": "built output\n"})
        code, output = self.run_gate("--inspect")
        self.assertEqual(code, 1, output)
        self.assertIn("only changes are generated/vendored", output)


if __name__ == "__main__":
    unittest.main()
