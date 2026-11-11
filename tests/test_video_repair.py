import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from video_repair import VideoRepair


class RepairJournals(unittest.TestCase):
    def test_verified_repair_updates_identity_without_losing_original_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "source"
            folder.mkdir()
            source = {
                "original_key": "source",
                "replacement_key": "old-candidate",
                "original_bytes": 1000,
                "encoded_bytes": 200,
                "encoded_sha256": "old-proof",
                "stage": "trashed",
                "account": "example",
            }
            (folder / "state.json").write_text(json.dumps(source))
            repair = {
                "_repair_parent": "source",
                "original_key": "old-candidate",
                "replacement_key": "new-candidate",
                "encoded_bytes": 201,
                "encoded_sha256": "new-proof",
                "download_verified_sha256": "new-proof",
                "output": "optimized-original-quality.mp4",
            }
            worker = VideoRepair.__new__(VideoRepair)
            worker.log = lambda *a, **kw: None
            with (
                patch("video_repair.WORK", root),
                patch("video_migration.WORK", root),
                patch("video_repair.Videos.finalize", return_value=True) as verified,
            ):
                self.assertTrue(worker.finalize(repair))
            verified.assert_called_once_with(repair)
            result = json.loads((folder / "state.json").read_text())
            self.assertEqual(result["replacement_key"], "new-candidate")
            self.assertEqual(result["original_key"], "source")
            self.assertEqual(result["original_bytes"], 1000)
            self.assertEqual(
                result["superseded_replacements"][0]["key"], "old-candidate"
            )
            self.assertEqual(result["download_verified_sha256"], "new-proof")

    def test_account_mismatch_prevents_repair_requests(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "source"
            folder.mkdir()
            (folder / "state.json").write_text(
                json.dumps({"account": "different-account"})
            )
            worker = VideoRepair.__new__(VideoRepair)
            worker.account = "expected-account"
            worker.api = SimpleNamespace(
                extended=Mock(side_effect=AssertionError("Unexpected cloud request"))
            )
            with (
                patch("video_repair.WORK", root),
                self.assertRaisesRegex(RuntimeError, "account mismatch"),
            ):
                worker.process(["source"])
            worker.api.extended.assert_not_called()


if __name__ == "__main__":
    unittest.main()
