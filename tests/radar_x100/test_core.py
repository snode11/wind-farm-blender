"""Binary, time, geometry and acquisition contracts independent of Blender."""
from dataclasses import asdict, replace
import hashlib
import json
import struct
import threading
import time
import numpy as np
import pytest
from wfrl.radar_x100 import schema, ply, recording, simulation, channel, acquisition


def frame(sequence=0, *, stream='NEAR', count=1, session='boot-a', source='TEST'):
    points = np.zeros(count, dtype=schema.POINT_DTYPE)
    points['x'] = 10
    points['rcs'] = -12
    points['label'] = 237
    return schema.RadarFrame('X100', session, sequence, source, stream, points)


def test_ply_layout_endian_unknown_attributes_and_nonfinite():
    header = ply.encode_ply(np.zeros(1, dtype=schema.POINT_DTYPE))[:-26]
    raw = header + struct.pack('<fffBfffB', 1.25, -2.5, float('nan'), 1, 3., -12., -4., 237)
    parsed = ply.decode_ply(raw)
    assert parsed.dtype.itemsize == 26 and parsed['rcs'][0] == -12 and parsed['label'][0] == 237
    observation = replace(frame(), points=parsed)
    assert not observation.finite_mask[0] and observation.radial_velocity_m_s is None
    assert observation.time.source_time_s is None
    assert ply.encode_ply(parsed) == raw
    assert len(ply.decode_ply(ply.encode_ply(np.zeros(0, dtype=schema.POINT_DTYPE)))) == 0


@pytest.mark.parametrize('mutate', [
    lambda raw: raw[:-1], lambda raw: raw + b'\0',
    lambda raw: raw.replace(b'binary_little_endian', b'binary_big_endian'),
    lambda raw: raw.replace(b'property float rcs\n', b''),
    lambda raw: raw.replace(b'property uchar label', b'property float label'),
    lambda raw: raw.replace(b'element vertex 1', b'element vertex 4097'),
    lambda raw: raw.replace(b'property float x', b'property float y'),
])
def test_ply_rejects_unsupported_and_incomplete(mutate):
    with pytest.raises(ValueError):
        ply.decode_ply(mutate(ply.encode_ply(frame().points)))


def test_ply_rejects_unknown_ms_but_preserves_unknown_labels():
    points = frame().points.copy()
    points['ms'] = 3
    with pytest.raises(ValueError, match='ms'):
        ply.decode_ply(ply.encode_ply(points))


def test_record_replay_identity_empty_velocity_seek_and_integrity(tmp_path):
    path = tmp_path / 'recording'
    sink = recording.Recorder(path)
    first = replace(frame(), radial_velocity_m_s=np.array([-1.5]), material_ids=(42,),
                    time=schema.FrameTime('device:boot-a', 100., (99.99, 100.01), 'CENTER', 'SOURCE'))
    sink.append(first, 0.)
    sink.append(frame(1, count=0), .1)
    sink.append(frame(2), .2)
    sink.close()
    reader = recording.Replay(path)
    assert reader.at(-.1) is None
    assert reader.at(.09).identity == first.identity
    assert reader.at(.1).validity == 'VALID_EMPTY'
    assert reader.at(.2).sequence == 2 and reader.at(0).sequence == 0
    assert reader.at(0).radial_velocity_m_s[0] == -1.5 and reader.at(0).material_ids == (42,)
    assert reader.at(0).time == first.time
    point_file = path / 'frames/00000000.ply'
    point_file.write_bytes(ply.encode_ply(frame(0, count=0).points))
    with pytest.raises(ValueError, match='digest'):
        reader.frame(0)


def test_record_failure_never_becomes_complete(tmp_path, monkeypatch):
    sink = recording.Recorder(tmp_path / 'failed')
    sink.append(frame(), 0)
    original = recording._atomic
    def fail(path, raw):
        if path.suffix == '.ply':
            raise OSError('disk full')
        return original(path, raw)
    monkeypatch.setattr(recording, '_atomic', fail)
    with pytest.raises(OSError):
        sink.append(frame(1), .1)
    sink.close()
    assert sink.state == 'FAILED' and (sink.path / 'INCOMPLETE').exists()
    manifest = json.loads((sink.path / 'manifest.json').read_text())
    assert manifest['frames'] == 1 and 'disk full' in manifest['error']
    with pytest.raises(ValueError, match='incomplete'):
        recording.Replay(sink.path)


def test_clock_mapping_bounds_error_and_reference():
    mapping = schema.ClockMapping('sync-1', 'device:boot-a', 'camera:boot-c', 1.0002, 2., (10, 20), .005, 'independent synchronization observations')
    assert mapping.map(15, target_domain='camera:boot-c', tolerance_s=.01) == pytest.approx(17.003)
    for t, target, tolerance in [(9, 'camera:boot-c', .01), (15, 'other', .01), (15, 'camera:boot-c', .001), (15, 'camera:boot-c', float('nan'))]:
        with pytest.raises(ValueError):
            mapping.map(t, target_domain=target, tolerance_s=tolerance)
    with pytest.raises(ValueError):
        schema.FrameTime('device:boot-b', 15, mapping=asdict(mapping))


def moving_scene(t):
    # Two aligned triangles: the near material moves at 3 m/s, the sensor at 1 m/s.
    vertices = np.array([[10,-1,-1],[10,1,-1],[10,0,2],[20,-1,-1],[20,1,-1],[20,0,2]], dtype=float)
    vertices[:, 0] += 3*t
    pose = np.eye(4)
    pose[0, 3] = t
    return simulation.SceneSample(t, 'source-a', 'topology-a', vertices, np.array([[0,1,2],[3,4,5]]), pose, 'two triangles')


def test_simulation_same_material_velocity_fov_occlusion_and_determinism():
    kwargs = dict(sensor_id='X100', session_id='sim', sequence=30, interval_s=(0,2), candidate_budget=2)
    result = simulation.simulate(moving_scene, 1, **kwargs)
    assert len(result.points) == 1 and result.material_ids == (0,)
    assert result.points['x'][0] == pytest.approx(12)
    assert result.radial_velocity_m_s[0] == pytest.approx(2, abs=1e-5)
    assert np.isnan(result.points['rcs']).all() and result.provenance['occluded'] == 1
    assert result.time.acquisition_interval_s == (1,1)
    assert result.points.tobytes() == simulation.simulate(moving_scene, 1, **kwargs).points.tobytes()
    az, el = np.radians([60,22])
    boundary = np.array([[np.cos(el)*np.cos(az), np.cos(el)*np.sin(az), np.sin(el)]])
    assert simulation.in_fov(boundary * 10)[0]
    assert not simulation.in_fov(np.array([[-1,0,0],[.1,0,0],[397,0,0]])).any()


def test_channel_atomic_fragmented_packets_identity_restart_and_independent_expiry():
    parser = channel.PacketReader()
    raw = channel.hello(frame()) + channel.encode_frame(frame())
    for byte in raw[:-1]:
        parser.feed(bytes([byte]))
    peer = parser.pop()
    assert peer['type'] == 'HELLO' and parser.pop() is None
    parser.feed(raw[-1:])
    parsed = parser.pop()
    state = channel.LiveState(.5)
    state.handshake(peer)
    assert state.accept(parsed, 1.)
    assert not state.accept(parsed, 1.1)
    state.accept(frame(1), 1.2)
    state.accept(frame(0, stream='FAR'), 1.6)
    assert state.status('NEAR', 1.8) == 'STALE' and state.status('FAR', 1.8) == 'VALID'
    assert state.streams['NEAR'].display_drops == 1
    state.accept(frame(2, count=0), 1.9)
    assert state.status('NEAR', 2.) == 'VALID_EMPTY'
    with pytest.raises(ValueError, match='reconnect'):
        state.accept(frame(session='boot-b'))
    state.handshake(dict(type='HELLO', schema=schema.SCHEMA, sensor_id='X100',session_id='boot-b',source_kind='TEST'))
    state.accept(frame(session='boot-b'), 2.1)
    assert len(state.streams) == 1
    state.connected = False
    assert state.status('NEAR', 2.1) == 'DISCONNECTED'


def test_channel_bounds_and_array_length():
    reader = channel.PacketReader()
    reader.feed(channel.HEADER.pack(channel.MAGIC, channel.MAX_METADATA + 1, 0))
    with pytest.raises(ValueError):
        reader.pop()
    reader = channel.PacketReader()
    metadata = dict(type='FRAME', count=2, velocity=False, frame=frame().metadata())
    reader.feed(channel.packet(metadata, frame().points.tobytes()))
    with pytest.raises(ValueError, match='length'):
        reader.pop()


def test_assembly_reordering_loss_duplicate_and_explicit_empty():
    assembler = acquisition.Assembler()
    template = frame(count=0)
    first = acquisition.Fragment(template, 0, 2, frame().points)
    last = acquisition.Fragment(template, 1, 2, frame().points, terminal=True)
    assert assembler.push(last, now=1) is None
    complete = assembler.push(first, now=1.01)
    assert len(complete.points) == 2
    assert assembler.push(first, now=1.02) is None and assembler.counters['duplicate'] == 1
    missing = acquisition.Fragment(frame(1,count=0), 0, 2, frame().points)
    assert assembler.push(missing, now=1.1) is None
    assembler.expire(2)
    assert assembler.counters['timeout'] == 1
    assert assembler.push(acquisition.Fragment(frame(1,count=0),1,2,frame().points,terminal=True),now=2.01) is None
    empty = assembler.push(acquisition.Fragment(frame(2,count=0), 0, 1, frame(count=0).points, terminal=True), now=2.1)
    assert empty.validity == 'VALID_EMPTY'
    bad = acquisition.Fragment(frame(0,count=0,session='boot-b'),0,1,frame().points,terminal=True)
    assert assembler.push(bad,now=2.2) is None
    assembler.begin_session('X100','boot-b')
    assert assembler.push(bad,now=2.3).session_id == 'boot-b'


def test_assembly_conflicting_duplicate_retires_corrupt_unit():
    assembler = acquisition.Assembler()
    template = frame(count=0)
    fragment = acquisition.Fragment(template,0,2,frame().points)
    assert assembler.push(fragment,now=1) is None
    assert assembler.push(fragment,now=1.01) is None
    different = frame().points.copy()
    different['x'] = 11
    assert assembler.push(acquisition.Fragment(template,0,2,different),now=1.02) is None
    assert assembler.counters['duplicate'] == 1 and assembler.counters['corrupt'] == 1
    assert assembler.push(acquisition.Fragment(template,1,2,frame().points,terminal=True),now=1.03) is None
    assert not assembler.pending


def test_record_backpressure_fails_without_stopping_latest_display(tmp_path):
    sink = recording.Recorder(tmp_path / 'bounded')
    entered, release = threading.Event(), threading.Event()
    append = sink.append
    def slow(frame, t):
        entered.set()
        assert release.wait(2)
        append(frame,t)
    sink.append = slow
    fanout = acquisition.Fanout(sink, record_capacity=1)
    fanout.submit(frame(0), 0)
    assert entered.wait(2)
    fanout.submit(frame(1), .1)
    fanout.submit(frame(2), .2)
    fanout.submit(frame(3), .3)
    assert fanout.take_latest().sequence == 3
    release.set()
    fanout.close()
    assert sink.state == 'FAILED' and fanout.record_failures > 0 and fanout.display_drops == 3
    assert (sink.path / 'INCOMPLETE').exists()


def test_actual_local_socket_full_frame_and_disconnect():
    fanout = acquisition.Fanout()
    publisher = acquisition.LocalPublisher(fanout, port=0)
    client = channel.LocalClient(publisher.port)
    try:
        fanout.submit(frame(4, count=4096), 0)
        deadline = time.monotonic() + 3
        while client.state.status('NEAR') == 'WAITING' or client.state.peer is None:
            client.poll()
            assert time.monotonic() < deadline
            time.sleep(.005)
        assert client.state.streams['NEAR'].frame.sequence == 4
        assert len(client.state.streams['NEAR'].frame.points) == 4096
        publisher.close()
        while client.state.connected:
            client.poll()
            assert time.monotonic() < deadline
            time.sleep(.005)
        assert client.state.status('NEAR') == 'DISCONNECTED'
    finally:
        client.close()
        publisher.close()
        fanout.close()


def test_input_failure_without_recording_stops_worker_cleanly():
    fanout = acquisition.Fanout()
    fanout.failure = 'Adapter input failed'
    time.sleep(.06)
    fanout.close()
    assert not fanout.worker.is_alive() and fanout.record_failures == 0


def test_explicit_ply_import_preserves_bytes_and_marks_fps_derivation(tmp_path):
    from scripts.radar_x100.import_ply import import_index
    raw = ply.encode_ply(frame().points).replace(b'element vertex',b'comment original source bytes\nelement vertex')
    (tmp_path/'original.ply').write_bytes(raw)
    index = tmp_path/'index.json'
    index.write_text(json.dumps(dict(sensor_id='X100',source_kind='TEST',fps=30,
        frames=[dict(file='original.ply',sequence=9,replay_time_s=0)])))
    destination = tmp_path/'imported'
    assert import_index(index,destination) == 1
    imported = recording.Replay(destination).frame(0)
    assert imported.time.quality == 'FPS_DERIVED'
    assert imported.time.acquisition_interval_s is None
    assert (destination/'frames/00000000.ply').read_bytes() == raw
