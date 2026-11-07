"""Malformed metadata and bounded binary headers cannot become canonical frames."""
import json
import numpy as np
import pytest
from wfrl.radar_x100 import channel, ply, schema


def frame():
    return schema.RadarFrame('X100', 'boot', 0, 'TEST', 'NEAR',
                            np.zeros(1, dtype=schema.POINT_DTYPE))


def decode_metadata(metadata, payload=b''):
    reader = channel.PacketReader()
    raw = json.dumps(metadata).encode('utf-8')
    reader.feed(channel.HEADER.pack(channel.MAGIC, len(raw), len(payload)) + raw + payload)
    return reader.pop()


def test_ply_header_limit_includes_entire_end_header_line():
    source = frame()
    raw = ply.encode_ply(source.points)
    padding = ply.MAX_HEADER - (len(raw) - source.points.nbytes)
    at_limit = raw.replace(b'end_header\n', b' ' * padding + b'end_header\n')
    assert ply.decode_ply(at_limit).tobytes() == source.points.tobytes()
    with pytest.raises(ValueError, match='header exceeds budget'):
        ply.decode_ply(at_limit.replace(b'end_header\n', b' end_header\n'))


@pytest.mark.parametrize('version', [None, 'x100-frame.v2'])
def test_packet_requires_explicit_supported_frame_schema(version):
    source = frame()
    metadata = source.metadata()
    if version is None:
        metadata.pop('schema')
    else:
        metadata['schema'] = version
    with pytest.raises(ValueError, match='schema'):
        decode_metadata(dict(type='FRAME', count=1, velocity=False, frame=metadata), source.points.tobytes())


@pytest.mark.parametrize('field,value', [
    ('sensor_id', ['X100']), ('sensor_id', 1), ('sensor_id', 'x' * 257),
    ('session_id', True), ('session_id', ''), ('source_kind', ['TEST']),
    ('schema', 'x100-frame.v2'),
])
def test_handshake_rejects_invalid_identity_and_version(field, value):
    metadata = dict(type='HELLO', schema=schema.SCHEMA, sensor_id='X100', session_id='boot', source_kind='TEST')
    metadata[field] = value
    with pytest.raises(ValueError, match='handshake'):
        decode_metadata(metadata)


@pytest.mark.parametrize('raw', [
    b'{"type":"HELLO","schema":"unknown","schema":"x100-frame.v1",'
    b'"sensor_id":"X100","session_id":"boot","source_kind":"TEST"}',
    b'{"type":"HELLO","schema":"x100-frame.v1","sensor_id":"X100",'
    b'"session_id":"boot","source_kind":"TEST","unknown":NaN}',
    b'{"type":"HELLO","schema":"x100-frame.v1","sensor_id":"X100",'
    b'"session_id":"boot","source_kind":"TEST","unknown":Infinity}',
])
def test_packet_rejects_ambiguous_or_nonfinite_json(raw):
    reader = channel.PacketReader()
    reader.feed(channel.HEADER.pack(channel.MAGIC, len(raw), 0) + raw)
    with pytest.raises(ValueError, match='Duplicate|Nonfinite'):
        reader.pop()


@pytest.mark.parametrize('metadata', [None, [], {}, {'type': 'FRAME'}, {'type': 'HELLO'}])
def test_packet_rejects_missing_metadata_fields_as_value_error(metadata):
    with pytest.raises(ValueError):
        decode_metadata(metadata)


@pytest.mark.parametrize('field,value', [
    ('time', None), ('time', {'clock_domain': ['device']}),
    ('time', {'source_time_s': True}), ('time', {'source_time_s': float('inf')}),
    ('spatial', []), ('spatial', {'frame': ['axes'], 'units': 'm'}),
    ('provenance', []), ('stream', []), ('sequence', True),
])
def test_frame_metadata_rejects_malformed_fields(field, value):
    source = frame()
    metadata = source.metadata()
    metadata[field] = value
    with pytest.raises(ValueError):
        schema.frame_from_metadata(metadata, source.points)


def test_clock_mapping_rejects_nonfinite_result():
    mapping = schema.ClockMapping('id', 'device', 'camera', 1e308, 0., (0., 10.), 0., 'test')
    with pytest.raises(ValueError, match='Nonfinite mapped time'):
        mapping.map(10., target_domain='camera', tolerance_s=0.)


def test_clock_mapping_requires_string_identity():
    with pytest.raises(ValueError, match='clock mapping'):
        schema.ClockMapping(['id'], 'device', 'camera', 1., 0., (0., 10.), 0., 'test')
