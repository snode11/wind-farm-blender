"""WFRL v1 bounded JSON wire protocol; standard library only.

Copied verbatim as protocol.py into the Blender extension at build time.
"""
import json
import math
import struct

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 1024 * 1024
STALE_AFTER_SECONDS = {"telemetry": 2.0, "curve": 2.0, "wake": 5.0, "safety_event": 10.0}
TRAINING_STATS_CAPABILITY = 'training_stats_v1'
TRAINING_STATS_STALE_AFTER_SECONDS = 30.0
TRAINING_STATS_CHANNELS = frozenset(('mean_power', 'mean_reward', 'value_loss', 'explained_variance', 'episode_return', 'learning_rate'))
SCALAR_NUMERIC_CHANNELS = frozenset(('rotor_speed', 'power', 'yaw', 'pitch', 'wind_speed', 'wind_direction', 'torque', 'reward'))
MODES = frozenset(('demo', 'interactive_training', 'formal_training', 'replay'))
RUN_STATUSES = frozenset(('READY', 'STARTING', 'RUNNING', 'PAUSED', 'DRAINING', 'STOPPED', 'FAILED'))
MESSAGE_TYPES = frozenset(('hello', 'hello_ack', 'command', 'lifecycle', 'snapshot', 'safety_event', 'curve', 'wake', 'error', 'training_stats'))


class ProtocolError(ValueError):
    """Invalid, unsupported, oversized or out-of-order protocol input."""


def _require(condition, message):
    if not condition:
        raise ProtocolError(message)


def _integer(value):
    return type(value) is int and 0 <= value <= 2**53 - 1


def _number(value):
    return type(value) in (float, int) and math.isfinite(value)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _finite_tree(value, depth=0):
    _require(depth <= 64, 'JSON nesting exceeds 64')
    if isinstance(value, dict):
        _require(all(isinstance(k, str) for k in value), 'JSON keys must be strings')
        for key in value:
            try: key.encode('utf-8')
            except UnicodeEncodeError as exc: raise ProtocolError('String is not valid UTF-8') from exc
        for child in value.values(): _finite_tree(child, depth + 1)
    elif isinstance(value, list):
        for child in value: _finite_tree(child, depth + 1)
    elif isinstance(value, str):
        try: value.encode('utf-8')
        except UnicodeEncodeError as exc: raise ProtocolError('String is not valid UTF-8') from exc
    elif type(value) in (int, float):
        _require(_number(value), 'Non-finite number; use null and an invalidity reason')
    else:
        _require(value is None or type(value) is bool, 'Value is not JSON compatible')


def normalize_rotor_speed(mapping):
    """Normalize one backend channel mapping, rejecting conflicting aliases."""
    result = dict(mapping)
    if 'rotorspeed' in result:
        if 'rotor_speed' in result:
            _require(result['rotor_speed'] == result['rotorspeed'], 'Conflicting rotor_speed aliases')
        result['rotor_speed'] = result.pop('rotorspeed')
    return result


def _channel(channel, stale_after_seconds=2.0):
    _require(isinstance(channel, dict), 'Channel must be an object')
    required = {'value', 'unit', 'validity', 'error', 'fidelity', 'provenance', 'source_age_seconds', 'stale_after_seconds'}
    _require(required <= channel.keys(), 'Missing channel metadata')
    validity = channel['validity']
    _require(validity in ('unsupported', 'waiting', 'valid', 'stale', 'invalid'), 'Invalid validity')
    _require(isinstance(channel['unit'], str), 'Unit must be text')
    if validity == 'valid':
        _require(channel['value'] is not None and channel['error'] is None, 'Valid channel needs value and null error')
    else:
        _require(_text(channel['error']), 'Nonvalid channel needs an error reason')
        if validity != 'stale':
            _require(channel['value'] is None, 'Missing or invalid values must be null')
    _require(channel['fidelity'] in ('SYNTH', 'DIRECT', 'EXPORTED'), 'Invalid fidelity')
    provenance = channel['provenance']
    _require(isinstance(provenance, dict) and bool(provenance), 'Missing provenance')
    source = {'SYNTH': 'formula', 'DIRECT': 'backend_session', 'EXPORTED': 'file'}[channel['fidelity']]
    _require(_text(provenance.get(source)), 'Missing provenance ' + source)
    _require(_text(provenance.get('channel')) or channel['fidelity'] == 'SYNTH', 'Missing provenance channel')
    for key in ('source_age_seconds', 'stale_after_seconds'):
        _require(_number(channel[key]) and channel[key] >= 0, 'Invalid ' + key)


    _require(channel['stale_after_seconds'] == stale_after_seconds, 'Staleness threshold disagrees with v1 channel policy')


def data_age_seconds(channel, received_monotonic, now_monotonic):
    """Elapsed receive age uses only the local monotonic clock."""
    _require(_number(received_monotonic) and _number(now_monotonic) and now_monotonic >= received_monotonic, 'Invalid monotonic age')
    age = channel.get('source_age_seconds')
    _require(_number(age) and age >= 0, 'Invalid source age')
    return age + now_monotonic - received_monotonic


def channel_is_stale(channel, received_monotonic, now_monotonic):
    return channel.get('validity') == 'stale' or data_age_seconds(channel, received_monotonic, now_monotonic) >= channel['stale_after_seconds']


def _channels(mapping):
    _require(isinstance(mapping, dict), 'Channels must be an object')
    _require('rotorspeed' not in mapping, 'Wire key must be canonical rotor_speed')
    for key, channel in mapping.items():
        _require(_text(key), 'Empty channel name')
        _channel(channel)
        if key in SCALAR_NUMERIC_CHANNELS and channel['value'] is not None:
            _require(_number(channel['value']), key + ' must be a finite scalar number')
        if key == 'rotor_speed': _require(channel['unit'] == 'rpm', 'rotor_speed unit must be rpm')


def _data_context(payload):
    _require(payload.get('mode') in MODES, 'Invalid business mode')
    _require(_integer(payload.get('step')), 'Invalid step')
    timestamp = payload.get('timestamp')
    _require(isinstance(timestamp, dict), 'Missing timestamp')
    _require(_number(timestamp.get('value')), 'Invalid timestamp value')
    _require(timestamp.get('timebase') in ('simulation_seconds', 'unix_seconds', 'replay_seconds'), 'Invalid timestamp timebase')


def _training_stats(payload):
    _data_context(payload)
    _require(payload['timestamp']['timebase'] == 'unix_seconds', 'Training timestamp must use unix_seconds')
    _require(_text(payload.get('run_id')), 'Training run_id required')
    _require(payload.get('record_kind') in ('progress', 'iteration_stats'), 'Invalid training record_kind')
    for key in ('iteration', 'agent_step'):
        _require(_integer(payload.get(key)), 'Invalid ' + key)
    if 'episode' in payload:
        _require(_integer(payload['episode']), 'Invalid episode')
    _require(payload.get('phase') in ('warmup', 'sampling', 'updating', 'done', 'waiting'), 'Invalid training phase')
    _require(_number(payload.get('source_age_seconds')) and payload['source_age_seconds'] >= 0, 'Invalid training source age')
    _require(_number(payload.get('stale_after_seconds')) and payload['stale_after_seconds'] == TRAINING_STATS_STALE_AFTER_SECONDS, 'Invalid training staleness threshold')
    stats = payload.get('stats')
    _require(isinstance(stats, dict), 'Training stats must be an object')
    if payload['record_kind'] == 'progress':
        _require(not stats, 'Progress stats must be empty')
    else:
        _require(TRAINING_STATS_CHANNELS <= stats.keys(), 'Missing training statistic channels')
    for name, channel in stats.items():
        _require(_text(name), 'Empty training statistic name')
        _channel(channel, TRAINING_STATS_STALE_AFTER_SECONDS)
        _require(channel['value'] is None or _number(channel['value']), 'Training statistic must be a finite scalar')
        if name == 'mean_power':
            _require(channel['unit'] == 'MW', 'mean_power unit must be MW')


def validate_training_stats_payload(payload):
    """Validate a JSONL-derived payload using the same wire contract."""
    try:
        _require(isinstance(payload, dict), 'Payload must be an object')
        _finite_tree(payload)
        _training_stats(payload)
        return payload
    except (TypeError, OverflowError, RecursionError, UnicodeError) as exc:
        raise ProtocolError(str(exc)) from exc


def _validate_message(message):
    _require(isinstance(message, dict), 'Message must be an object')
    _finite_tree(message)
    _require({'protocol_version', 'type', 'session_id', 'sequence', 'payload'} <= message.keys(), 'Missing envelope fields')
    _require(type(message['protocol_version']) is int and message['protocol_version'] == 1, 'Unsupported protocol version')
    kind = message['type']
    _require(kind != 'wake_binary', 'Unsupported binary wake frame: not negotiated in v1 JSON')
    _require(isinstance(kind, str) and kind in MESSAGE_TYPES, 'Unsupported message type')
    _require(_integer(message['sequence']), 'Invalid sequence')
    _require(isinstance(message['session_id'], str), 'Invalid session_id')
    _require(_text(message['session_id']) or kind == 'hello', 'Session required after hello')
    p = message['payload']
    _require(isinstance(p, dict), 'Payload must be an object')
    if kind in ('hello_ack', 'lifecycle') and 'run_id' in p:
        _require(p['run_id'] is None or _text(p['run_id']), 'Invalid run_id')
    if kind == 'hello':
        _require(message['session_id'] == '' and message['sequence'] == 0, 'hello must use empty session and sequence zero')
        versions = p.get('supported_versions')
        _require(isinstance(versions, list) and 1 in versions and all(type(v) is int for v in versions), 'No compatible protocol version')
        _require('resume_session_id' in p and (p['resume_session_id'] is None or _text(p['resume_session_id'])), 'Invalid resume_session_id')
    if kind in ('hello', 'hello_ack'):
        caps = p.get('capabilities')
        _require(isinstance(caps, list) and all(_text(c) for c in caps), 'Invalid capabilities')
        _require(not any('binary' in c for c in caps), 'Unsupported binary capability')
    if kind == 'hello_ack':
        _require(type(p.get('selected_version')) is int and p['selected_version'] == 1, 'Unsupported selected version')
        _require(p.get('run_status') in RUN_STATUSES and p.get('mode') in MODES, 'Invalid session state')
        _require(type(p.get('resumed')) is bool, 'Missing resumed flag')
    elif kind == 'command':
        _require(p.get('command') in ('scene.load', 'run.start', 'run.pause', 'run.resume', 'run.step', 'run.stop', 'run.reset', 'session.sync', 'channel.set'), 'Unknown command')
        _require(isinstance(p.get('arguments'), dict), 'Command arguments required')
    elif kind == 'lifecycle':
        _require(p.get('run_status') in RUN_STATUSES, 'Invalid run status')
        _require('reason' in p and (p['reason'] is None or _text(p['reason'])), 'Lifecycle reason required')
        _require(p.get('mode') in MODES, 'Invalid lifecycle mode')
        caps = p.get('capabilities')
        _require(isinstance(caps, list) and all(_text(c) for c in caps), 'Invalid lifecycle capabilities')
        _require(not any('binary' in c for c in caps), 'Unsupported binary capability')
    elif kind in ('snapshot', 'curve', 'wake', 'safety_event'):
        _data_context(p)
        if kind == 'snapshot':
            _channels(p.get('farm'))
            turbines = p.get('turbines')
            _require(isinstance(turbines, list), 'Turbines must be a list')
            ids = set()
            for turbine in turbines:
                _require(isinstance(turbine, dict) and _text(turbine.get('turbine_id')), 'Stable turbine_id required')
                _require(turbine['turbine_id'] not in ids, 'Duplicate turbine_id')
                ids.add(turbine['turbine_id']); _channels(turbine.get('channels'))
        elif kind == 'curve':
            _require(_text(p.get('channel')), 'Curve channel required')
            _require(p.get('channel') != 'rotorspeed', 'Wire key must be canonical rotor_speed')
            _require('turbine_id' in p and (p['turbine_id'] is None or _text(p['turbine_id'])), 'Curve scope required')
            _channel(p.get('data'))
            if p['channel'] == 'rotor_speed':
                _require(p['data']['unit'] == 'rpm', 'rotor_speed unit must be rpm')
            _require(isinstance(p['data']['value'], list) or p['data']['value'] is None, 'Curve value must be points or null')
            for point in p['data']['value'] or []:
                _require(isinstance(point, list) and len(point) == 2 and all(_number(x) for x in point), 'Curve points must be finite [time,value] pairs')
        elif kind == 'wake':
            _require(p.get('encoding') == 'json', 'Unsupported binary wake encoding')
            _require(isinstance(p.get('turbine_ids'), list) and all(_text(x) for x in p['turbine_ids']), 'Wake ID order required')
            _require(len(set(p['turbine_ids'])) == len(p['turbine_ids']), 'Duplicate wake turbine IDs')
            _channel(p.get('data'), STALE_AFTER_SECONDS['wake'])
        else:
            _require(_text(p.get('event_id')) and p.get('severity') in ('info', 'warning', 'critical') and _text(p.get('message')), 'Invalid safety event')
            _require('turbine_id' in p and (p['turbine_id'] is None or _text(p['turbine_id'])), 'Safety scope required')
            _channel(p.get('data'), STALE_AFTER_SECONDS['safety_event'])
    elif kind == 'training_stats':
        _training_stats(p)
    elif kind == 'error':
        _require(_text(p.get('code')) and _text(p.get('message')) and type(p.get('fatal')) is bool, 'Invalid error payload')
    return message


def validate_message(message):
    try:
        return _validate_message(message)
    except (TypeError, OverflowError, RecursionError, UnicodeError) as exc:
        raise ProtocolError(str(exc)) from exc


def encode_message(message):
    try:
        validate_message(message)
        body = json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (TypeError, OverflowError, RecursionError, UnicodeError) as exc:
        raise ProtocolError(str(exc)) from exc
    _require(0 < len(body) <= MAX_FRAME_BYTES, 'Frame exceeds size limit')
    return struct.pack('!I', len(body)) + body


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'Duplicate JSON key')
        result[key] = value
    return result


class FrameDecoder:
    def __init__(self, max_frame_bytes=MAX_FRAME_BYTES):
        _require(type(max_frame_bytes) is int and 0 < max_frame_bytes <= MAX_FRAME_BYTES, 'Invalid frame limit')
        self.max_frame_bytes = max_frame_bytes
        self._buffer = bytearray()
        self._length = None
        self.failed = False

    def feed(self, chunk):
        _require(not self.failed, 'Decoder failed; establish a new connection')
        _require(isinstance(chunk, (bytes, bytearray, memoryview)), 'Chunk must be bytes')
        result = []
        # Consume incrementally: an attacker cannot expand the internal buffer beyond one frame.
        view = memoryview(chunk)
        offset = 0
        try:
            while offset < len(view):
                target = 4 if self._length is None else self._length
                take = min(target - len(self._buffer), len(view) - offset)
                self._buffer.extend(view[offset:offset + take]); offset += take
                if len(self._buffer) != target: continue
                if self._length is None:
                    self._length = struct.unpack('!I', self._buffer)[0]
                    self._buffer.clear()
                    _require(0 < self._length <= self.max_frame_bytes, 'Invalid frame size')
                else:
                    body = bytes(self._buffer); self._buffer.clear(); self._length = None
                    message = json.loads(body.decode('utf-8'), object_pairs_hook=_pairs,
                                         parse_constant=lambda value: (_ for _ in ()).throw(ProtocolError('Non-finite JSON')))
                    result.append(validate_message(message))
        except (ValueError, TypeError, RecursionError, OverflowError) as exc:
            self.failed = True; self._buffer.clear()
            raise ProtocolError(str(exc)) from exc
        return result


class SessionSequenceGuard:
    """One receiving direction. Preserve this guard across a resumed connection."""
    def __init__(self, session_id, last_sequence=-1):
        _require(_text(session_id), 'Session required')
        _require(last_sequence == -1 or _integer(last_sequence), 'Invalid last sequence')
        self.session_id = session_id
        self.last_sequence = last_sequence

    def accept(self, message):
        validate_message(message)
        _require(message['session_id'] == self.session_id, 'Old or unrelated session')
        _require(message['sequence'] > self.last_sequence, 'Duplicate or reversed sequence')
        self.last_sequence = message['sequence']
        return message
