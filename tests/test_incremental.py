import json
from pathlib import Path
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import Mock, patch

from removal import IncrementalRemoval, create_plan
from selection import Selection
from workflow import run_batches
import test_removal
from test_selection import fixture


class Backpressure(unittest.TestCase):
    def run_cohorts(self, *, count=7, size=3, stuck=False, fail_delete=False):
        uploaded, deleted, pending, errors = [], [], [], []
        stop = threading.Event()

        def task(row):
            # No item in the next cohort starts while the previous one remains.
            first = int(row[0]) // size * size
            assert all(str(i) in deleted for i in range(first))
            uploaded.append(row[0])
            pending.append(row[0])

        def drain():
            if not stuck:
                pending.clear()

        def remove(keys):
            assert not pending
            if fail_delete:
                raise RuntimeError("deletion failed")
            deleted.extend(keys)

        args = dict(
            task=task,
            drain=drain,
            errors=errors,
            waiting=lambda: pending,
            workers=2,
            batch_size=size,
            after_batch=remove,
            stop_event=stop,
            timeout=0,
            pause=0,
        )
        if fail_delete:
            with self.assertRaisesRegex(RuntimeError, "deletion failed"):
                run_batches([[str(i)] for i in range(count)], **args)
        else:
            run_batches([[str(i)] for i in range(count)], **args)
        return uploaded, deleted, stop

    def test_each_cohort_is_verified_and_deleted_before_next_upload(self):
        uploaded, deleted, stop = self.run_cohorts()
        self.assertEqual(set(uploaded), {str(i) for i in range(7)})
        self.assertEqual(deleted, [str(i) for i in range(7)])
        self.assertFalse(stop.is_set())

    def test_processing_stall_does_not_fill_the_library_with_new_uploads(self):
        uploaded, deleted, stop = self.run_cohorts(stuck=True)
        self.assertEqual(set(uploaded), {"0", "1", "2"})
        self.assertEqual(deleted, [])
        self.assertTrue(stop.is_set())

    def test_deletion_failure_prevents_next_cohort(self):
        uploaded, deleted, stop = self.run_cohorts(fail_delete=True)
        self.assertEqual(set(uploaded), {"0", "1", "2"})
        self.assertEqual(deleted, [])
        self.assertTrue(stop.is_set())


class IncrementalScope(unittest.TestCase):
    def test_no_confirmation_or_trash_mode_never_calls_delete(self):
        with patch("removal.execute") as execute:
            IncrementalRemoval(Path("does-not-exist"), Selection())(["old"])
            IncrementalRemoval(
                Path("does-not-exist"), Selection(delete_mode="trash"), confirmed=True
            )(["old"])
            execute.assert_not_called()

    def test_batch_filter_excludes_other_pending_originals_before_validation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state, _, _ = test_removal.data(root)
            row, ext = fixture(key="other")
            inv = json.loads((root / "inventory-api.json").read_text())
            inv["items"].append(row)
            (root / "inventory-api.json").write_text(json.dumps(inv))
            cat = json.loads((root / "catalog-api.json").read_text())
            cat["items"]["other"] = ext
            (root / "catalog-api.json").write_text(json.dumps(cat))
            folder = root / "work-api/other"
            folder.mkdir()
            state.update(
                original_key="other",
                original_ext=ext,
                source="missing-backup",
                output="missing-output",
            )
            (folder / "state.json").write_text(json.dumps(state))
            plan = create_plan(root, Selection(), source_keys={"old"})
            self.assertEqual([i["source_key"] for i in plan["items"]], ["old"])
            self.assertEqual(
                create_plan(root, Selection(years=[2023]), source_keys={"old"})[
                    "items"
                ],
                [],
            )

    def test_concurrent_callbacks_do_not_delete_the_same_source_twice(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, _, path = test_removal.data(root)
            remove = IncrementalRemoval(root, Selection(), confirmed=True)

            def execute(*args, **kw):
                self.assertEqual(kw["source_keys"], {"old"})
                state = json.loads(path.read_text())
                state["original_deleted_permanently"] = True
                path.write_text(json.dumps(state))
                return 1

            with patch("removal.execute", side_effect=execute) as remote:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    list(pool.map(remove, [["old"], ["old"]]))
                remote.assert_called_once()

    def test_unverified_replacement_is_not_purged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state, _, path = test_removal.data(root)
            state["stage"] = "uploaded"
            path.write_text(json.dumps(state))
            with patch("removal.execute") as execute:
                IncrementalRemoval(root, Selection(), confirmed=True)(["old"])
                execute.assert_not_called()

    def test_combined_command_uses_shared_small_cohorts(self):
        import sys
        import test_reliability

        cli = test_reliability.cli_module()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rows = [fixture(key=str(i), video=bool(i % 2))[0] for i in range(5)]
            inv = dict(account="example", complete=True, items=rows)
            cat = dict(
                account="example", items={r[0]: fixture(key=r[0])[1] for r in rows}
            )
            (root / "inventory-api.json").write_text(json.dumps(inv))
            (root / "catalog-api.json").write_text(json.dumps(cat))
            images = Mock(inventory=inv, catalog=cat)
            videos = Mock()
            images.run.return_value = videos.run.return_value = ([], [])
            with (
                patch.object(
                    sys,
                    "argv",
                    ["cli", "--data-dir", td, "run", "--batch-size", "2", "--apply"],
                ),
                patch("migration.Migration", return_value=images),
                patch("video_migration.Videos", return_value=videos),
                patch.object(cli, "complete_workflow"),
            ):
                cli.main()
            groups = [
                [r[0] for r in c.kwargs["rows"]] for c in images.run.call_args_list
            ]
            self.assertEqual(groups, [["0", "1"], ["2", "3"], ["4"]])
            self.assertEqual(videos.run.call_args_list, images.run.call_args_list)
