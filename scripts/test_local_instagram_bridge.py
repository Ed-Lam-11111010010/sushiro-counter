"""Offline tests for the local-to-GitHub Instagram review bridge."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_instagram_bridge as bridge


class BridgeUnitTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {
            "schemaVersion": 1, "account": "sushiro.hk",
            "requiresReview": True, "posts": [{
                "postId": "TEST123", "url": "https://www.instagram.com/p/TEST123/",
                "contentFingerprint": "a" * 64, "observedAt": "2026-11-23T01:00:00+08:00",
                "items": [{"id": "candidate_1", "name": "測試壽司",
                           "priceText": "$17", "verificationStatus": "unverified"}],
            }],
        }
        self.report = {
            "profileFetchStatus": "success", "postDiscoveryStatus": "found",
            "sourceStatus": "posts_found", "discoveryComplete": True,
            "sourceFailures": [], "posts": self.candidate["posts"],
        }
        self.preview = {
            "dryRunOnly": True, "productionAutoPublishEnabled": False,
            "summary": {"candidateItems": 1, "eligibleItems": 0,
                        "quarantinedItems": 1},
        }
        self.events = []

    def simulated_command(self, *argv, cwd=None, timeout=240, check=True):
        self.events.append(argv)
        if argv[:2] == ("git", "clone"):
            directory = Path(argv[-1])
            (directory / "data").mkdir(parents=True)
            (directory / bridge.CANDIDATES).write_text(
                json.dumps(self.candidate), encoding="utf-8"
            )
        if len(argv) > 1 and argv[1] == "scripts/detect_instagram_menu.py" and "--check" not in argv:
            Path(argv[argv.index("--output") + 1]).write_text(
                json.dumps(self.candidate), encoding="utf-8"
            )
            Path(argv[argv.index("--report") + 1]).write_text(
                json.dumps(self.report), encoding="utf-8"
            )
        if len(argv) > 1 and argv[1] == "scripts/instagram_autopublish.py":
            Path(argv[argv.index("--preview") + 1]).write_text(
                json.dumps(self.preview), encoding="utf-8"
            )
            Path(argv[argv.index("--diff") + 1]).write_text(
                json.dumps({"wouldChangePromotions": False}), encoding="utf-8"
            )
        exit_code = 1 if argv[:4] == ("git", "diff", "--cached", "--quiet") else 0
        return subprocess.CompletedProcess(argv, exit_code, "", "")

    def run_bridge(self, state_dir, *, submit=False):
        with patch.object(bridge, "require_executables"), patch.object(
            bridge, "command", side_effect=self.simulated_command
        ):
            return bridge.execute(state_dir, submit=submit)

    def test_signature_ignores_scan_diagnostics_but_detects_item_edits(self):
        first = bridge.candidate_signature(self.candidate)
        copied = copy.deepcopy(self.candidate)
        copied["posts"][0]["observedAt"] = "2026-11-24T09:00:00+08:00"
        copied["posts"][0]["discoverySources"] = ["different-source"]
        self.assertEqual(first, bridge.candidate_signature(copied))
        copied["posts"][0]["items"][0]["priceText"] = "$22"
        self.assertNotEqual(first, bridge.candidate_signature(copied))

    def test_scan_health_requires_source_completeness(self):
        self.assertTrue(bridge.discovery_healthy(self.report))
        for patch_value in (
            {"discoveryComplete": False},
            {"postDiscoveryStatus": "partial"},
            {"sourceStatus": "unavailable"},
            {"posts": []},
            {"sourceFailures": ["public-search-rss"]},
        ):
            with self.subTest(patch_value=patch_value):
                self.assertFalse(bridge.discovery_healthy({**self.report, **patch_value}))

    def test_dry_run_generates_preview_without_any_push_or_gh_access(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_bridge(Path(folder))
            self.assertEqual(result["status"], "preview-ready")
            self.assertFalse(result["submitted"])
            self.assertEqual(result["candidateItems"], 1)
            self.assertTrue((Path(folder) / "latest_autopublish_preview.json").exists())
            self.assertFalse((Path(folder) / "last_submitted.json").exists())
            self.assertFalse(any(cmd[0] == "gh" for cmd in self.events))
            self.assertFalse(any(cmd[:2] == ("git", "push") for cmd in self.events))

    def test_incomplete_scan_creates_neither_pr_nor_preview(self):
        self.report["postDiscoveryStatus"] = "none"
        self.report["sourceStatus"] = "profile_only"
        self.report["discoveryComplete"] = False
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_bridge(Path(folder))
            self.assertEqual(result["status"], "source-incomplete")
            self.assertFalse((Path(folder) / "latest_autopublish_preview.json").exists())
            self.assertFalse((Path(folder) / "last_submitted.json").exists())
            self.assertFalse(any(cmd[:2] == ("git", "push") for cmd in self.events))

    def test_unexpected_auto_publish_mode_is_rejected(self):
        self.preview["productionAutoPublishEnabled"] = True
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, "dry-run mode"):
                self.run_bridge(Path(folder))
            self.assertFalse((Path(folder) / "last_submitted.json").exists())

    def test_submit_creates_only_draft_review_pr_and_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            with patch.object(bridge, "require_executables"), patch.object(
                bridge, "command", side_effect=self.simulated_command
            ), patch.object(bridge, "existing_pr", side_effect=[
                None, {"url": "https://github.com/example/PR/99", "isDraft": True,
                       "state": "OPEN", "number": 99},
            ]), patch.object(bridge, "remote_branch_document", return_value=None):
                result = bridge.execute(state, submit=True)
            self.assertTrue(result["submitted"])
            self.assertEqual(result["status"], "review-pr-created")
            receipt = bridge.read_json(state / "last_submitted.json")
            self.assertEqual(receipt["signature"], result["signature"])
            self.assertTrue(any(cmd[:2] == ("git", "push") for cmd in self.events))
            staged = [cmd for cmd in self.events if cmd[:2] == ("git", "add")]
            self.assertEqual(staged, [("git", "add", "--", "data/instagram_candidates.json")])
            requests = [cmd for cmd in self.events if cmd[:3] == ("gh", "pr", "create")]
            self.assertEqual(len(requests), 1)
            self.assertIn("--draft", requests[0])
            self.assertEqual(requests[0][requests[0].index("--base") + 1], bridge.BASE)
            self.assertFalse(any("menu.json" in str(cmd) or "promotions.json" in str(cmd) for cmd in staged))

    def test_repeat_scan_does_not_resubmit_same_content(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            bridge.atomic_json(state / "last_submitted.json", {
                "signature": bridge.candidate_signature(self.candidate),
                "url": "https://github.com/example/pull/99",
            })
            with patch.object(bridge, "require_executables"), patch.object(
                bridge, "command", side_effect=self.simulated_command
            ):
                result = bridge.execute(state, submit=True)
            self.assertEqual(result["status"], "already-submitted")
            self.assertFalse(result["submitted"])
            self.assertFalse(any(cmd[:2] == ("git", "push") for cmd in self.events))

    def test_closed_review_pr_is_never_reopened_or_reported_as_new(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(bridge, "require_executables"), patch.object(
                bridge, "command", side_effect=self.simulated_command
            ), patch.object(bridge, "existing_pr", return_value={
                "state": "CLOSED", "isDraft": True,
                "url": "https://github.com/example/pull/77",
            }), patch.object(bridge, "remote_branch_document", return_value=self.candidate):
                result = bridge.execute(Path(folder), submit=True)
            self.assertEqual(result["status"], "prior-review-closed")
            self.assertFalse(result["submitted"])
            self.assertFalse(any(cmd[:2] == ("git", "push") for cmd in self.events))
            self.assertFalse(any(cmd[:3] == ("gh", "pr", "create") for cmd in self.events))
            self.assertFalse((Path(folder) / "last_submitted.json").exists())

    def test_existing_remote_review_branch_must_match_same_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(bridge, "require_executables"), patch.object(
                bridge, "command", side_effect=self.simulated_command
            ), patch.object(bridge, "existing_pr", return_value=None), patch.object(
                bridge, "remote_branch_document", return_value={"posts": [{"postId": "WRONG"}]}
            ):
                with self.assertRaisesRegex(RuntimeError, "unexpected content"):
                    bridge.execute(Path(folder), submit=True)
            self.assertFalse(any(cmd[:2] == ("git", "push") for cmd in self.events))


if __name__ == "__main__":
    unittest.main()
