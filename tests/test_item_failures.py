"""Synthetic item deferral tests; no Google account or requests."""
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from converter import atomic_json, digest
from migration import Migration
from video_migration import Videos
from workflow import record_item_failure, run_batches
import test_removal
from test_selection import fixture


class Deferral(unittest.TestCase):
    def worker(self, root, *, enabled=True):
        def write(state, **changes):
            state.update(changes)
            path = root / 'work-api' / state['original_key'] / 'state.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(path, state)
        return SimpleNamespace(account='example', continue_on_item_error=enabled,
                               deferred=[], errors=[], write=write, log=Mock())

    def unavailable(self, root):
        atomic_json(root / 'inventory-api.json', dict(account='example', items=[['old']]))
        return dict(original_key='old', account='example', deletion_mode='permanent')

    def record(self, worker, root, state, error):
        return record_item_failure(worker, root, state, RuntimeError(error), 'item_error',
                                   key='old', folder='work-api')

    def test_unavailable_source_is_logged_without_claiming_backup_or_completion(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); state=self.unavailable(root); worker=self.worker(root)
            self.assertTrue(self.record(worker, root, state, 'Original source is unavailable'))
            self.assertEqual(worker.deferred, ['old']); self.assertFalse(worker.errors)
            ledger=json.loads((root/'deferred-items.json').read_text())
            self.assertFalse(ledger['items']['old']['backup_verified'])
            self.assertFalse(ledger['items']['old']['source_removal_attempted'])
            self.assertEqual(ledger['items']['old']['journal_sha256'], digest(root/'work-api/old/state.json'))
            self.assertNotIn('stage', state)

    def test_date_failure_preserves_original_and_verifies_backup_and_output(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); state,_,journal=test_removal.data(root)
            output=root/'candidate.avif'; output.write_bytes(b'candidate')
            state.update(stage='uploaded', output=str(output), avif_sha256=digest(output))
            worker=self.worker(root)
            self.assertTrue(self.record(worker,root,state,'Capture date repair did not verify'))
            self.assertEqual(state['stage'],'uploaded')
            self.assertFalse(state.get('original_deleted_permanently'))
            self.assertTrue(json.loads((root/'deferred-items.json').read_text())['items']['old']['backup_verified'])

    def test_corrupt_backups_and_outputs_never_get_deferred(self):
        for corrupt in ('source','output'):
            with self.subTest(corrupt=corrupt), tempfile.TemporaryDirectory() as td:
                root=Path(td); state,_,_=test_removal.data(root)
                output=root/'candidate'; output.write_bytes(b'candidate')
                state.update(stage='uploaded', output=str(output), avif_sha256=digest(output))
                Path(state[corrupt]).write_bytes(b'corrupt')
                with self.assertRaises(RuntimeError):
                    self.record(self.worker(root),root,state,'Capture date repair did not verify')
                self.assertFalse((root/'deferred-items.json').exists())

    def test_default_unknown_identity_account_and_deletion_failures_stay_fatal(self):
        for enabled,message,stage in (
            (False,'Original source is unavailable',None),
            (True,'Account changed',None),
            (True,'Original info identity mismatch',None),
            (True,'Unexpected schema',None),
            (True,'Original source is unavailable','trashing'),
            (True,'Video capture date/zone mismatch','trashed'),
            (True,'TypeError: Failed to fetch','committing'),
        ):
            with self.subTest(message=message,stage=stage), tempfile.TemporaryDirectory() as td:
                root=Path(td); state=self.unavailable(root); state['stage']=stage
                worker=self.worker(root,enabled=enabled)
                self.assertFalse(self.record(worker,root,state,message))
                self.assertEqual(worker.errors,['old']); self.assertFalse(worker.deferred)
                self.assertFalse((root/'deferred-items.json').exists())

    def test_ambiguous_commit_is_deferred_without_any_upload_or_stage_reset(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); state,_,_=test_removal.data(root)
            output=root/'candidate'; output.write_bytes(b'candidate')
            state.update(stage='committing',replacement_key=None,output=str(output),avif_sha256=digest(output))
            with patch('photos_api.upload', side_effect=AssertionError('No upload allowed')):
                self.assertTrue(self.record(self.worker(root),root,state,'Commit outcome unknown; reconcile using hash'))
            self.assertEqual(state['stage'],'committing')
            self.assertIsNone(state['replacement_key'])

    def test_wrong_ledger_account_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); state=self.unavailable(root); ledger=root/'deferred-items.json'
            atomic_json(ledger,dict(account='foreign',items={}))
            before=ledger.read_bytes()
            with self.assertRaisesRegex(RuntimeError,'ledger account'):
                self.record(self.worker(root),root,state,'Original source is unavailable')
            self.assertEqual(ledger.read_bytes(),before)

    def test_deferral_does_not_skip_successful_sibling_cleanup_or_next_cohort(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); state=self.unavailable(root); worker=self.worker(root)
            stop=threading.Event();done=[];cleaned=[]
            def task(row):
                if row[0]=='old':self.record(worker,root,state,'Original source is unavailable')
                else:done.append(row[0])
            def cleanup(keys):cleaned.append(list(keys))
            run_batches([['old'],['good'],['later']],task=task,drain=lambda:None,
                        errors=worker.errors,waiting=lambda:[],workers=1,batch_size=2,
                        after_batch=cleanup,stop_event=stop,timeout=0,pause=0)
            self.assertEqual(done,['good','later'])
            self.assertEqual(cleaned,[['old','good'],['later']])
            self.assertFalse(stop.is_set())

    def test_empty_info_is_distinct_from_a_foreign_source_identity(self):
        for info,message in ((None,'Original source is unavailable'),([['foreign']],'identity mismatch')):
            with self.subTest(info=info), tempfile.TemporaryDirectory() as td:
                root=Path(td); row,ext=fixture(); work=root/'work-api';work.mkdir()
                worker=Migration.__new__(Migration);worker.account='example'
                worker.selection=SimpleNamespace(live_photos='still',delete_mode='permanent')
                worker.catalog=dict(items={'old':ext}); worker.api=Mock(); worker.api.info.return_value=info
                worker.state=lambda key:dict(original_key=key,account='example')
                worker.write=lambda state,**kw:state.update(kw)
                with patch('migration.WORK',work), self.assertRaisesRegex(RuntimeError,message):
                    worker.prepare(row,None)

    def test_real_image_and_video_runners_continue_only_when_opted_in(self):
        for video in (False, True):
            for enabled in (False, True):
                with self.subTest(video=video,enabled=enabled), tempfile.TemporaryDirectory() as td:
                    root=Path(td); rows=[fixture(key=k,video=video)[0] for k in ('old','good')]
                    atomic_json(root/'inventory-api.json',dict(account='example',items=rows))
                    kind=Videos if video else Migration; worker=kind.__new__(kind)
                    worker.account='example';worker.continue_on_item_error=enabled
                    worker.deferred=[];worker.errors=[];worker.waiting=[];worker.stop_event=threading.Event()
                    worker.log=Mock();worker.drain=lambda:None;worker.after_batch=Mock()
                    worker.workers=1;worker.batch_size=1;worker.wait_lock=threading.Lock();worker.lock=threading.Lock()
                    calls=[]
                    def process(row,*args):
                        calls.append(row[0])
                        if row[0]=='old':raise RuntimeError('Original source is unavailable')
                    module='video_migration' if video else 'migration'
                    if video:worker.process=process
                    else:worker.prepare=process
                    with patch(module+'.ROOT',root),patch(module+'.WORK',root/('work-video' if video else 'work-api')):
                        worker.run(rows=rows)
                    self.assertEqual(calls,['old','good'] if enabled else ['old'])
                    self.assertEqual(worker.stop_event.is_set(),not enabled)
                    self.assertEqual(worker.deferred,['old'] if enabled else [])

    def test_combined_run_finishes_other_cohorts_but_exits_incomplete(self):
        import contextlib,io,sys
        import test_reliability
        cli=test_reliability.cli_module()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);rows=[fixture(key=str(i))[0] for i in range(3)]
            inv=dict(account='example',complete=True,items=rows)
            cat=dict(account='example',items={r[0]:fixture(key=r[0])[1] for r in rows})
            atomic_json(root/'inventory-api.json',inv);atomic_json(root/'catalog-api.json',cat)
            images=SimpleNamespace(inventory=inv,catalog=cat,deferred=['0'],run=Mock(return_value=([],[])))
            videos=SimpleNamespace(deferred=[],run=Mock(return_value=([],[])))
            with patch.object(sys,'argv',['cli','--data-dir',td,'run','--year','2026','--apply','--batch-size','1','--continue-on-item-error']),patch('migration.Migration',return_value=images),patch('video_migration.Videos',return_value=videos),patch.object(cli,'complete_workflow') as complete,contextlib.redirect_stdout(io.StringIO()) as out:
                with self.assertRaises(SystemExit) as result:cli.main()
            self.assertEqual(result.exception.code,2);complete.assert_not_called()
            self.assertEqual(images.run.call_count,3)
            self.assertFalse(json.loads(out.getvalue())['complete'])
