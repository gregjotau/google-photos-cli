import json
from contextlib import contextmanager
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from migration import Migration
from selection import Selection
from test_selection import fixture


class FreshAuditScope(unittest.TestCase):
    def audit_fixture(self, root, *, selection, missing=()):
        import audit_library

        source, source_ext = fixture(key="source")
        retained, retained_ext = fixture(key="retained", video=True)
        outside, outside_ext = fixture(key="outside", captured="2024-06-01T00:00:00+00:00")
        replacement, replacement_ext = fixture(key="replacement", filename="photo-avif.avif")
        other_replacement, other_ext = fixture(key="other-replacement", filename="other.avif", captured="2024-06-01T00:00:00+00:00")
        arrival, arrival_ext = fixture(key="new-arrival")
        for ext in (source_ext, replacement_ext, outside_ext, other_ext):
            ext[0][19] = []
        for ext in (replacement_ext, other_ext):
            ext[0][5] = 100
            ext[0].extend([None] * (31 - len(ext[0])))
            ext[0][30] = [1, 100, 2]
        original = dict(account="example", complete=True, items=[source, retained, outside])
        before = dict(account="example", items={"source": source_ext, "retained": retained_ext, "outside": outside_ext})
        fresh = dict(account="example", complete=True, items=[r for r in (replacement, retained, outside, other_replacement, arrival) if r[0] not in missing])
        metadata = {"replacement": replacement_ext, "retained": retained_ext, "outside": outside_ext, "other-replacement": other_ext, "new-arrival": arrival_ext}
        (root / "inventory-api.json").write_text(json.dumps(original))
        (root / "catalog-api.json").write_text(json.dumps(before))
        for key, state, folder in (
            ("source", dict(stage="trashed", replacement_key="replacement", original_ext=source_ext, avif_bytes=100, avif_sha256="proof", download_verified_sha256="proof"), "work-api"),
            ("retained", dict(stage="already_efficient"), "work-video"),
            ("outside", dict(stage="trashed", replacement_key="other-replacement", original_ext=outside_ext, avif_bytes=100, avif_sha256="proof", download_verified_sha256="proof"), "work-api"),
        ):
            directory = root / folder / key
            directory.mkdir(parents=True)
            (directory / "state.json").write_text(json.dumps(dict(account="example", original_key=key, **state)))
        scanned = []
        cataloged = []

        def scan(*args):
            scanned.append(args)
            return fresh

        def catalog(_root, _api, inventory, destination):
            keys = [r[0] for r in inventory["items"]]
            cataloged.extend(keys)
            result = dict(account="example", items={k: metadata[k] for k in keys})
            (root / destination).write_text(json.dumps(result))
            return result

        cli = SimpleNamespace(scan=scan, catalog=catalog)
        spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module: None))
        with (
            patch("audit_library.API", return_value=object()),
            patch("importlib.util.spec_from_file_location", return_value=spec),
            patch("importlib.util.module_from_spec", return_value=cli),
            patch("audit_library.original_files", side_effect=AssertionError("No media export expected")),
        ):
            result = audit_library.audit(root, verbose=False, selection=selection)
        return result, scanned, set(cataloged), fresh

    def test_scoped_audit_refreshes_sources_replacements_and_unknown_arrivals(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result, scanned, cataloged, fresh = self.audit_fixture(root, selection=Selection(years=[2026]))
            self.assertTrue(result["complete"], result)
            self.assertEqual(len(scanned), 1)
            self.assertEqual(scanned[0][2], "audit-inventory.json")
            self.assertEqual(len(fresh["items"]), 5)
            self.assertEqual(cataloged, {"replacement", "retained", "new-arrival"})
            saved = json.loads((root / "audit-catalog.json").read_text())
            self.assertIn("new-arrival", saved["items"])
            # Arrival year classification still uses its fresh timestamp and offset.
            self.assertTrue(Selection(years=[2026]).matches(fresh["items"][-1], saved["items"]["new-arrival"]))

    def test_scoped_audit_still_detects_absent_replacement_or_retained_source(self):
        for absent in ("replacement", "retained"):
            with self.subTest(absent=absent), tempfile.TemporaryDirectory() as td:
                result, _, cataloged, _ = self.audit_fixture(Path(td), selection=Selection(years=[2026]), missing={absent})
                self.assertFalse(result["media_complete"])
                self.assertTrue(any(p["problem"] == "original and expected replacement do not reconcile" for p in result["journal_problems"]))
                self.assertNotIn(absent, cataloged)

    def test_unfiltered_audit_still_catalogs_every_active_item(self):
        with tempfile.TemporaryDirectory() as td:
            _, scanned, cataloged, fresh = self.audit_fixture(Path(td), selection=Selection())
            self.assertEqual(len(scanned), 1)
            self.assertEqual(cataloged, {r[0] for r in fresh["items"]})


class MetadataBatching(unittest.TestCase):
    def test_verification_uses_forty_rpc_batches_and_passes_results_per_item(self):
        m = Migration.__new__(Migration)
        m.wait_lock = threading.Lock()
        m.waiting = [
            dict(
                original_key=f"old{i}",
                replacement_key=f"new{i}",
                avif_sha256="proof",
                download_verified_sha256="proof",
                original_ext=[[None] * 20],
            )
            for i in range(25)
        ]
        m.errors = []
        calls = []

        def batch(group):
            calls.append(group)
            return [[entry[1][0]] for entry in group]

        m.api = SimpleNamespace(batch=batch)
        m.log = lambda *a, **kw: None
        checked = []

        def finalize(state, defer, values):
            self.assertTrue(defer)
            self.assertEqual(
                values, [[state["replacement_key"]], [state["replacement_key"]]]
            )
            checked.append(state["original_key"])
            return False

        m.finalize = finalize
        m.drain()
        self.assertEqual([len(c) for c in calls], [40, 10])
        self.assertEqual(len(checked), 25)
        self.assertEqual(len(m.waiting), 25)


class ScopedAudit(unittest.TestCase):
    def test_empty_export_cannot_validate_an_untracked_avif(self):
        import audit_library

        row, ext = fixture(filename="already.avif")
        inv = dict(account="example", complete=True, items=[row])
        cat = dict(account="example", items={row[0]: ext})
        cli = SimpleNamespace(scan=lambda *a: inv, catalog=lambda *a: cat)
        spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module: None))

        @contextmanager
        def empty_export(*args):
            yield []

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "inventory-api.json").write_text(json.dumps(inv))
            (root / "catalog-api.json").write_text(json.dumps(cat))
            with (
                patch("audit_library.API", return_value=object()),
                patch("importlib.util.spec_from_file_location", return_value=spec),
                patch("importlib.util.module_from_spec", return_value=cli),
                patch("audit_library.original_files", empty_export),
            ):
                result = audit_library.audit(root, verbose=False)
            self.assertFalse(result["complete"])
            self.assertEqual(result["non_avif_images"][0]["key"], row[0])

    def test_filtered_success_does_not_claim_other_years_are_converted(self):
        import audit_library

        outside, outside_ext = fixture(
            key="outside", captured="2024-06-01T00:00:00+00:00"
        )
        original, original_ext = fixture(key="original")
        replacement, replacement_ext = fixture(
            key="replacement", filename="IMG_1234-avif.avif"
        )
        replacement_ext[0][5] = 100
        replacement_ext[0].extend([None] * (31 - len(replacement_ext[0])))
        replacement_ext[0][30] = [1, 100, 2]
        original_ext[0][19] = replacement_ext[0][19] = []
        source = {"account": "example", "complete": True, "items": [outside, original]}
        before = {
            "account": "example",
            "items": {"outside": outside_ext, "original": original_ext},
        }
        fresh = {
            "account": "example",
            "complete": True,
            "items": [outside, replacement],
        }
        after = {
            "account": "example",
            "items": {"outside": outside_ext, "replacement": replacement_ext},
        }
        cli = SimpleNamespace(scan=lambda *a: fresh, catalog=lambda *a: after)
        fake_spec = SimpleNamespace(
            loader=SimpleNamespace(exec_module=lambda module: None)
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "inventory-api.json").write_text(json.dumps(source))
            (root / "catalog-api.json").write_text(json.dumps(before))
            folder = root / "work-api" / "original"
            folder.mkdir(parents=True)
            (folder / "state.json").write_text(
                json.dumps(
                    {
                        "account": "example",
                        "original_key": "original",
                        "replacement_key": "replacement",
                        "stage": "trashed",
                        "original_ext": original_ext,
                        "avif_bytes": 100,
                        "avif_sha256": "proof",
                        "download_verified_sha256": "proof",
                    }
                )
            )
            with (
                patch("audit_library.API", return_value=object()),
                patch("importlib.util.spec_from_file_location", return_value=fake_spec),
                patch("importlib.util.module_from_spec", return_value=cli),
            ):
                scoped = audit_library.audit(
                    root, verbose=False, selection=Selection(years=[2026])
                )
                full = audit_library.audit(root, verbose=False)

                @contextmanager
                def changed_export(*args):
                    path = root / "changed.avif"
                    path.write_bytes(b"different contents")
                    yield [("changed.avif", path)]

                with patch("audit_library.original_files", changed_export):
                    deep = audit_library.audit(
                        root,
                        verbose=False,
                        selection=Selection(years=[2026]),
                        verify_exports=True,
                    )
            self.assertTrue(scoped["complete"], scoped)
            self.assertEqual(scoped["scope"], "filtered_selection")
            self.assertEqual(scoped["avif_images"], 1)
            self.assertFalse(full["complete"])
            self.assertFalse(deep["complete"])
            self.assertIn(
                "fresh original export hash mismatch",
                [p["problem"] for p in deep["journal_problems"]],
            )
            self.assertEqual(full["non_avif_images"][0]["key"], "outside")


if __name__ == "__main__":
    unittest.main()
