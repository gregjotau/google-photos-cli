from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from migration import Migration, is_video
from selection import Selection
from test_selection import fixture
import test_safety


class LivePolicy(unittest.TestCase):
    def worker(self, stage=None):
        m = Migration.__new__(Migration)
        m.account = "example"
        m.selection = Selection()
        s = {"account": "example", "original_key": "source", "stage": stage}
        m.state = lambda key: s
        m.write = lambda state, **kw: state.update(kw)
        m.api = SimpleNamespace(
            info=Mock(side_effect=AssertionError("Unexpected media request"))
        )
        return m, s

    def test_still_is_default_and_keep_remains_explicit(self):
        self.assertEqual(Selection().live_photos, "still")
        m, s = self.worker()
        m.selection = Selection(live_photos="keep")
        row, _ = fixture(key="source")
        row[-1]["146008172"] = [1]
        self.assertIsNone(m.prepare(row, None))
        self.assertEqual(s["stage"], "live_photo_unchanged")
        m.api.info.assert_not_called()

    def test_completed_live_conversion_is_not_reset_or_downloaded_again(self):
        m, s = self.worker("trashed")
        s["replacement_key"] = "new"
        row, _ = fixture(key="source")
        row[-1]["146008172"] = [1]
        self.assertIsNone(m.prepare(row, None))
        self.assertEqual(s["stage"], "trashed")
        self.assertEqual(s["replacement_key"], "new")
        m.api.info.assert_not_called()

    def test_existing_avif_is_skipped_before_media_requests(self):
        m, s = self.worker()
        row, ext = fixture(key="source", filename="photo.AVIF")
        m.catalog = {"items": {"source": ext}}
        self.assertIsNone(m.prepare(row, None))
        self.assertEqual(s["stage"], "already_avif")
        m.api.info.assert_not_called()

    def test_live_photo_with_video_attributes_is_not_a_standalone_video(self):
        row, ext = fixture(video=True)
        row[-1]["146008172"] = [1]
        self.assertFalse(is_video(row))
        self.assertTrue(Selection(media="images").matches(row, ext))
        self.assertFalse(Selection(media="videos").matches(row, ext))


class LiveBackup(unittest.TestCase):
    def test_both_components_are_backed_up_and_motion_corruption_is_detected(self):
        m = Migration.__new__(Migration)
        m.api = object()
        m.write = lambda state, **kw: state.update(kw)
        with tempfile.TemporaryDirectory() as td:
            s = {"original_key": "source", "original_bytes": 7}
            with patch(
                "migration.originals",
                return_value=[("photo.jpg", b"JPEG"), ("motion.mov", b"MOV")],
            ):
                m.backup_live(s, Path(td) / "original.jpg")
            self.assertEqual(len(s["live_backup_manifest"]), 2)
            m.verify_live_backup(s)
            movie = next(x for x in s["live_backup_manifest"] if x["role"] == "motion")
            Path(movie["path"]).write_bytes(b"BAD")
            with self.assertRaisesRegex(RuntimeError, "backup changed"):
                m.verify_live_backup(s)

    def test_incomplete_export_is_rejected(self):
        m = Migration.__new__(Migration)
        m.api = object()
        with (
            tempfile.TemporaryDirectory() as td,
            patch("migration.originals", return_value=[("photo.jpg", b"JPEG")]),
        ):
            with self.assertRaisesRegex(RuntimeError, "combined original"):
                m.backup_live(
                    {"original_key": "source", "original_bytes": 7},
                    Path(td) / "original.jpg",
                )

    def test_verified_still_cannot_trash_live_source_without_complete_backup(self):
        m, s, _ = test_safety.DeletionGates().setup_case()
        row, _ = fixture()
        row[-1]["146008172"] = [1]
        s.update(
            inventory_row=row,
            live_photo_policy="still",
            download_verified_sha256="expected",
            original_bytes=7,
        )
        with self.assertRaisesRegex(
            RuntimeError, "Complete Live Photo original backup"
        ):
            m.finalize(s)
        self.assertEqual(m.api.trashed, [])


if __name__ == "__main__":
    unittest.main()
