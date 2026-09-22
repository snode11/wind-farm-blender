"""Actual 4K camera sample transport check, separate from synthetic soak tests."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from wfrl.camera_video.package import verify_package, write_json
from wfrl.camera_video.media import sha256, read_frames
from wfrl.camera_video.stream import publish, receive, mediamtx_config


def main():
    package = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).parent / 't1-short-4k'
    output = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path(__file__).parent / 'short-4k-rtsp'
    output.mkdir(exist_ok=False)
    verified = verify_package(package)
    binary = Path('/tmp/wfrl-mediamtx-v1.21.0/mediamtx')
    config = output / 'mediamtx.yml'
    config.write_text(mediamtx_config(28554))
    url = 'rtsp://127.0.0.1:28554/windfarm/camera1'
    mapping = 'http://127.0.0.1:28555/'
    log = (output / 'server.log').open('w')
    server = subprocess.Popen([str(binary), str(config.resolve())], stdout=log, stderr=log)
    stop = threading.Event()
    errors = []
    session_logs = []
    report = {'status': 'running', 'package': verified, 'scope': 'one local 4K TCP client, actual single-turbine camera video',
              'mediamtx_sha256': sha256(binary), 'mediamtx_version': '1.21.0',
              'stream_source_sha256': sha256(ROOT / 'wfrl/camera_video/stream.py')}

    def work():
        try:
            session_logs.append(str(publish(package, url, mapping_port=28555, stop_event=stop)))
        except BaseException:
            errors.append(traceback.format_exc())

    worker = threading.Thread(target=work)
    try:
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1', 28554), timeout=.1):
                    break
            except OSError:
                time.sleep(.05)
        worker.start()
        deadline = time.monotonic() + 180
        while True:
            if errors:
                raise RuntimeError(errors[-1])
            try:
                with socket.create_connection(('127.0.0.1', 28555), timeout=.2):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError('Publisher mapping server did not become ready')
                time.sleep(.2)
        time.sleep(1.5)  # Intentionally join after actual publication began.
        batches = []
        rows = read_frames(package / 'frames.csv')
        counts = (len(rows) + 20, 40)
        for index, count in enumerate(counts):
            directory = output / f'receive-{index}'
            records = receive(url, mapping, frame_count=count, output_dir=directory, timeout=count / 20 + 30)
            assert all(r['dataset_id'] == verified['dataset_id'] for r in records)
            assert all(r['frame_data'] == rows[r['frame_id']] for r in records)
            assert all(b['media_timestamp'] - a['media_timestamp'] == 4500 for a, b in zip(records, records[1:]))
            subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-threads', '0', '-i', str(directory / 'received.h264'),
                            '-f', 'null', '-'], check=True)
            info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-threads', '0', '-count_frames',
                '-show_entries', 'stream=width,height,nb_read_frames', '-of', 'json',
                str(directory / 'received.h264')], text=True))['streams'][0]
            assert (info['width'], info['height'], int(info['nb_read_frames'])) == (3840, 2160, count)
            batches.append({'decoded_frames': count, 'width': info['width'], 'height': info['height'],
                            'cycles': sorted({r['cycle_id'] for r in records}),
                            'session_id': records[0]['session_id'], 'every_label_matches_csv': True,
                            'source_cycles': sorted({int(r['frame_data']['source_cycle_id']) for r in records if 'source_cycle_id' in r['frame_data']})})
        assert batches[0]['session_id'] == batches[1]['session_id']
        if 'source_repeat_count' in verified:
            assert batches[0]['source_cycles'] == list(range(verified['source_repeat_count']))
        report.update(status='passed', batches=batches, matched_decoded_frames=sum(counts), reconnection=True, cycle_boundary=any(len(b['cycles']) > 1 for b in batches))
    except BaseException:
        errors.append(traceback.format_exc())
        report['status'] = 'failed'
        raise
    finally:
        stop.set()
        if worker.is_alive():
            worker.join(10)
        server.terminate()
        server.wait(timeout=10)
        log.close()
        report.update(errors=errors, session_logs=session_logs)
        if errors:
            report['status'] = 'failed'
        write_json(output / 'checks.json', report)
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
