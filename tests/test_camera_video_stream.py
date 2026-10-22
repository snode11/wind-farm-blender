"""RTP integrity, wraparound and optional real MediaMTX integration.

Set WFRL_MEDIAMTX to an existing local binary to enable the integration test.
It opens ephemeral loopback-only ports and cleans up every child process.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from wfrl.camera_video.stream import AccessUnits, MappingServer, mediamtx_config, packetize, publish, receive, vcl_hash
from test_camera_video_media import encoded_package


def _port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def test_fragmented_access_unit_wrap_and_missing_packet():
    nals = [b'\x67abc', b'\x65' + b'x' * 8000]
    packets = list(packetize(nals, (1 << 32) + 123, 777, 65534))
    assembler = AccessUnits()
    outputs = [result for p in packets if (result := assembler.feed(p))]
    assert outputs == [(777, 123, nals)]
    bad = AccessUnits()
    assert not any(bad.feed(p) for p in packets[:2] + packets[3:])
    assert bad.discontinuities > 0
    assert vcl_hash(nals) == vcl_hash([b'\x67different'] + nals[1:])


def test_mapping_session_and_hash_required():
    record = {'session_id': 'one', 'rtp_ssrc': 77, 'rtp_timestamp': 99, 'vcl_sha256': 'abc'}
    with MappingServer(0) as server:
        server.add(record)
        url = f'http://127.0.0.1:{server.server.server_port}?timestamp=99&vcl_sha256=abc&session_id='
        assert json.load(urlopen(url + 'one')) == record
        with pytest.raises(HTTPError):
            urlopen(url + 'two')


def test_local_config_disables_other_listeners():
    config = mediamtx_config()
    assert 'rtspAddress: 127.0.0.1:' in config
    for service in ('rtmp', 'hls', 'webrtc', 'srt', 'moq', 'api', 'metrics', 'pprof'):
        assert f'{service}: no' in config


def test_tampered_package_rejected_before_network(encoded_package):
    (encoded_package / 'frames.csv').write_text('tampered')
    with pytest.raises(ValueError, match='checksum'):
        publish(encoded_package)


@pytest.mark.skipif(not os.environ.get('WFRL_MEDIAMTX'), reason='Set WFRL_MEDIAMTX to run the real RTSP integration')
def test_rtsp_midjoin_reconnect_cycles_restart_wrap_and_decode(encoded_package, tmp_path):
    port, mapping_port = _port(), _port()
    config = tmp_path / 'mediamtx.yml'; config.write_text(mediamtx_config(port))
    url = f'rtsp://127.0.0.1:{port}/windfarm/camera1'
    mapping_url = f'http://127.0.0.1:{mapping_port}'
    log = (tmp_path / 'mediamtx.log').open('w')
    process = subprocess.Popen([os.environ['WFRL_MEDIAMTX'], str(config)], cwd=tmp_path, stdout=log, stderr=log)
    threads, stops, failures = [], [], []
    try:
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.1):
                    break
            except OSError:
                if process.poll() is not None:
                    pytest.fail('MediaMTX failed: ' + (tmp_path / 'mediamtx.log').read_text())
                time.sleep(.05)
        sessions = []
        for run in range(2):
            stop = threading.Event(); stops.append(stop)
            def run_publisher():
                try:
                    publish(encoded_package, url, mapping_port=mapping_port, stop_event=stop,
                            initial_timestamp=(1 << 32) - 180000)
                except BaseException as exc:
                    failures.append(exc)
            thread = threading.Thread(target=run_publisher); threads.append(thread); thread.start()
            time.sleep(.6)  # intentionally join after publication began
            received = receive(url, mapping_url, frame_count=50, output_dir=tmp_path / f'receiver-{run}')
            assert len({r['cycle_id'] for r in received}) >= 2
            assert {r['rtp_wrap_count'] for r in received} == {0, 1}
            assert all(r['frame_id'] == int(r['frame_data']['frame_id']) for r in received)
            assert all(b['media_timestamp'] - a['media_timestamp'] == 4500 for a, b in zip(received, received[1:]))
            sessions.append(received[0]['session_id'])
            reconnected = receive(url, mapping_url, frame_count=5)
            assert reconnected[0]['session_id'] == sessions[-1]
            for recording in [tmp_path / f'receiver-{run}' / 'received.h264']:
                subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(recording), '-f', 'null', '-'], check=True)
                result = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-count_frames', '-show_entries',
                    'stream=nb_read_frames', '-of', 'json', str(recording)], text=True))
                assert int(result['streams'][0]['nb_read_frames']) == len(received)
            stop.set(); thread.join(5)
            assert not thread.is_alive()
        assert sessions[0] != sessions[1]
        assert not failures
    finally:
        for stop in stops:
            stop.set()
        for thread in threads:
            thread.join(5)
        process.terminate()
        process.wait(timeout=5)
        log.close()


def test_sender_report_counter_and_ntp_era_wrap():
    import struct
    from wfrl.camera_video.stream import sender_report
    encoded = sender_report(77, (1 << 32) + 99, (1 << 32) + 7, (1 << 32) + 123,
                            unix_time=(1 << 32) - 2208988800 + .25)
    assert struct.unpack('!BBHIIIIII', encoded) == (0x80, 200, 6, 77, 0, 1 << 30, 99, 7, 123)
    with pytest.raises(ValueError, match='negative'):
        sender_report(77, 99, -1, 0)


def test_sender_report_counts_actual_packets_and_payloads(encoded_package, monkeypatch):
    import struct
    from wfrl.camera_video import stream
    sent = []
    class Connection:
        def __init__(self, url):
            pass
        def request(self, *args, **kwargs):
            pass
        def send_packet(self, packet, channel=0):
            sent.append((channel, packet))
        def close(self):
            pass
    monkeypatch.setattr(stream, 'RTSPConnection', Connection)
    log = stream.publish(encoded_package, cycles=1, mapping_port=0)
    packet_count = octets = reports = 0
    for channel, packet in sent:
        if channel == 0:
            packet_count += 1
            octets += len(packet) - 12
        else:
            report = struct.unpack('!BBHIIIIII', packet)
            assert report[-2:] == (packet_count, octets)
            assert packet_count > 0 and octets > 0
            reports += 1
    assert reports == 2
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert all(r['actual_send_elapsed_s'] >= r['media_timestamp'] / 90000 for r in rows)
    assert all(r['pacing_lag_s'] >= 0 for r in rows)
    assert rows[-1]['actual_send_elapsed_s'] >= rows[0]['actual_send_elapsed_s']
