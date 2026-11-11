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
