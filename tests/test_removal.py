import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from photos_api import API
from removal import create_plan, execute, pending
from selection import Selection
from test_selection import fixture


class FakeRemovalAPI:
    def __init__(self, ext):
        self.ext = ext
        self.deleted = False
        self.calls = []

    def call(self, task):
        return {"account": "example"}

    def in_trash(self, keys, exhaustive=False):
        return set() if self.deleted else set(keys) & {"old"}

    def trash_items(self, keys, exhaustive=False):
        return {} if self.deleted else {"old": ["old", None, None, "old-dedup"]}

    def batch(self, calls):
        return [
            self.ext
            if name == "fDcn4b"
            else None
            if self.deleted
            else [["old", None, None, "old-dedup"]]
            for name, _ in calls
        ]

    def purge(self, keys, confirmed=False):
        assert confirmed is True
        self.calls.append(keys)
        self.deleted = True


def data(root, mode="permanent"):
    row, ext = fixture(key="old")
    ext[0][5] = 4
    ext[0][19] = []
    ext[0][11] = "old-dedup"
    folder = root / "work-api" / "old"
    folder.mkdir(parents=True)
    src = folder / "original.jpg"
    src.write_bytes(b"JPEG")
    out = folder / "new.avif"
    out.write_bytes(b"AVIF")
    fingerprint = lambda b: hashlib.sha256(b).hexdigest()
    state = {
        "original_key": "old",
        "replacement_key": "new",
        "account": "example",
        "filename": "old.jpg",
        "stage": "trashed",
        "original_ext": ext,
        "original_bytes": 4,
        "source": str(src),
        "original_sha256": fingerprint(b"JPEG"),
        "original_backup_bytes": 4,
        "output": str(out),
        "avif_bytes": 4,
        "avif_sha256": fingerprint(b"AVIF"),
        "download_verified_sha256": fingerprint(b"AVIF"),
    }
    if mode:
        state["deletion_mode"] = mode
    (folder / "state.json").write_text(json.dumps(state))
    (root / "inventory-api.json").write_text(
        json.dumps({"account": "example", "complete": True, "items": [row]})
    )
    (root / "catalog-api.json").write_text(
        json.dumps({"account": "example", "items": {"old": ext}})
    )
    new = json.loads(json.dumps(ext))
    new[0][0] = "new"
    new[0][2] = "new.avif"
    new[0].extend([None] * (31 - len(new[0])))
    new[0][11] = "new-dedup"
    new[0][30] = [1, 4, 2]
    return state, new, folder / "state.json"


class PermanentRemoval(unittest.TestCase):
    def test_no_confirmation_performs_no_io_or_cloud_request(self):
        with self.assertRaises(ValueError):
            execute(Path("nonexistent"), Selection())

    def test_only_explicit_original_key_is_removed_and_backup_remains(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            s, ext, path = data(root)
            api = FakeRemovalAPI(ext)
            self.assertEqual(execute(root, Selection(), confirmed=True, api=api), 1)
            self.assertEqual(api.calls, [["old-dedup"]])
            self.assertTrue(
                json.loads(path.read_text())["original_deleted_permanently"]
            )
            self.assertEqual(Path(s["source"]).read_bytes(), b"JPEG")
            self.assertEqual(pending(root, Selection()), [])

    def test_corrupted_backup_blocks_permanent_deletion(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            s, ext, _ = data(root)
            api = FakeRemovalAPI(ext)
            Path(s["source"]).write_bytes(b"BAD!")
            with self.assertRaisesRegex(RuntimeError, "backup missing or changed"):
                execute(root, Selection(), confirmed=True, api=api)
            self.assertEqual(api.calls, [])

    def test_trashed_source_info_can_be_absent_but_trash_identity_is_required(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, ext, _ = data(root)
            api = FakeRemovalAPI(ext)
            api.batch = lambda calls: [
                ext if name == "fDcn4b" else None for name, _ in calls
            ]
            self.assertEqual(execute(root, Selection(), confirmed=True, api=api), 1)

    def test_changed_trash_deletion_key_blocks_removal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, ext, _ = data(root)
            api = FakeRemovalAPI(ext)
            api.trash_items = lambda *a, **kw: {"old": ["old", None, None, "unrelated"]}
            with self.assertRaisesRegex(RuntimeError, "Source identity"):
                execute(root, Selection(), confirmed=True, api=api)
            self.assertEqual(api.calls, [])

    def test_storage_saver_replacement_blocks_permanent_deletion(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, ext, _ = data(root)
            ext[0][30][2] = 1
            api = FakeRemovalAPI(ext)
            with self.assertRaisesRegex(RuntimeError, "quality/size/date"):
                execute(root, Selection(), confirmed=True, api=api)
            self.assertEqual(api.calls, [])

    def test_trash_and_legacy_journals_are_not_retroactively_purged(self):
        for mode in ["trash", None]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                data(root, mode)
                self.assertEqual(create_plan(root, Selection())["items"], [])

    def test_interrupted_success_is_reconciled_without_repeating_delete(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            s, ext, path = data(root)
            s["permanent_delete_started"] = True
            path.write_text(json.dumps(s))
            api = FakeRemovalAPI(ext)
            api.deleted = True
            execute(root, Selection(), confirmed=True, api=api)
            self.assertEqual(api.calls, [])
            self.assertTrue(
                json.loads(path.read_text())["original_deleted_permanently"]
            )

    def test_year_filter_cannot_include_another_years_original(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data(root)
            self.assertEqual(create_plan(root, Selection(years=[2023]))["items"], [])
            self.assertEqual(
                len(create_plan(root, Selection(years=[2026]))["items"]), 1
            )

    def test_journal_cannot_redirect_a_selected_item_to_another_source(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state, _, path = data(root)
            state["original_key"] = "out-of-scope"
            path.write_text(json.dumps(state))
            with self.assertRaisesRegex(RuntimeError, "identity differs"):
                create_plan(root, Selection())

    def test_low_level_api_requires_confirmation_and_never_empties_trash(self):
        api = API.__new__(API)
        api.rpc = Mock()
        with self.assertRaises(ValueError):
            api.purge(["specific"])
        with self.assertRaises(ValueError):
            api.purge([], confirmed=True)
        api.rpc.assert_not_called()
        api.purge(["specific"], confirmed=True)
        api.rpc.assert_called_once_with("XwAOJf", [None, 2, ["specific"], 2])


if __name__ == "__main__":
    unittest.main()
