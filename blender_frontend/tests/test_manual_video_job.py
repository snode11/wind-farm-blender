"""Isolated job supervision and master recovery using non-media placeholder bytes."""
import csv
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
PACKAGE = '_manual_video_test_helpers'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT / 'blender_frontend/wfrl_blender')]
sys.modules[PACKAGE] = package
jobs = importlib.import_module(PACKAGE + '.manual_video_job')
video = importlib.import_module(PACKAGE + '.manual_video_render')
from wfrl.camera_video import media


def make_job(root):
    root.mkdir(exist_ok=True)
    snapshot = root / 'snapshot.fixture'
    snapshot.write_bytes(b'non-media frozen scene placeholder')
    executable = root / 'executable.fixture'
    executable.write_bytes(b'non-executable encoder placeholder')
    pose = dict(yaw_deg=0, pitch_deg=0, rpm=9, azimuth_deg=180, individual_pitch=False,
                pitch_b1_deg=0, pitch_b2_deg=0, pitch_b3_deg=0)
    configuration = dict(schema='wfrl.manual-pitch-trial.v1', blade_model='rootkink', start_pose=pose,
                         targets=[20, 0, 0], selected=[True, False, False], duration_s=.08, interpolation='LINEAR')
    job = dict(schema='wfrl.manual-video-job.v2', job_id=uuid.uuid4().hex, control_token=uuid.uuid4().hex * 2,
               configuration=json.dumps(configuration), snapshot=str(snapshot), scene_name='manual',
               output_mp4=str(root / 'output.mp4'), width=1920, height=1080, fps=25, hold_s=.04,
               samples=16, ffmpeg=str(executable), ffprobe=str(executable), render_command=[])
    path = root / 'job.json'
    jobs.write_json(path, job)
    return path, job


def make_master(path, job):
    root = path.parent
    spec = video._spec(job)
    count = video.frame_count(spec.duration_s, job['hold_s'], job['fps'])
    master = root / 'master_frames'
    master.mkdir()
    for index in range(count):
        (master / f'{index:06d}.png').write_bytes(f'NON-MEDIA frame placeholder {index}'.encode())
    with (root / 'frames.csv').open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(('frame_id', 'video_pts', 'time_s', 'rpm', 'azimuth_deg',
                         'pitch_b1_deg', 'pitch_b2_deg', 'pitch_b3_deg', 'segment'))
        for index in range(count):
            pose = spec.pose_at(index / job['fps'])
            writer.writerow((index, index, pose.time_s, spec.rpm, pose.azimuth_deg, *pose.pitches_deg,
                             'transition' if pose.time_s < spec.duration_s else 'hold'))
    jobs.write_json(root / 'camera.json', dict(camera='fixed placeholder'))
    jobs.write_json(root / 'experiment.json', dict(configuration=json.loads(job['configuration']),
                    frame_count=count, fps=job['fps'], resolution=[job['width'], job['height']],
                    snapshot_sha256=media.sha256(job['snapshot']),
                    source=dict(render_source=video._sources(), resources_sha256={})))
    video.seal_master(path)
    jobs.write_json(root / 'status.json', dict(state='FAILED', job_id=job['job_id'], final=True))


class MasterRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='manual-master-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path, self.job = make_job(self.root)
        make_master(self.path, self.job)

    def mock_encoder(self, root, fps, **options):
        self.assertTrue(options['resume'])
        checks = root / 'checks'
        checks.mkdir(exist_ok=True)
        (root / 'video.mp4').write_bytes(b'NON-MEDIA encoded placeholder')
        return dict(valid=True, width=1920, height=1080, frame_count=3,
                    video_sha256=media.sha256(root / 'video.mp4'))

    def mock_probe(self, *args, **kwargs):
        return types.SimpleNamespace(stdout=json.dumps(dict(format=dict(duration=.12), streams=[dict(duration=.12)])))

    def finish(self):
        with patch.object(media, 'encode_video', side_effect=self.mock_encoder), \
             patch.object(video.subprocess, 'run', side_effect=self.mock_probe):
            return video.resume_job(self.path)

    def test_complete_master_retries_encoding_and_publication_without_bpy(self):
        before = {p.name: media.sha256(p) for p in (self.root / 'master_frames').iterdir()}
        self.finish()
        self.assertEqual(jobs.query(self.path)['state'], 'COMPLETE')
        self.assertEqual(before, {p.name: media.sha256(p) for p in (self.root / 'master_frames').iterdir()})
        self.assertEqual(Path(self.job['output_mp4']).read_bytes(), b'NON-MEDIA encoded placeholder')

    def test_owned_interrupted_staging_is_cleaned_but_foreign_staging_is_preserved(self):
        stage = self.root / '.wfrl-video-fixture'
        stage.write_bytes(b'NON-MEDIA partial publication')
        info = stage.stat()
        receipt = dict(job_id=self.job['job_id'], output_mp4=self.job['output_mp4'],
                       master_sha256=media.sha256(self.root / 'master.json'),
                       device=info.st_dev, inode=info.st_ino, staging_path=str(stage))
        jobs.write_json(self.root / 'publication.json', receipt)
        video.cleanup_publication(self.path)
        self.assertFalse(stage.exists())
        stage.write_bytes(b'foreign replacement')
        receipt['inode'] = -1
        jobs.write_json(self.root / 'publication.json', receipt)
        with self.assertRaises(ValueError):
            video.cleanup_publication(self.path)
        self.assertEqual(stage.read_bytes(), b'foreign replacement')

    def test_three_minute_contract_is_4500_linear_samples(self):
        job = dict(self.job)
        config = json.loads(job['configuration'])
        config['duration_s'] = 4
        job['configuration'] = json.dumps(config)
        job['hold_s'] = 176
        spec = video._spec(job)
        self.assertEqual(video.frame_count(spec.duration_s, job['hold_s'], 25), 4500)
        for index in range(4500):
            time_s = index / 25
            pose = spec.pose_at(time_s)
            self.assertAlmostEqual(pose.pitches_deg[0], min(20, time_s * 5))
            self.assertEqual(pose.pitches_deg[1:], (0, 0))
            self.assertAlmostEqual(pose.azimuth_deg, 180 + 54 * time_s)

    def test_successful_state_is_immutable_on_retry(self):
        self.finish()
        before = (self.root / 'status.json').read_bytes()
        with self.assertRaises(ValueError):
            video.resume_job(self.path)
        self.assertEqual(before, (self.root / 'status.json').read_bytes())

    def test_publication_failure_can_retry_with_identical_master(self):
        with patch.object(video, '_publish', side_effect=OSError('simulated full destination')):
            with self.assertRaises(OSError):
                self.finish()
        self.assertEqual(jobs.query(self.path)['state'], 'FAILED')
        self.finish()
        self.assertEqual(jobs.query(self.path)['state'], 'COMPLETE')

    def test_supervised_cli_retry_waits_for_simulated_codec_failure_and_recovery(self):
        executable = Path(self.job['ffmpeg'])
        executable.write_text(f'#!{sys.executable}\n' + '''import json,sys
from pathlib import Path
args=sys.argv[1:]
complete=b'NON-MEDIA simulated codec placeholder'
if '-version' in args:
    print('simulated-codec fixture 1')
elif '-framerate' in args:
    flag=Path(__file__).with_name('fail-encode.fixture')
    if flag.exists():
        flag.unlink()
        Path(args[-1]).write_bytes(b'NON-MEDIA partial')
        raise SystemExit(7)
    Path(args[-1]).write_bytes(complete)
elif '-show_frames' in args:
    if Path(args[-1]).read_bytes()!=complete:
        raise SystemExit(8)
    stream=dict(codec_name='h264',pix_fmt='yuv420p',has_b_frames=0,time_base='1/25',avg_frame_rate='25/1',
                width=1920,height=1080,color_space='bt709',color_transfer='iec61966-2-1',
                color_primaries='bt709',color_range='tv')
    print(json.dumps(dict(streams=[stream],frames=[dict(pts=i,key_frame=int(i==0),pict_type='I' if i==0 else 'P') for i in range(3)])))
elif '-show_entries' in args:
    print(json.dumps(dict(format=dict(duration=.12),streams=[dict(duration=.12)])))
''')
        executable.chmod(0o700)
        (self.root / 'master.json').unlink()
        video.seal_master(self.path)
        before = {p.name: media.sha256(p) for p in (self.root / 'master_frames').iterdir()}
        (self.root / 'fail-encode.fixture').touch()
        command = [sys.executable, '-B', str(ROOT / 'scripts/blender/manage_manual_video.py'),
                   'retry', '--job', str(self.path)]
        failed = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(failed.returncode, 1, failed.stderr)
        self.assertEqual(jobs.query(self.path)['state'], 'FAILED')
        recovered = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(recovered.returncode, 0, recovered.stderr + recovered.stdout)
        self.assertEqual(jobs.query(self.path)['state'], 'COMPLETE')
        self.assertEqual(before, {p.name: media.sha256(p) for p in (self.root / 'master_frames').iterdir()})
        self.assertEqual(Path(self.job['output_mp4']).read_bytes(), b'NON-MEDIA simulated codec placeholder')

    def test_commit_before_status_failure_is_recognized_by_file_identity(self):
        original = video._write_json
        def interrupted(path, value):
            if path.name == 'status.json' and value.get('state') == 'COMPLETE':
                raise OSError('simulated final status interruption')
            original(path, value)
        with patch.object(video, '_write_json', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.finish()
        identity = Path(self.job['output_mp4']).stat().st_ino
        self.finish()
        self.assertEqual(identity, Path(self.job['output_mp4']).stat().st_ino)

    def test_foreign_output_and_dangling_symlink_are_preserved(self):
        output = Path(self.job['output_mp4'])
        output.write_bytes(b'other owner')
        with self.assertRaises((ValueError, FileExistsError)):
            self.finish()
        self.assertEqual(output.read_bytes(), b'other owner')
        output.unlink()
        output.symlink_to(self.root / 'absent')
        with self.assertRaises((ValueError, FileExistsError)):
            self.finish()
        self.assertTrue(output.is_symlink())

    def test_missing_frame_rejected_without_status_mutation(self):
        (self.root / 'master_frames/000001.png').unlink()
        self.assert_rejected_without_mutation()

    def test_unknown_master_without_completion_seal_is_rejected(self):
        (self.root / 'master.json').unlink()
        self.assert_rejected_without_mutation()

    def test_corrupt_and_mixed_frame_content_rejected(self):
        (self.root / 'master_frames/000001.png').write_bytes(b'other job frame')
        self.assert_rejected_without_mutation()

    def test_extra_frame_rejected(self):
        (self.root / 'master_frames/000003.png').write_bytes(b'extra')
        self.assert_rejected_without_mutation()

    def test_frame_symlink_rejected(self):
        frame = self.root / 'master_frames/000001.png'
        frame.unlink()
        frame.symlink_to(self.root / 'snapshot.fixture')
        self.assert_rejected_without_mutation()

    def test_changed_configuration_source_camera_snapshot_and_encoder_rejected(self):
        for name in ('job.json', 'camera.json', 'snapshot.fixture', 'executable.fixture', 'experiment.json'):
            path = self.root / name
            content = path.read_bytes()
            if path.suffix == '.json':
                data = jobs.read_json(path)
                data['changed'] = True
                if name == 'experiment.json':
                    data['source']['render_source'] = {}
                jobs.write_json(path, data)
            else:
                path.write_bytes(content + b'changed')
            with self.subTest(name=name):
                self.assert_rejected_without_mutation()
            path.write_bytes(content)

    def test_pose_rows_rejected_even_if_a_csv_hash_is_resealed(self):
        path = self.root / 'frames.csv'
        path.write_text(path.read_text().replace('20.0,0.0,0.0,hold', '19.0,0.0,0.0,hold'))
        self.assert_rejected_without_mutation()

    def assert_rejected_without_mutation(self):
        previous = (self.root / 'status.json').read_bytes()
        with self.assertRaises((ValueError, KeyError, OSError)):
            video.resume_job(self.path)
        self.assertEqual(previous, (self.root / 'status.json').read_bytes())
        self.assertFalse(Path(self.job['output_mp4']).exists())


class SupervisionTests(unittest.TestCase):
    def setUp(self):
        jobs.require_supervision()
        self.temp = tempfile.TemporaryDirectory(prefix='manual-process-test-')
        self.root = Path(self.temp.name)
        self.path, self.job = make_job(self.root)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        if jobs.lease_busy(self.root / 'supervision.lock'):
            jobs.cancel(self.path)
        self.temp.cleanup()

    def start(self, body):
        script = self.root / 'worker.fixture.py'
        script.write_text(body)
        jobs.start(self.path, command=[sys.executable, '-B', str(script)])

    def ready(self, name='ready'):
        deadline = time.monotonic() + 8
        while not (self.root / name).exists():
            if time.monotonic() > deadline:
                self.fail('isolated worker did not become ready')
            time.sleep(.02)

    def test_fresh_controller_recovers_and_cancels_all_children(self):
        child = self.root / 'child.fixture.py'
        child.write_text("import signal,time,os\nfrom pathlib import Path\n"
                         "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                         f"p=Path({str(self.root / 'heartbeat')!r})\n"
                         "while True:\n p.write_text(str(time.monotonic()));time.sleep(.02)\n")
        self.start("import subprocess,sys,time,signal\nfrom pathlib import Path\n"
                   "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                   f"subprocess.Popen([sys.executable,'-B',{str(child)!r}])\n"
                   f"Path({str(self.root / 'ready')!r}).touch()\nwhile True:time.sleep(.1)\n")
        self.ready('heartbeat')
        process_record = jobs.read_json(self.root / 'process.json')
        process_record['pid'] = os.getpid()
        jobs.write_json(self.root / 'process.json', process_record)
        command = [sys.executable, '-B', str(ROOT / 'scripts/blender/manage_manual_video.py')]
        record = subprocess.run(command + ['status', '--job', str(self.path)], capture_output=True, text=True, check=True)
        self.assertTrue(json.loads(record.stdout)['active'])
        result = subprocess.run(command + ['cancel', '--job', str(self.path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 130, result.stderr)
        self.assertEqual(json.loads(result.stdout)['state'], 'CANCELLED')
        content = (self.root / 'heartbeat').read_bytes()
        time.sleep(.1)
        self.assertEqual(content, (self.root / 'heartbeat').read_bytes())
        self.assertFalse(jobs.query(self.path)['active'])
        self.assertEqual(jobs.cancel(self.path)['state'], 'CANCELLED')

    def test_same_output_is_exclusively_reserved_across_jobs(self):
        self.start('import time\ntime.sleep(20)\n')
        other_path, other = make_job(self.root / 'other')
        other['output_mp4'] = self.job['output_mp4']
        jobs.write_json(other_path, other)
        with self.assertRaisesRegex(ValueError, '占用'):
            jobs.start(other_path, command=[sys.executable, '-B', '-c', 'pass'])
        self.assertEqual(jobs.cancel(self.path)['state'], 'CANCELLED')

    def test_process_exit_without_completion_is_failure_and_cli_wait_fails(self):
        self.start('raise SystemExit(7)\n')
        result = jobs.wait(self.path, timeout=10)
        self.assertEqual(result['state'], 'FAILED')
        self.assertEqual(result['exit_code'], 7)
        proc = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/blender/manage_manual_video.py'),
                               'wait', '--job', str(self.path)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, proc.stderr)

    def test_cancel_after_failed_root_exit_does_not_relabel_failure(self):
        self.start('raise SystemExit(7)\n')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            record = jobs.query(self.path)
            if record['state'] in {'FINALIZING', 'FAILED'}:
                break
            time.sleep(.01)
        self.assertEqual(jobs.cancel(self.path)['state'], 'FAILED')

    def test_verified_complete_job_and_cancel_exit_race_keep_success(self):
        self.start("import os,json,time,hashlib\nfrom pathlib import Path\n"
                   f"root=Path({str(self.root)!r})\njob=json.loads((root/'job.json').read_text())\n"
                   "(root/'output.mp4').write_bytes(b'NON-MEDIA success placeholder')\n"
                   "record=dict(job_id=job['job_id'],attempt_id=os.environ['WFRL_MANUAL_ATTEMPT'],state='COMPLETE',"
                   "video_sha256=hashlib.sha256((root/'output.mp4').read_bytes()).hexdigest())\n"
                   "(root/'worker-status.json').write_text(json.dumps(record))\n(root/'ready').touch()\ntime.sleep(.4)\n")
        self.ready()
        result = jobs.cancel(self.path)
        self.assertEqual(result['state'], 'COMPLETE')
        before = (self.root / 'status.json').read_bytes()
        with self.assertRaises(ValueError):
            jobs.start(self.path, command=[sys.executable, '-B', '-c', 'pass'])
        self.assertEqual(before, (self.root / 'status.json').read_bytes())

    def test_cancel_after_verified_commit_before_worker_status_preserves_completion(self):
        make_master(self.path, self.job)
        self.start("import os,json,time,hashlib\nfrom pathlib import Path\n"
                   "from wfrl.camera_video.publication import publish\n"
                   f"root=Path({str(self.root)!r})\njob=json.loads((root/'job.json').read_text())\n"
                   "video=root/'video.mp4'\nvideo.write_bytes(b'NON-MEDIA committed placeholder')\n"
                   "digest=hashlib.sha256(video.read_bytes()).hexdigest()\n(root/'checks').mkdir()\n"
                   "checks=dict(valid=True,frame_count=3,resolution=[1920,1080],duration_s=.12,fps=25,full_decode=True,video_sha256=digest)\n"
                   "(root/'checks/manual-video.json').write_text(json.dumps(checks))\n"
                   "def receipt(stage,info):\n"
                   " data=dict(job_id=job['job_id'],output_mp4=job['output_mp4'],video_sha256=digest,"
                   "master_sha256=hashlib.sha256((root/'master.json').read_bytes()).hexdigest(),"
                   "device=info.st_dev,inode=info.st_ino,staging_path=str(stage))\n"
                   " (root/'publication.json').write_text(json.dumps(data))\n"
                   "publish(video,Path(job['output_mp4']),on_stage=receipt)\n"
                   "(root/'worker-status.json').write_text(json.dumps(dict(job_id=job['job_id'],"
                   "attempt_id=os.environ['WFRL_MANUAL_ATTEMPT'],state='PUBLISHING')))\n"
                   "(root/'ready').touch()\ntime.sleep(20)\n")
        self.ready()
        self.assertEqual(jobs.cancel(self.path)['state'], 'COMPLETE')

    def test_supervisor_spawn_failure_has_final_failure_status(self):
        with patch.object(jobs.subprocess, 'Popen', side_effect=OSError('simulated unavailable interpreter')):
            with self.assertRaises(OSError):
                jobs.start(self.path)
        self.assertEqual(jobs.query(self.path)['state'], 'FAILED')
        self.assertFalse(jobs.query(self.path)['active'])

    def test_unreachable_or_wrong_authenticated_endpoint_is_lost_without_signals(self):
        self.start('import time\ntime.sleep(20)\n')
        real_rpc = jobs._rpc
        with patch.object(jobs, '_rpc', side_effect=ConnectionRefusedError), \
             patch.object(os, 'killpg', side_effect=AssertionError('client must never signal a saved PID')):
            self.assertEqual(jobs.query(self.path)['state'], 'LOST')
            self.assertTrue(jobs.query(self.path)['active'])
            with self.assertRaises(ValueError):
                jobs.cancel(self.path)
        endpoint = jobs.read_json(self.root / 'control.json')
        endpoint['attempt_id'] = uuid.uuid4().hex
        jobs.write_json(self.root / 'control.json', endpoint)
        self.assertEqual(jobs.query(self.path)['state'], 'LOST')
        endpoint['attempt_id'] = jobs.read_json(self.root / 'process.json')['attempt_id']
        jobs.write_json(self.root / 'control.json', endpoint)
        self.assertTrue(real_rpc(self.path, self.job, 'status')['active'])
        jobs.cancel(self.path)

    def test_lost_job_without_live_lease_is_not_marked_cancelled_or_failed(self):
        jobs.write_json(self.root / 'status.json', dict(job_id=self.job['job_id'], state='RENDERING'))
        record = jobs.query(self.path)
        self.assertEqual(record['state'], 'LOST')
        self.assertFalse(record['active'])
        with self.assertRaises(ValueError):
            jobs.cancel(self.path)
        self.assertEqual(jobs.read_json(self.root / 'status.json')['state'], 'RENDERING')

    def test_reused_port_cannot_impersonate_supervisor_by_echoing_request(self):
        with socket.socket() as server:
            server.bind(('127.0.0.1', 0))
            server.listen(1)
            attempt = uuid.uuid4().hex
            jobs.write_json(self.root / 'control.json', dict(job_id=self.job['job_id'],
                            attempt_id=attempt, port=server.getsockname()[1]))
            def impersonate():
                connection, address = server.accept()
                with connection:
                    with connection.makefile('rb') as handle:
                        request = json.loads(handle.readline())
                    response = dict(request, request_nonce=request['nonce'], state='COMPLETE', final=True)
                    connection.sendall(json.dumps(response).encode() + b'\n')
            thread = threading.Thread(target=impersonate)
            thread.start()
            with self.assertRaises(ValueError):
                jobs._rpc(self.path, self.job, 'cancel')
            thread.join(timeout=2)

    def test_output_aliases_share_a_lease(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(jobs.output_lock(self.job['output_mp4']), jobs.output_lock(alias / 'output.mp4'))
        self.assertEqual(jobs.output_lock(self.root / 'OUTPUT.mp4'), jobs.output_lock(self.root / 'output.mp4'))

    def test_codec_lease_survives_an_abrupt_render_parent_exit(self):
        child = self.root / 'codec.fixture.py'
        child.write_text("import time\nfrom pathlib import Path\n"
                         f"Path({str(self.root / 'ready')!r}).touch()\ntime.sleep(.7)\n")
        parent = self.root / 'render.fixture.py'
        parent.write_text("import os,sys,threading,time\nfrom pathlib import Path\n"
                          "from wfrl.camera_video.media import _run\n"
                          f"threading.Thread(target=_run,args=([sys.executable,'-B',{str(child)!r}],),daemon=True).start()\n"
                          f"p=Path({str(self.root / 'ready')!r})\n"
                          "while not p.exists():time.sleep(.01)\nos._exit(0)\n")
        with jobs.Lease(jobs.output_lock(self.job['output_mp4']), self.job['job_id']) as lease:
            environment = dict(os.environ, WFRL_MANUAL_OUTPUT_FD=str(lease.fd), WFRL_MANUAL_ATTEMPT='fixture')
            process = subprocess.Popen([sys.executable, '-B', str(parent)], env=environment, pass_fds=(lease.fd,))
            process.wait(timeout=5)
        self.assertTrue(jobs.query(self.path)['active'])
        deadline = time.monotonic() + 5
        while jobs.query(self.path)['active'] and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertFalse(jobs.query(self.path)['active'])

    def test_exited_root_still_cleans_lingering_children_before_reaping(self):
        child = self.root / 'child.fixture.py'
        child.write_text("import signal,time\nfrom pathlib import Path\n"
                         "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                         f"p=Path({str(self.root / 'heartbeat')!r})\n"
                         "while True:\n p.write_text(str(time.monotonic()));time.sleep(.02)\n")
        self.start("import subprocess,sys,time\nfrom pathlib import Path\n"
                   f"subprocess.Popen([sys.executable,'-B',{str(child)!r}])\n"
                   f"p=Path({str(self.root / 'heartbeat')!r})\n"
                   "while not p.exists():time.sleep(.01)\n")
        result = jobs.wait(self.path, timeout=10)
        self.assertEqual(result['state'], 'FAILED')
        content = (self.root / 'heartbeat').read_bytes()
        time.sleep(.1)
        self.assertEqual(content, (self.root / 'heartbeat').read_bytes())

    @unittest.skipUnless(os.name == 'posix', 'Lease requires POSIX flock')
    def test_empty_lock_file_can_be_claimed_under_exclusive_flock(self):
        path = jobs.output_lock(self.job['output_mp4'])
        path.touch()
        inode = path.stat().st_ino
        with jobs.Lease(path, self.job['job_id']):
            self.assertEqual(path.stat().st_ino, inode)
            self.assertEqual(jobs.read_json(path), dict(schema=jobs.LEASE_SCHEMA, owner=self.job['job_id']))
            self.assertTrue(jobs.lease_busy(path, owner=self.job['job_id']))
            with self.assertRaisesRegex(ValueError, '已有作业占用'):
                jobs.Lease(path, 'other owner')
        self.assertFalse(jobs.lease_busy(path))

    def test_unowned_or_symlink_lock_files_are_rejected(self):
        path = jobs.output_lock(self.job['output_mp4'])
        for content in (b'foreign file', b'[]', b'{}'):
            path.write_bytes(content)
            with self.subTest(content=content), self.assertRaises(ValueError):
                jobs.Lease(path, self.job['job_id'])
            self.assertEqual(path.read_bytes(), content)
        path.unlink()
        path.symlink_to(self.root / 'snapshot.fixture')
        with self.assertRaises(OSError):
            jobs.Lease(path, self.job['job_id'])


class SceneRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='manual-scene-test-')
        self.root = Path(self.temp.name)
        self.path, self.job = make_job(self.root)
        self.addCleanup(self.cleanup)
        bpy = types.ModuleType('bpy')
        handlers = types.ModuleType('bpy.app.handlers')
        handlers.persistent = lambda function: function
        bpy.types = types.SimpleNamespace(Operator=type('Operator', (), {}))
        bpy.props = types.SimpleNamespace(StringProperty=lambda **kw: None)
        self.registered = set()
        timers = types.SimpleNamespace(is_registered=lambda fn: fn in self.registered,
                                       register=lambda fn, **kw: self.registered.add(fn))
        bpy.app = types.SimpleNamespace(timers=timers, binary_path='Blender is not run')
        bpy.data = types.SimpleNamespace(scenes=[], filepath='source.fixture')
        bpy.context = types.SimpleNamespace(window_manager=types.SimpleNamespace(windows=[]))
        bpy.path = types.SimpleNamespace(abspath=str)
        bpy.ops = types.SimpleNamespace(wm=types.SimpleNamespace(save_as_mainfile=lambda **kw: self.fail('snapshot must not be written')))
        extras = types.ModuleType('bpy_extras.io_utils')
        extras.ExportHelper = type('ExportHelper', (), {})
        modules = {'bpy': bpy, 'bpy.app.handlers': handlers, 'bpy_extras.io_utils': extras}
        self.patching = patch.dict(sys.modules, modules)
        self.patching.start()
        self.addCleanup(self.patching.stop)
        spec = importlib.util.spec_from_file_location(PACKAGE + '.manual_video_ui',
                       ROOT / 'blender_frontend/wfrl_blender/manual_video.py')
        self.ui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ui)
        self.bpy = bpy
        class Scene(dict):
            pass
        self.scene = Scene(wfrl_manual_owner='owner', wfrl_manual_video_job=str(self.path))
        self.scene.name, self.scene.session_uid = 'manual', 1
        self.scene.wfrl_manual_trial = types.SimpleNamespace(configuration=self.job['configuration'])
        self.scene.wfrl_manual_video_hold_s = .04
        bpy.data.scenes.append(self.scene)
        jobs.write_json(self.root / 'scene-binding.json', dict(scene_name='manual', scene_owner='owner'))

    def cleanup(self):
        self.ui._QUERIES.clear()
        if jobs.lease_busy(self.root / 'supervision.lock'):
            jobs.cancel(self.path)
        self.temp.cleanup()

    def test_fresh_scene_controller_restores_supervision_and_cancel(self):
        jobs.start(self.path, command=[sys.executable, '-B', '-c', 'import time;time.sleep(20)'])
        self.assertFalse(self.ui._JOBS)
        self.assertFalse(self.ui._SCENE_JOBS)
        self.ui._after_load(None)
        self.assertTrue(self.ui.active(self.scene))
        self.assertIn(self.ui._poll, self.registered)
        copy = type(self.scene)(self.scene)
        copy.name, copy.session_uid = 'manual copy', 2
        self.assertFalse(self.ui.active(copy))
        self.assertEqual(self.ui.cancel(self.scene)['state'], 'CANCELLED')

    def test_terminal_status_does_not_reactivate_poll_tracking(self):
        jobs.start(self.path, command=[sys.executable, '-B', '-c', 'import time;time.sleep(20)'])
        self.ui._after_load(None)
        self.ui.cancel(self.scene)
        self.ui._poll()
        self.assertEqual(self.ui.status(self.scene)['state'], 'CANCELLED')
        self.assertFalse(self.ui._JOBS)

    def test_publish_preflight_failure_precedes_snapshot_or_background_launch(self):
        from wfrl.camera_video import publication
        control = types.ModuleType(PACKAGE + '.manual_control')
        control.validate_scene = lambda scene: (True, '')
        control.trial_configuration = lambda scene: ({}, video._spec(self.job))
        self.scene.pop('wfrl_manual_video_job')
        with patch.dict(sys.modules, {PACKAGE + '.manual_control': control}), \
             patch.object(self.ui, '_binary', return_value=sys.executable), \
             patch.object(self.ui, '_python_binary', return_value=sys.executable), \
             patch.object(self.ui, '_preflight_output', side_effect=OSError('unsupported publication')):
            with self.assertRaises(OSError):
                self.ui.launch(self.scene, self.root / 'new.mp4')
        self.assertFalse(list(self.root.glob('*_data_*')))

    def test_registration_restore_waits_for_unrestricted_scene_access(self):
        with patch.object(self.bpy, 'data', types.SimpleNamespace()), \
             patch.object(self.ui, '_after_load') as restore:
            self.assertEqual(self.ui._restore_registered_jobs(), .1)
            restore.assert_not_called()
        with patch.object(self.ui, '_after_load') as restore:
            self.assertIsNone(self.ui._restore_registered_jobs())
            restore.assert_called_once_with(None)

    def test_installed_entry_requires_and_uses_configured_project(self):
        preferences = types.ModuleType(PACKAGE + '.preferences')
        preferences.get_preferences = lambda: types.SimpleNamespace(project_dir=str(ROOT))
        with patch.object(self.ui, '__file__', str(self.root / 'extensions/addon/manual_video.py')), \
             patch.dict(sys.modules, {PACKAGE + '.preferences': preferences}):
            self.assertEqual(self.ui._workspace_root(), ROOT)
            preferences.get_preferences = lambda: types.SimpleNamespace(project_dir='')
            with self.assertRaisesRegex(ValueError, 'Project directory'):
                self.ui._workspace_root()


if __name__ == '__main__':
    unittest.main()
