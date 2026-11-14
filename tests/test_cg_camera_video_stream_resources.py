"""RTSP resource ownership and complete receiver output publication."""
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from wfrl.camera_video import stream, publication


@pytest.fixture
def receiver(monkeypatch):
    session = '00000000-0000-0000-0000-000000000001'
    nals = [b'\x67sps', b'\x68pps', b'\x65frame']
    packets = list(stream.packetize(nals, 100, 7, 10))
    record = dict(session_id=session, rtp_timestamp=100,
                  vcl_sha256=stream.vcl_hash(nals), cycle_start=True, media_timestamp=0)
    class Connection:
        closed = False
        source_packets = packets
        mapping_record = record
        def __init__(self, *args, **kwargs):
            self.packets = iter(self.source_packets)
        def request(self, method, *args, **kwargs):
            return {}, f's=WFRL camera {session}\r\na=control:trackID=0\r\n'.encode()
        def receive_packet(self):
            try:
                return next(self.packets)
            except StopIteration:
                raise TimeoutError('fixture receive timeout')
        def close(self):
            Connection.closed = True
    monkeypatch.setattr(stream, 'RTSPConnection', Connection)
    monkeypatch.setattr(stream, 'urlopen', lambda *a, **kw: io.StringIO(json.dumps(record)))
    return Connection, b''.join(b'\x00\x00\x00\x01' + nal for nal in nals)


def receive_one(output, **kwargs):
    return stream.receive('rtsp://127.0.0.1/camera', 'http://127.0.0.1:8555',
                          frame_count=kwargs.pop('frame_count', 1), output_dir=output, **kwargs)


def test_receive_preserves_existing_mapping(receiver, tmp_path):
    existing = tmp_path / 'received.jsonl'
    existing.write_text('foreign mapping\n')
    with pytest.raises(FileExistsError):
        receive_one(tmp_path)
    assert existing.read_text() == 'foreign mapping\n'
    assert list(tmp_path.iterdir()) == [existing]


def test_receive_timeout_leaves_no_partial_recording(receiver, tmp_path):
    with pytest.raises(TimeoutError, match='fixture receive timeout'):
        receive_one(tmp_path, frame_count=2)
    assert receiver[0].closed
    assert not list(tmp_path.iterdir())


def test_receive_rejects_repeated_mapped_frame(receiver):
    connection = receiver[0]
    nals = [b'\x67sps', b'\x68pps', b'\x65frame']
    connection.source_packets = connection.source_packets + list(stream.packetize(nals, 100, 7, 13))
    with pytest.raises(ValueError, match='media timestamp'):
        receive_one(None, frame_count=2)
    assert connection.closed


@pytest.mark.parametrize('value', [-1, True, float('nan'), float('inf')])
def test_receive_rejects_invalid_media_timestamp(receiver, value):
    receiver[0].mapping_record['media_timestamp'] = value
    with pytest.raises(ValueError, match='media timestamp'):
        receive_one(None)
    assert receiver[0].closed


def test_receive_publishes_mapping_after_recording_is_complete(receiver, tmp_path, monkeypatch):
    real_commit = publication.commit_exclusive
    committed = []
    def commit(source, destination):
        destination = Path(destination)
        assert Path(source).parent != destination.parent
        assert Path(source).is_file()
        if destination.name == 'received.jsonl':
            assert (tmp_path / 'received.h264').read_bytes() == receiver[1]
            assert len(Path(source).read_text().splitlines()) == 1
        committed.append(destination.name)
        return real_commit(source, destination)
    monkeypatch.setattr(publication, 'commit_exclusive', commit)
    rows = receive_one(tmp_path)
    assert committed == ['received.h264', 'received.jsonl']
    assert (tmp_path / 'received.h264').read_bytes() == receiver[1]
    assert json.loads((tmp_path / 'received.jsonl').read_text()) == rows[0]
    assert {p.name for p in tmp_path.iterdir()} == {'received.h264', 'received.jsonl'}


def test_receive_commit_failure_rolls_back_only_owned_recording(receiver, tmp_path, monkeypatch):
    real_commit = publication.commit_exclusive
    def commit(source, destination):
        if Path(destination).name == 'received.jsonl':
            Path(destination).write_text('concurrent mapping\n')
        return real_commit(source, destination)
    monkeypatch.setattr(publication, 'commit_exclusive', commit)
    with pytest.raises(FileExistsError):
        receive_one(tmp_path)
    assert (tmp_path / 'received.jsonl').read_text() == 'concurrent mapping\n'
    assert {p.name for p in tmp_path.iterdir()} == {'received.jsonl'}
    assert receiver[0].closed


def test_receive_close_failure_still_closes_connection(receiver, tmp_path, monkeypatch):
    original = Path.open
    class FailingClose:
        def __init__(self, file):
            self.file = file
        def __getattr__(self, name):
            return getattr(self.file, name)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.close()
        def close(self):
            self.file.close()
            raise OSError('recording close failed')
    def open_file(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        return FailingClose(result) if path.name == 'received.h264' else result
    monkeypatch.setattr(Path, 'open', open_file)
    with pytest.raises(OSError, match='recording close failed'):
        receive_one(tmp_path)
    assert receiver[0].closed
    assert not list(tmp_path.iterdir())


def test_rtsp_makefile_failure_closes_socket(monkeypatch):
    class Socket:
        closed = False
        def makefile(self, *args):
            raise OSError('makefile failed')
        def close(self):
            self.closed = True
    sock = Socket()
    monkeypatch.setattr(stream.socket, 'create_connection', lambda *a, **kw: sock)
    with pytest.raises(OSError, match='makefile failed'):
        stream.RTSPConnection('rtsp://127.0.0.1/camera')
    assert sock.closed


def test_rtsp_file_close_failure_still_closes_socket():
    connection = stream.RTSPConnection.__new__(stream.RTSPConnection)
    def fail():
        raise OSError('file close failed')
    sock = SimpleNamespace(closed=False)
    sock.close = lambda: setattr(sock, 'closed', True)
    connection.file = SimpleNamespace(close=fail)
    connection.sock = sock
    with pytest.raises(OSError, match='file close failed'):
        connection.close()
    assert sock.closed


def test_mapping_thread_start_failure_closes_listener(monkeypatch):
    server = SimpleNamespace(closed=False, serve_forever=lambda: None)
    server.server_close = lambda: setattr(server, 'closed', True)
    monkeypatch.setattr(stream, 'ThreadingHTTPServer', lambda *a: server)
    def fail():
        raise RuntimeError('thread start failed')
    monkeypatch.setattr(stream.threading, 'Thread', lambda **kw: SimpleNamespace(start=fail))
    with pytest.raises(RuntimeError, match='thread start failed'):
        with stream.MappingServer():
            pytest.fail('failed server cannot be entered')
    assert server.closed


def test_mapping_thread_creation_failure_closes_listener(monkeypatch):
    server = SimpleNamespace(closed=False, serve_forever=lambda: None)
    server.server_close = lambda: setattr(server, 'closed', True)
    monkeypatch.setattr(stream, 'ThreadingHTTPServer', lambda *a: server)
    def fail(**kwargs):
        raise RuntimeError('thread creation failed')
    monkeypatch.setattr(stream.threading, 'Thread', fail)
    with pytest.raises(RuntimeError, match='thread creation failed'):
        stream.MappingServer()
    assert server.closed


def test_mapping_shutdown_failure_still_closes_listener_and_joins():
    owner = stream.MappingServer.__new__(stream.MappingServer)
    calls = []
    def fail():
        raise OSError('shutdown failed')
    owner.server = SimpleNamespace(shutdown=fail, server_close=lambda: calls.append('close'))
    owner.thread = SimpleNamespace(join=lambda: calls.append('join'))
    with pytest.raises(OSError, match='shutdown failed'):
        owner.__exit__()
    assert calls == ['close', 'join']
