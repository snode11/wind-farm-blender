"""Local 30-minute synthetic transport test; never a 4K image-quality claim."""
import csv
import json
from pathlib import Path
import resource
import socket
import subprocess
import sys
import threading
import time
import traceback

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from wfrl.camera_video.media import encode_video, sha256
from wfrl.camera_video.stream import mediamtx_config, publish, receive

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT / 'synthetic-60s'
BINARY = Path('/tmp/wfrl-mediamtx-v1.21.0/mediamtx')
URL = 'rtsp://127.0.0.1:18564/windfarm/camera1'
MAP = 'http://127.0.0.1:18565'


def write_json(name, data):
    temp = ROOT / (name + '.tmp')
    temp.write_text(json.dumps(data, indent=2) + '\n')
    temp.replace(ROOT / name)


def main():
    PACKAGE.mkdir(exist_ok=True)
    master = PACKAGE / 'master_frames'; master.mkdir(exist_ok=True)
    if not (PACKAGE / 'video.mp4').exists():
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=128x72:rate=20',
                        '-frames:v', '1200', '-start_number', '0', str(master / '%06d.png')], check=True)
        with (PACKAGE / 'frames.csv').open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=['frame_id', 'video_pts', 'sim_time_s'])
            writer.writeheader()
            writer.writerows({'frame_id': i, 'video_pts': i, 'sim_time_s': 117 + (i + .5) / 20} for i in range(1200))
        encode_video(PACKAGE, 20, preset='ultrafast')
        (PACKAGE / 'manifest.json').write_text(json.dumps({'dataset_id': 'SYNTHETIC-transport-only-128x72-60s',
            'time_contract': {'fps': 20}, 'files': {n: sha256(PACKAGE / n) for n in ('video.mp4', 'frames.csv')}}))
    config = ROOT / 'mediamtx-local.yml'; config.write_text(mediamtx_config(18564))
    log = (ROOT / 'mediamtx-stability.log').open('w')
    server = subprocess.Popen([str(BINARY), str(config)], cwd=ROOT, stdout=log, stderr=log)
    stop = threading.Event()
    errors = []
    def publish_work():
        try:
            publish(PACKAGE, URL, mapping_port=18565, stop_event=stop, initial_timestamp=(1 << 32) - 45000)
        except BaseException:
            errors.append(traceback.format_exc())
    thread = threading.Thread(target=publish_work)
    start = time.monotonic()
    report = {'status': 'running', 'started_unix': time.time(), 'scope': '128x72 synthetic 60s 20FPS local transport only',
              'target_seconds': 1800, 'mediamtx_version': '1.21.0', 'mediamtx_sha256': sha256(BINARY),
              'download_url': 'https://github.com/bluenviron/mediamtx/releases/download/v1.21.0/mediamtx_v1.21.0_darwin_arm64.tar.gz',
              'receivers': [], 'errors': errors}
    try:
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1', 18564), timeout=.1): break
            except OSError: time.sleep(.05)
        thread.start(); time.sleep(.6)
        index = 0
        all_cycles = set()
        session = None
        while time.monotonic() - start < 1800 or len(all_cycles) < 31:
            target = ROOT / f'stability-receiver-{index:02d}'
            received = receive(URL, MAP, frame_count=1200, output_dir=target)
            assert all(b['media_timestamp'] - a['media_timestamp'] == 4500 for a, b in zip(received, received[1:]))
            if session is None: session = received[0]['session_id']
            assert all(r['session_id'] == session for r in received)
            cycles = sorted({r['cycle_id'] for r in received}); all_cycles.update(cycles)
            subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(target / 'received.h264'), '-f', 'null', '-'], check=True)
            decoded = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-count_frames', '-show_entries',
                'stream=nb_read_frames', '-of', 'json', str(target / 'received.h264')], text=True))
            assert int(decoded['streams'][0]['nb_read_frames']) == 1200
            rss = subprocess.check_output(['ps', '-o', 'rss=', '-p', str(server.pid)], text=True).strip()
            report['receivers'].append({'index': index, 'matched_decoded_frames': len(received), 'cycles': cycles,
                'elapsed_seconds': time.monotonic() - start, 'server_rss_kib': int(rss),
                'client_peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                'first_media_timestamp': received[0]['media_timestamp'], 'last_media_timestamp': received[-1]['media_timestamp']})
            report.update(observed_cycles=sorted(all_cycles), elapsed_seconds=time.monotonic() - start)
            write_json('stability-progress.json', report)
            print(json.dumps(report['receivers'][-1]), flush=True)
            index += 1
        report.update(status='passed', session_id=session)
    except BaseException:
        errors.append(traceback.format_exc()); report['status'] = 'failed'
        raise
    finally:
        stop.set()
        if thread.is_alive(): thread.join(10)
        server.terminate(); server.wait(timeout=5); log.close()
        report.update(elapsed_seconds=time.monotonic() - start, errors=errors)
        if errors: report['status'] = 'failed'
        write_json('stability-progress.json', report)


if __name__ == '__main__':
    main()
