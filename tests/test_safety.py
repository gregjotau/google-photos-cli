import sys, unittest, tempfile, threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from migration import Migration
from photos_api import API, upload


class FakeAPI:
    def __init__(self, info, ext):
        self.info_result = info
        self.ext_result = ext
        self.trashed = []

    def batch(self, _):
        return [self.info_result, self.ext_result]

    def trash(self, keys):
        self.trashed.extend(keys)


class DeletionGates(unittest.TestCase):
    def setup_case(self):
        er = [None] * 20
        er[3] = 100000
        er[4] = 3600000
        er[19] = []
        ne = er.copy()
        ne[2] = "new.avif"
        ne[5] = 100
        info = [["new", None, 100000, "new-dedup"]]
        state = {
            "original_key": "old",
            "replacement_key": "new",
            "original_ext": [er],
            "avif_bytes": 100,
            "avif_sha256": "expected",
            "output": "new.avif",
            "stage": "uploaded",
        }
        migration = Migration.__new__(Migration)
        migration.api = FakeAPI(info, [ne])
        migration.write = lambda state, **kw: state.update(kw)
        return migration, state, ne

    def test_wrong_stored_size_never_trashes(self):
        m, s, ne = self.setup_case()
        ne[5] = 99
        self.assertFalse(m.finalize(s))
        self.assertEqual(m.api.trashed, [])

    def test_changed_original_download_never_trashes(self):
        m, s, _ = self.setup_case()
        with patch(
            "migration.originals", return_value=[("new.avif", b"changed bytes")]
        ):
            self.assertFalse(m.finalize(s))
        self.assertEqual(m.api.trashed, [])


class CommitRecovery(unittest.TestCase):
    def test_upload_intent_is_written_before_remote_commit(self):
        events = []

        class UploadAPI:
            authuser = 0
            write_lock = threading.RLock()

            def rpc(self, rpc, data):
                if rpc == "swbisb":
                    return []
                events.append("commit")
                return [[["token", ["AF1new"]]]]

            def fetch(self, url, method, headers, body):
                if headers.get("x-goog-upload-command") == "start":
                    return {
                        "status": 200,
                        "headers": {
                            "x-goog-upload-status": "active",
                            "x-goog-upload-url": "https://photos.google.com/_/upload/uploadmedia/interactive?upload_id=test",
                        },
                        "body": b"",
                    }
                return {
                    "status": 200,
                    "headers": {"x-goog-upload-status": "final"},
                    "body": b"A" * 32,
                }

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "test.avif"
            path.write_bytes(b"file bytes")
            result = upload(
                UploadAPI(), path, 1000, lambda **kw: events.append("intent")
            )
        self.assertEqual(result, "AF1new")
        self.assertEqual(events, ["intent", "commit"])


class TrashRecovery(unittest.TestCase):
    def test_interrupted_removal_can_be_found_beyond_recent_page(self):
        api = API.__new__(API)
        calls = []

        def rpc(name, data):
            self.assertEqual(name, "zy0IHe")
            calls.append(data[0])
            return (
                [[["unrelated"]], "next-page"]
                if data[0] is None
                else [[["original"]], None]
            )

        api.rpc = rpc
        self.assertEqual(api.in_trash(["original"], exhaustive=True), {"original"})
        self.assertEqual(calls, [None, "next-page"])
        calls.clear()
        self.assertEqual(api.in_trash(["original"]), set())
        self.assertEqual(calls, [None])

    def test_verified_video_removal_reconciles_without_another_cloud_write(self):
        from video_migration import Videos, sha
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.mp4"
            source.write_bytes(b"synthetic backup")
            s = {
                "stage": "trashing",
                "original_key": "old",
                "encoded_sha256": "proof",
                "download_verified_sha256": "proof",
                "playback_ready": True,
                "capture_date_verified": True,
                "albums_preserved": True,
                "source": str(source),
                "original_sha256": sha(source),
            }
            videos = Videos.__new__(Videos)
            videos.api = SimpleNamespace(in_trash=lambda keys, exhaustive: {"old"})
            videos.write = lambda state, **kw: state.update(kw)
            videos.log = lambda *a, **kw: None
            self.assertTrue(videos.finalize(s))
            self.assertEqual(s["stage"], "trashed")


if __name__ == "__main__":
    unittest.main()
