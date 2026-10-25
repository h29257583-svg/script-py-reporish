"""Regression and generated-media tests. No YouTube login or Telegram message."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import yaml

from shift_plan import plan, STEP
from chain_shift import dispatch_body
from notify_telegram import chunks
from publish_recordings import Publisher, PublishError, fingerprints, select_media, split_for_release, validate_gofile


class ScheduleTests(unittest.TestCase):
    def test_eight_and_nine_hours_have_four_overlapping_windows(self):
        for hours in (8, 9):
            windows = []
            index, now = 0, 0
            while True:
                result = plan(0, hours, now, index)
                windows.append((now, result['window_deadline']))
                if not result['chain']: break
                index, now = result['next_index'], result['next_at']
            self.assertEqual([a for a,b in windows], [0,7200,14400,21600])
            self.assertEqual(windows[-1][1], hours*3600)
            self.assertTrue(all(a[1]-b[0] == 3600 for a,b in zip(windows,windows[1:])))
            self.assertTrue(all(b-a <= 10800 for a,b in windows))

    def test_any_manual_start_has_same_relative_schedule(self):
        for start in (19*3600, 20*3600+1800, 22*3600, 24*3600):
            first = plan(start, 9, start)
            self.assertEqual(first['deadline'], start+9*3600)
            self.assertEqual(first['next_at'], start+2*3600)
            self.assertEqual(plan(start, 9, start+6*3600, 3)['window_deadline'], start+9*3600)

    def test_delayed_window_never_extends_its_original_end(self):
        result = plan(0, 9, 3*3600, 1)
        self.assertEqual(result['duration_hours'], 2)
        self.assertEqual(result['window_deadline'], 5*3600)

    def test_expired_duplicate_is_skipped(self):
        result = plan(0, 9, 3*3600+1, 0)
        self.assertFalse(result['active'])
        self.assertFalse(result['chain'])

    def test_session_deadline_truncates_last_window(self):
        result = plan(0, 8, 6*3600, 3)
        self.assertEqual(result['duration_hours'], 2)
        self.assertFalse(result['chain'])

    def test_bad_inputs_rejected(self):
        for value in [0,-1,9.1,18,float('nan'),float('inf')]:
            with self.assertRaises(ValueError): plan(0,value,0)

    def test_dispatch_retains_backup_choice(self):
        with patch.dict(os.environ, {'WORKFLOW_REF':'candidate','TOTAL_HOURS':'9',
                                    'SESSION_START':'1000','SESSION_ID':'123','NEXT_INDEX':'1','RELEASE_BACKUP':'true'}):
            body = dispatch_body()
        self.assertEqual(body['inputs']['release_backup'],'true')
        self.assertEqual(body['inputs']['session_start'],'1000')
        self.assertEqual(body['inputs']['total_hours'],'9')

    def test_short_test_needs_no_second_shift(self):
        self.assertFalse(plan(0,.1,0)['chain'])
        with self.assertRaises(ValueError): plan(0,9,8*3600,4)


class UploadTests(unittest.TestCase):
    def test_rclone_copy_and_hash_contract_with_local_backend(self):
        # Real CLI/file transfer, without claiming Google OAuth/Drive validation.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); config = root/'rclone.conf'; config.write_text('[drive]\ntype = local\n')
            source = root/'source.ts'; source.write_bytes(b'owned binary fixture\x00' * 1000)
            env = {'RCLONE_CONFIG': str(config), 'GDRIVE_REMOTE': 'drive:'+str(root/'remote')}
            with patch.dict(os.environ, env):
                pub = Publisher(root, root/'report.json', 1800)
                receipt = pub.google_drive(source, fingerprints(source))
            returned = next((root/'remote').rglob('*.ts'))
            self.assertEqual(returned.read_bytes(), source.read_bytes())
            self.assertEqual(receipt['verification'], 'remote_size_and_md5')
            self.assertTrue(source.exists())

    def test_drive_checksum_mismatch_is_not_success(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); config = root/'rclone.conf'; config.write_text('test config')
            source = root/'fixture.ts'; source.write_bytes(b'fixture')
            with patch.dict(os.environ, {'RCLONE_CONFIG': str(config)}):
                pub = Publisher(root, root/'report.json', 1800)
            results = [subprocess.CompletedProcess([],0,'',''), subprocess.CompletedProcess([],0,json.dumps({'Size':7,'Hashes':{'md5':'wrong'}}),'')]
            with patch('publish_recordings.subprocess.run', side_effect=results):
                with self.assertRaises(PublishError): pub.google_drive(source, fingerprints(source))

    def test_drive_failure_falls_back_without_deleting_source(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root/'fixture.ts'; source.write_bytes(b'fixture')
            pub = Publisher(root, root/'report.json', 1800); pub.drive_config = 'test-config'
            with patch('publish_recordings.media_ok',return_value=True), \
                 patch.object(pub,'google_drive',side_effect=PublishError('drive_failed')), \
                 patch.object(pub,'gofile',return_value={'provider':'gofile','url':'https://gofile.io/d/test'}):
                self.assertEqual(pub.run(),0)
            report = json.loads((root/'report.json').read_text())
            self.assertTrue(report['files'][0]['drive_attempt_failed'])
            self.assertTrue(source.exists())

    def test_backup_mode_tries_gofile_once(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root/'capture.ts'; source.write_bytes(b'fixture')
            pub = Publisher(root, root/'report.json', 1800, release_fallback=True)
            with patch('publish_recordings.subprocess.run', return_value=subprocess.CompletedProcess([], 22, '', '')) as run, \
                 patch('publish_recordings.time.sleep') as sleep:
                with self.assertRaises(PublishError): pub.gofile(source, fingerprints(source))
            self.assertEqual(run.call_count, 1)
            sleep.assert_not_called()

    def test_backup_reserve_skips_gofile_when_little_time_remains(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root/'capture.ts'; source.write_bytes(b'fixture')
            pub = Publisher(root, root/'report.json', 590, release_fallback=True)
            with patch('publish_recordings.subprocess.run') as run:
                with self.assertRaises(PublishError): pub.gofile(source, fingerprints(source))
            run.assert_not_called()

    def test_gofile_attempt_allows_ten_minutes(self):
        with tempfile.TemporaryDirectory() as d, patch('publish_recordings.time.monotonic', return_value=1000):
            root = Path(d); source = root/'capture.ts'; source.write_bytes(b'fixture')
            meta = fingerprints(source)
            response = {'status': 'ok', 'data': {'size': meta['bytes'], 'md5': meta['md5'],
                                                'downloadPage': 'https://gofile.io/d/test'}}
            pub = Publisher(root, root/'report.json', 1800)
            with patch('publish_recordings.subprocess.run', return_value=subprocess.CompletedProcess([], 0, json.dumps(response), '')) as run:
                pub.gofile(source, meta)
            args = run.call_args.args[0]
            self.assertEqual(float(args[args.index('--max-time') + 1]), 600)
            self.assertEqual(run.call_args.kwargs['timeout'], 602)

    def test_gofile_attempt_still_obeys_remaining_global_budget(self):
        with tempfile.TemporaryDirectory() as d, patch('publish_recordings.time.monotonic', return_value=1000):
            root = Path(d); source = root/'capture.ts'; source.write_bytes(b'fixture')
            meta = fingerprints(source)
            response = {'status': 'ok', 'data': {'size': meta['bytes'], 'md5': meta['md5'],
                                                'downloadPage': 'https://gofile.io/d/test'}}
            pub = Publisher(root, root/'report.json', 37)
            with patch('publish_recordings.subprocess.run', return_value=subprocess.CompletedProcess([], 0, json.dumps(response), '')) as run:
                pub.gofile(source, meta)
            args = run.call_args.args[0]
            self.assertEqual(float(args[args.index('--max-time') + 1]), 37)

    def test_checksum_mismatch_is_failure(self):
        metadata = {'bytes': 10, 'md5': 'abcd'}
        response = {'status': 'ok', 'data': {'size': 10, 'md5': 'wrong', 'downloadPage': 'https://gofile.io/d/test'}}
        with self.assertRaises(PublishError):
            validate_gofile(response, metadata)

    def test_bad_download_link_is_failure(self):
        for url in ['https://gofile.io.evil.invalid/d/test', 'http://gofile.io/d/test', None]:
            with self.assertRaises(PublishError):
                validate_gofile({'status': 'ok', 'data': {'size': 10, 'md5': 'abcd', 'downloadPage': url}},
                                {'bytes': 10, 'md5': 'abcd'})

    def test_missing_recording_fails(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(Publisher(d, Path(d)/'report.json', 10).run(), 1)

    def test_nested_and_short_exit_chunks_not_silently_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'chunks').mkdir()
            for name in ['final.ts', 'chunks/tail_short_exit_01.ts', 'chunks/recovered.part']:
                (root/name).write_bytes(b'fixture')
            self.assertEqual(len(select_media(root)), 3)

    def test_failed_upload_keeps_original_and_fails_run(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root/'capture.ts'
            source.write_bytes(b'fixture')
            pub = Publisher(root, root/'report.json', 10)
            with patch('publish_recordings.media_ok', return_value=True), \
                 patch.object(pub, 'gofile', side_effect=PublishError('network_failed')):
                result = pub.run()
            self.assertEqual(result, 1)
            self.assertEqual(source.read_bytes(), b'fixture')
            self.assertFalse(json.loads((root/'report.json').read_text())['all_uploaded'])

    def test_success_receipt_and_original_are_retained(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root/'capture.ts'
            source.write_bytes(b'fixture')
            pub = Publisher(root, root/'report.json', 10)
            with patch('publish_recordings.media_ok', return_value=True), \
                 patch.object(pub, 'gofile', return_value={'provider': 'gofile', 'url': 'https://gofile.io/d/test'}):
                self.assertEqual(pub.run(), 0)
            self.assertTrue(source.exists())
            self.assertTrue(json.loads((root/'report.json').read_text())['all_uploaded'])

    def test_expired_upload_budget_keeps_original(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'capture.ts').write_bytes(b'fixture')
            pub = Publisher(root, root/'report.json', 10)
            pub.end = 0
            self.assertEqual(pub.run(), 1)
            self.assertTrue((root/'capture.ts').exists())

    def test_verified_guest_account_is_reused(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root/'capture.ts'; source.write_bytes(b'fixture')
            meta = fingerprints(source)
            response = {'status': 'ok', 'data': {'size': meta['bytes'], 'md5': meta['md5'],
                        'downloadPage': 'https://gofile.io/d/test', 'guestToken': 'dummy-guest', 'parentFolder': 'dummy-folder'}}
            completed = subprocess.CompletedProcess([], 0, json.dumps(response), '')
            pub = Publisher(root, root/'report.json', 10)
            with patch.dict(os.environ, {}, clear=True), patch('publish_recordings.subprocess.run', return_value=completed) as call:
                pub.gofile(source, meta)
                pub.gofile(source, meta)
            self.assertIn('Authorization: Bearer dummy-guest', call.call_args.kwargs['input'])
            self.assertIn('folderId=dummy-folder', call.call_args.args[0])

    def test_size_split_with_real_ffmpeg_preserves_every_video_frame(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root/'generated.ts'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=25',
                            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '8',
                            '-c:v', 'mpeg2video', '-g', '12', '-q:v', '3', '-c:a', 'mp2', str(source)], check=True)
            before = fingerprints(source)
            parts = split_for_release(source, root/'parts', limit=260_000, timeout=60)
            self.assertGreater(len(parts), 1)
            self.assertTrue(all(0 < p.stat().st_size < 260_000 for p in parts))
            def frames(path):
                p = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0',
                                    '-show_entries', 'stream=nb_read_frames', '-of', 'json', str(path)],
                                   capture_output=True, text=True, check=True)
                return int(json.loads(p.stdout)['streams'][0]['nb_read_frames'])
            self.assertEqual(sum(frames(p) for p in parts), frames(source))
            for part in parts:
                subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(part), '-f', 'null', '-'],
                               capture_output=True, check=True)
            self.assertEqual(fingerprints(source), before)

    def test_impossible_split_keeps_source(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root/'fixture.ts'; source.write_bytes(b'not video')
            with self.assertRaises(PublishError):
                split_for_release(source, root/'parts', limit=2)
            self.assertEqual(source.read_bytes(), b'not video')


class ShutdownAndNotifyTests(unittest.TestCase):
    def test_concat_timeout_preserves_source_chunks(self):
        tree = ast.parse(Path('recorder.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'build_final_file')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            first, second = root/'chunk1.ts', root/'chunk2.ts'
            first.write_bytes(b'x' * 2000); second.write_bytes(b'y' * 2000)
            namespace = {'register_recovered_chunks': lambda active: None, 'unique_chunk_paths': lambda paths:(paths,[]), 'os': os,
                         'MIN_SAVE_BYTES': 1880, 'MIN_FREE_BYTES': 512 * 1024 * 1024,
                         'OUT_DIR': d, 'chunk_number': lambda value: value,
                         'is_archived_short_exit': lambda path: False, 'shutil': shutil,
                         'log': lambda message: None, 'log_raw': lambda *args: None, 'subprocess': subprocess}
            exec(compile(ast.Module(body=[node], type_ignores=[]), '<build_final_file>', 'exec'), namespace)
            active = {'chunks': [str(first), str(second)], 'final_ts': str(root/'final.ts'), 'chunk_dir': d}
            with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('ffmpeg', 180)) as run:
                self.assertIsNone(namespace['build_final_file'](active))
                self.assertEqual(run.call_args.kwargs['timeout'], 180)
            self.assertTrue(first.exists() and second.exists())

    def test_low_disk_preserves_chunks_without_attempting_concat(self):
        tree = ast.parse(Path('recorder.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'build_final_file')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            first, second = root/'chunk1.ts', root/'chunk2.ts'
            first.write_bytes(b'x' * 2000); second.write_bytes(b'y' * 2000)
            namespace = {'register_recovered_chunks': lambda active: None, 'unique_chunk_paths': lambda paths:(paths,[]), 'os': os,
                         'MIN_SAVE_BYTES': 1880, 'MIN_FREE_BYTES': 512 * 1024 * 1024,
                         'OUT_DIR': d, 'chunk_number': lambda value: value,
                         'is_archived_short_exit': lambda path: False, 'shutil': shutil,
                         'log': lambda message: None, 'subprocess': subprocess}
            exec(compile(ast.Module(body=[node], type_ignores=[]), '<build_final_file>', 'exec'), namespace)
            active = {'chunks': [str(first), str(second)], 'final_ts': str(root/'final.ts')}
            with patch('shutil.disk_usage', return_value=shutil._ntuple_diskusage(10000, 9000, 1000)), \
                 patch('subprocess.run') as run:
                self.assertIsNone(namespace['build_final_file'](active))
                run.assert_not_called()
            self.assertTrue(first.exists() and second.exists())

    def test_shutdown_uses_sigint_before_termination(self):
        # Extract just this function: importing recorder.py would start its monitor.
        tree = ast.parse(Path('recorder.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'stop_proc')
        namespace = {'signal': signal}
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<stop_proc>', 'exec'), namespace)
        class Process:
            def __init__(self): self.calls = []
            def poll(self): return None
            def send_signal(self, value): self.calls.append(('signal', value))
            def wait(self, timeout): self.calls.append(('wait', timeout))
            def terminate(self): self.calls.append(('terminate',))
        process = Process()
        namespace['stop_proc'](process)
        self.assertEqual(process.calls[0], ('signal', signal.SIGINT))

    def test_long_notification_split_does_not_lose_text(self):
        text = 'recording link\n' * 1000
        parts = list(chunks(text))
        self.assertEqual(''.join(parts), text)
        self.assertTrue(all(len(p) <= 3500 for p in parts))


class WorkflowStatusTests(unittest.TestCase):
    def test_capture_failure_cannot_turn_green_after_successful_upload(self):
        workflow = yaml.load(Path(os.environ.get('RECORD_WORKFLOW_PATH', '.github/workflows/record.yml')).read_text(), Loader=yaml.BaseLoader)
        script = workflow['jobs']['record']['steps'][-1]['run']
        for capture, upload, expected in [('failure', 'success', 1), ('success', 'failure', 1), ('success', 'success', 0)]:
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                (root/'upload-report.json').write_text(json.dumps({'all_uploaded': True, 'files': []}))
                env = dict(os.environ, CAPTURE_OUTCOME=capture, UPLOAD_OUTCOME=upload,
                           GITHUB_STEP_SUMMARY=str(root/'summary.md'))
                result = subprocess.run(['bash', '-e', '-c', script], cwd=root, env=env, capture_output=True)
                self.assertEqual(result.returncode, expected)


if __name__ == '__main__':
    unittest.main()
