import concurrent.futures
import io
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from converter import backup_file, digest
from migration import Migration
from photos_api import original_files, upload
from removal import verify_backups
from selection import Selection
from video_migration import Videos
import test_removal


class Recovery(unittest.TestCase):
    def test_ambiguous_commit_never_starts_another_upload(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "output.avif"
            path.write_bytes(b"encoded")
            api = SimpleNamespace(rpc=Mock(return_value=[]), fetch=Mock())
            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                upload(api, path, 0, reconcile_only=True)
            api.fetch.assert_not_called()
            api.rpc.assert_called_once()

    def test_ambiguous_commit_can_recover_by_hash_without_upload(self):
        from photos_api import sha1_b64

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "output.avif"
            path.write_bytes(b"encoded")
            api = SimpleNamespace(
                rpc=Mock(return_value=[[[sha1_b64(b"encoded"), ["AF1recovered"]]]]),
                fetch=Mock(),
            )
            self.assertEqual(upload(api, path, 0, reconcile_only=True), "AF1recovered")
            api.fetch.assert_not_called()

    def test_image_reconciliation_failure_keeps_committing_stage(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "output.avif"
            path.write_bytes(b"encoded")
            s = dict(
                stage="committing",
                output=str(path),
                avif_sha256=digest(path),
                original_ext=[[None, None, None, 0]],
            )
            worker = Migration.__new__(Migration)
            worker.api = SimpleNamespace(rpc=Mock(return_value=[]), fetch=Mock())
            worker.write = lambda state, **kw: state.update(kw)
            future = concurrent.futures.Future()
            future.set_result(dict(avif_sha256=digest(path)))
            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                worker.commit(s, future)
            self.assertEqual(s["stage"], "committing")
            worker.api.fetch.assert_not_called()

    def test_video_rejects_foreign_journal_before_terminal_skip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "old").mkdir()
            (root / "old/state.json").write_text(
                json.dumps(dict(account="other", stage="trashed"))
            )
            worker = Videos.__new__(Videos)
            worker.account = "expected"
            with (
                patch("video_migration.WORK", root),
                self.assertRaisesRegex(RuntimeError, "account mismatch"),
            ):
                worker.process(["old"])

    def test_partial_backup_cannot_replace_an_existing_good_backup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src, dst = root / "src", root / "dst"
            src.write_bytes(b"new bytes")
            dst.write_bytes(b"good backup")

            def interrupt(source, target, size):
                target.write(b"partial")
                raise OSError("disk full")

            with (
                patch("shutil.copyfileobj", side_effect=interrupt),
                self.assertRaises(OSError),
            ):
                backup_file(src, dst)
            self.assertEqual(dst.read_bytes(), b"good backup")
            backup_file(src, dst)
            self.assertEqual(dst.read_bytes(), b"new bytes")

    def test_backup_verification_is_offline_and_finds_corruption(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            s, _, _ = test_removal.data(root)
            with patch("removal.API", side_effect=AssertionError("No cloud access")):
                self.assertTrue(verify_backups(root, Selection())["complete"])
                Path(s["source"]).write_bytes(b"oops")
                result = verify_backups(root, Selection())
                self.assertFalse(result["complete"])
                self.assertEqual(result["cloud_requests"], 0)


class StreamingExport(unittest.TestCase):
    def test_zip_entries_stream_to_generated_names_and_are_cleaned_up(self):
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, "w") as z:
            z.writestr("../../outside.mp4", b"video" * 1000)

        class Response(io.BytesIO):
            status = 200

            def read(self, size=-1):
                if size < 0:
                    raise AssertionError("Unbounded network read")
                return super().read(size)

        api = SimpleNamespace(
            rpc=Mock(
                return_value=["https://video-downloads.googleusercontent.com/export"]
            )
        )
        with (
            patch("urllib.request.urlopen", return_value=Response(blob.getvalue())),
            patch.object(
                zipfile.ZipFile,
                "read",
                side_effect=AssertionError("No full-entry RAM extraction"),
            ),
        ):
            with original_files(api, ["synthetic"]) as files:
                name, path = files[0]
                self.assertEqual(name, "../../outside.mp4")
                self.assertEqual(path.name, "0")
                self.assertEqual(path.read_bytes(), b"video" * 1000)
            self.assertFalse(path.exists())

    def test_unapproved_export_host_fails_before_download(self):
        api = SimpleNamespace(rpc=Mock(return_value=["https://example.invalid/export"]))
        with (
            patch("urllib.request.urlopen") as network,
            self.assertRaisesRegex(RuntimeError, "host"),
        ):
            with original_files(api, ["synthetic"]):
                pass
        network.assert_not_called()


def cli_module():
    spec = importlib.util.spec_from_file_location(
        "review_cli", Path(__file__).parents[1] / "google-photos-cli.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InventoryRecovery(unittest.TestCase):
    def test_append_preserves_originals_and_skips_replacements_idempotently(self):
        cli = cli_module()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, _, journal = test_removal.data(root)
            state = json.loads(journal.read_text())
            state["superseded_replacements"] = [{"key": "older-replacement"}]
            journal.write_text(json.dumps(state))
            from test_selection import fixture

            fresh = dict(
                account="example",
                complete=True,
                items=[
                    fixture(key=k)[0] for k in ("new", "older-replacement", "arrival")
                ],
            )
            api = SimpleNamespace(call=Mock(return_value={"account": "example"}))
            with patch.object(cli, "scan", return_value=fresh):
                first = cli.append_inventory(root, api)
                second = cli.append_inventory(root, api)
            self.assertEqual([r[0] for r in first["items"]], ["old", "arrival"])
            self.assertEqual(second["last_appended_items"], 0)

    def test_pending_commit_blocks_append_before_network(self):
        cli = cli_module()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, _, journal = test_removal.data(root)
            state = json.loads(journal.read_text())
            state["stage"] = "committing"
            journal.write_text(json.dumps(state))
            api = SimpleNamespace(call=Mock())
            with self.assertRaisesRegex(RuntimeError, "pending conversions"):
                cli.append_inventory(root, api)
            api.call.assert_not_called()

    def test_catalog_short_batch_is_not_marked_complete(self):
        cli = cli_module()
        from test_selection import fixture

        row, _ = fixture()
        api = SimpleNamespace(
            call=lambda task: {"account": "example"}, batch=lambda group: []
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaisesRegex(RuntimeError, "Incomplete metadata batch"):
                cli.catalog(
                    root, api, dict(account="example", complete=True, items=[row])
                )
            self.assertEqual(
                json.loads((root / "catalog-api.json").read_text())["items"], {}
            )

    def test_catalog_checkpoints_avoid_rewriting_entire_catalog_per_batch(self):
        cli = cli_module()
        from test_selection import fixture

        rows = [fixture(key=f"item{i}")[0] for i in range(2001)]
        api = SimpleNamespace(
            call=lambda task: {"account": "example"},
            batch=lambda group: [fixture(key=data[0])[1] for _, data in group],
        )
        with tempfile.TemporaryDirectory() as td, patch("migration.save") as checkpoint:
            result = cli.catalog(Path(td), api, dict(account="example", items=rows))
            self.assertEqual(len(result["items"]), 2001)
            self.assertEqual(checkpoint.call_count, 3)
