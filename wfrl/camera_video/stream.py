"""Observable local RTSP/TCP publishing and an RTP-aware reference receiver.

The publisher packetizes already encoded AVCC H.264 samples (RFC 6184).
Mapping is committed after the entire access unit is sent. The receiver checks
complete RTP sequence continuity and the encoded VCL hash before pairing data.
Only loopback RTSP and HTTP endpoints are supported by this initial service.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import socket
import struct
import threading
import time
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen
import uuid

from .media import h264_samples, read_frames, read_sample, validate_video, sha256


def _local_url(url, scheme):
    parsed = urlparse(url)
    if parsed.scheme != scheme or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.username:
        raise ValueError(f'Initial service permits only unauthenticated loopback {scheme} URLs')
    return parsed


class RTSPConnection:
    def __init__(self, url, timeout=10):
        parsed = _local_url(url, 'rtsp')
        self.url = url
        self.sock = socket.create_connection((parsed.hostname, parsed.port or 554), timeout=timeout)
        self.file = self.sock.makefile('rb')
        self.cseq = 0
        self.session = None

    def request(self, method, url=None, headers=None, body=b''):
        self.cseq += 1
        fields = {'CSeq': str(self.cseq), 'User-Agent': 'WFRL-camera-video/1'}
        if self.session:
            fields['Session'] = self.session
        fields.update(headers or {})
        if body:
            fields['Content-Length'] = str(len(body))
        message = f'{method} {url or self.url} RTSP/1.0\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in fields.items()) + '\r\n'
        self.sock.sendall(message.encode() + body)
        while True:
            first = self.file.read(1)
            if first != b'$':
                break
            prefix = self.file.read(3)
            self.file.read(int.from_bytes(prefix[1:], 'big'))
        status = first + self.file.readline()
        response = {}
        while True:
            line = self.file.readline()
            if line in (b'\r\n', b'\n', b''):
                break
            key, value = line.decode().split(':', 1)
            response[key.lower()] = value.strip()
        payload = self.file.read(int(response.get('content-length', '0')))
        if not status.startswith(b'RTSP/1.0 200'):
            raise RuntimeError(f'RTSP {method} failed: {status.decode().strip()} {payload!r}')
        if 'session' in response:
            self.session = response['session'].split(';', 1)[0]
        return response, payload

    def send_packet(self, packet, channel=0):
        self.sock.sendall(b'$' + bytes([channel]) + struct.pack('!H', len(packet)) + packet)

    def keepalive(self):
        self.cseq += 1
        message = (f'GET_PARAMETER {self.url} RTSP/1.0\r\nCSeq: {self.cseq}\r\n'
                   f'Session: {self.session}\r\n\r\n')
        self.sock.sendall(message.encode())

    def receive_packet(self):
        while True:
            first = self.file.read(1)
            if not first:
                raise EOFError('RTSP publisher disconnected')
            if first != b'$':
                status = first + self.file.readline()
                if not status.startswith(b'RTSP/1.0 200'):
                    raise ValueError(f'Unexpected RTSP control response: {status!r}')
                fields = {}
                while True:
                    line = self.file.readline()
                    if line in (b'\r\n', b'\n', b''):
                        break
                    key, value = line.decode().split(':', 1)
                    fields[key.lower()] = value.strip()
                self.file.read(int(fields.get('content-length', '0')))
                continue
            prefix = self.file.read(3)
            length = int.from_bytes(prefix[1:], 'big')
            payload = self.file.read(length)
            if len(payload) != length:
                raise EOFError('Truncated interleaved packet')
            if prefix[0] == 0:
                return payload

    def close(self):
        self.file.close()
        self.sock.close()


def packetize(nals, timestamp, ssrc, sequence, mtu=1200):
    """Yield RFC 6184 single-NAL/FU-A RTP packets, marker only at AU end."""
    payloads = []
    for nal in nals:
        if len(nal) <= mtu:
            payloads.append(nal)
        else:
            chunks = [nal[i:i + mtu - 2] for i in range(1, len(nal), mtu - 2)]
            for i, chunk in enumerate(chunks):
                payloads.append(bytes([(nal[0] & 0xe0) | 28, (nal[0] & 31) | (0x80 if i == 0 else 0) | (0x40 if i == len(chunks) - 1 else 0)]) + chunk)
    for i, payload in enumerate(payloads):
        yield struct.pack('!BBHII', 0x80, 96 | (0x80 if i == len(payloads) - 1 else 0),
                          (sequence + i) & 0xffff, timestamp & 0xffffffff, ssrc) + payload


def sender_report(ssrc, timestamp, packet_count, octet_count, *, unix_time=None):
    """RFC 3550 SR: successful RTP sends, payload octets, NTP modulo era."""
    if packet_count < 0 or octet_count < 0:
        raise ValueError('RTP transmission counters cannot be negative')
    ntp = (time.time() if unix_time is None else unix_time) + 2208988800
    return struct.pack('!BBHIIIIII', 0x80, 200, 6, ssrc & 0xffffffff,
                       int(ntp) & 0xffffffff, int((ntp % 1) * (1 << 32)),
                       timestamp & 0xffffffff, packet_count & 0xffffffff,
                       octet_count & 0xffffffff)


def vcl_hash(nals):
    digest = hashlib.sha256()
    for nal in nals:
        if nal[0] & 31 in (1, 5):
            digest.update(struct.pack('!I', len(nal)))
            digest.update(nal)
    return digest.hexdigest()


class AccessUnits:
    """Reject incomplete access units; never pair a partial decoded frame."""
    def __init__(self):
        self.key = None
        self.sequence = None
        self.nals = []
        self.fragment = None
        self.invalid = False
        self.discontinuities = 0

    def feed(self, packet):
        if len(packet) < 12 or packet[0] != 0x80:
            raise ValueError('Only plain RTP v2 headers supported')
        _, marker_pt, sequence, timestamp, ssrc = struct.unpack('!BBHII', packet[:12])
        key = (ssrc, timestamp)
        if key != self.key:
            if self.nals or self.fragment is not None:
                self.discontinuities += 1
            self.key, self.nals, self.fragment, self.invalid = key, [], None, False
        if self.sequence is not None and sequence != (self.sequence + 1) & 0xffff:
            self.invalid = True
            self.discontinuities += 1
        self.sequence = sequence
        payload = packet[12:]
        if not payload:
            self.invalid = True
        elif payload[0] & 31 == 28:
            if len(payload) < 2:
                self.invalid = True
            elif payload[1] & 0x80:
                self.fragment = bytearray([(payload[0] & 0xe0) | (payload[1] & 31)]) + payload[2:]
            elif self.fragment is None:
                self.invalid = True
            else:
                self.fragment.extend(payload[2:])
            if len(payload) > 1 and payload[1] & 0x40 and self.fragment is not None:
                self.nals.append(bytes(self.fragment)); self.fragment = None
        elif payload[0] & 31 == 24:
            offset = 1
            while offset < len(payload):
                length = int.from_bytes(payload[offset:offset + 2], 'big'); offset += 2
                if not length or offset + length > len(payload):
                    self.invalid = True; break
                self.nals.append(payload[offset:offset + length]); offset += length
        elif 1 <= payload[0] & 31 <= 23:
            self.nals.append(payload)
        else:
            self.invalid = True
        if marker_pt & 0x80:
            if self.invalid or self.fragment is not None or not any(n[0] & 31 in (1, 5) for n in self.nals):
                return None
            result = (ssrc, timestamp, self.nals)
            self.nals = []
            return result
        return None


class MappingServer:
    """Bounded latest timestamp lookup; persisted JSONL is the full audit trail."""
    def __init__(self, port=8555, capacity=10000):
        self.records = OrderedDict()
        self.capacity = capacity
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                query = parse_qs(urlparse(self.path).query)
                try:
                    timestamp = int(query['timestamp'][0])
                    with owner.lock:
                        if 'vcl_sha256' in query:
                            candidates = [r for (ssrc, ts), r in owner.records.items()
                                          if ts == timestamp and r['vcl_sha256'] == query['vcl_sha256'][0]
                                          and r['session_id'] == query.get('session_id', [''])[0]]
                            record = candidates[0] if len(candidates) == 1 else None
                        else:
                            record = owner.records.get((int(query['ssrc'][0]), timestamp))
                    body = json.dumps(record or {'error': 'mapping_not_available'}).encode()
                    self.send_response(200 if record else 404)
                except (KeyError, ValueError):
                    body = b'{"error":"ssrc and timestamp required"}'
                    self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers(); self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start(); return self

    def __exit__(self, *args):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def add(self, record):
        with self.lock:
            key = (record['rtp_ssrc'], record['rtp_timestamp'])
            self.records[key] = record
            self.records.move_to_end(key)
            while len(self.records) > self.capacity:
                self.records.popitem(last=False)


def mediamtx_config(port=8554, path='windfarm/camera1'):
    if not path or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/_-' for c in path):
        raise ValueError('Invalid stream path')
    return (f'logLevel: info\nrtspAddress: 127.0.0.1:{int(port)}\nrtspTransports: [tcp]\n'
            # A 4K IDR can exceed the default 512-packet queue in one burst.
            # Keep bounded headroom while the receiver fetches its frame mapping.
            'writeQueueSize: 8192\n'
            'rtmp: no\nhls: no\nwebrtc: no\nsrt: no\nmoq: no\napi: no\nmetrics: no\npprof: no\n'
            f'paths:\n  {path}:\n    source: publisher\n')


def publish(package_dir, rtsp_url='rtsp://127.0.0.1:8554/windfarm/camera1', *, fps=None,
            cycles=None, mapping_port=8555, ffprobe='ffprobe', stop_event=None, initial_timestamp=None):
    """Publish normal-speed loops; each call creates a fresh UUID and random SSRC.

    ``cycles=None`` runs until stopped. The monotonic clock only paces actual
    packet transmission; all mappings use the timestamp in emitted RTP headers.
    """
    _local_url(rtsp_url, 'rtsp')
    root = Path(package_dir)
    manifest = json.loads((root / 'manifest.json').read_text())
    for filename in ('video.mp4', 'frames.csv'):
        expected = manifest.get('files', {}).get(filename)
        if not expected or sha256(root / filename) != expected:
            raise ValueError(f'Package checksum missing or mismatch: {filename}')
    if fps is None:
        fps = int(manifest['time_contract']['fps'])
    validate_video(root / 'video.mp4', root / 'frames.csv', fps, ffprobe=ffprobe)
    rows = read_frames(root / 'frames.csv')
    parameters, length_size, samples = h264_samples(root / 'video.mp4', ffprobe=ffprobe)
    if [int(p['pts']) for p in samples] != list(range(len(rows))) or any(p['pts'] != p['dts'] for p in samples):
        raise ValueError('Encoded packet order differs from validated presentation order')
    if 90000 % fps:
        raise ValueError('FPS must divide RTP clock rate')
    session_id, ssrc, sequence = str(uuid.uuid4()), secrets.randbits(32), secrets.randbits(16)
    origin = secrets.randbits(32) if initial_timestamp is None else int(initial_timestamp) & 0xffffffff
    dataset = manifest.get('dataset_id')
    if not dataset:
        raise ValueError('manifest.dataset_id required')
    sps = next(n for n in parameters if n[0] & 31 == 7)
    pps = next(n for n in parameters if n[0] & 31 == 8)
    sdp = (f'v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=WFRL camera {session_id}\r\nc=IN IP4 127.0.0.1\r\nt=0 0\r\n'
           'm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n'
           f'a=fmtp:96 packetization-mode=1;profile-level-id={sps[1:4].hex()};sprop-parameter-sets={base64.b64encode(sps).decode()},{base64.b64encode(pps).decode()}\r\n'
           'a=control:trackID=0\r\n').encode()
    stream_dir = root / 'stream'; stream_dir.mkdir(exist_ok=True)
    log_path = stream_dir / f'{session_id}.jsonl'
    connection = RTSPConnection(rtsp_url)
    try:
        connection.request('ANNOUNCE', headers={'Content-Type': 'application/sdp'}, body=sdp)
        connection.request('SETUP', rtsp_url.rstrip('/') + '/trackID=0', {'Transport': 'RTP/AVP/TCP;unicast;interleaved=0-1;mode=record'})
        connection.request('RECORD')
        with MappingServer(mapping_port) as mappings, log_path.open('x') as log, (root / 'video.mp4').open('rb') as video:
            started = time.monotonic()
            last_keepalive = started
            packet_count = octet_count = 0
            cycle = 0
            while cycles is None or cycle < cycles:
                for frame, packet in enumerate(samples):
                    if stop_event and stop_event.is_set():
                        return log_path
                    index = cycle * len(rows) + frame
                    delay = started + index / fps - time.monotonic()
                    if delay > 0:
                        if stop_event:
                            if stop_event.wait(delay):
                                return log_path
                        else:
                            time.sleep(delay)
                    if time.monotonic() - last_keepalive > 20:
                        connection.request('OPTIONS')
                        last_keepalive = time.monotonic()
                    media_timestamp = index * (90000 // fps)
                    extended = origin + media_timestamp
                    nals = read_sample(video, packet, length_size)
                    if 'K' in packet['flags']:
                        nals = parameters + nals
                    packets = list(packetize(nals, extended, ssrc, sequence))
                    for rtp in packets:
                        connection.send_packet(rtp)
                        packet_count += 1
                        octet_count += len(rtp) - 12
                    sequence = (sequence + len(packets)) & 0xffff
                    sent_timestamp, sent_ssrc = struct.unpack('!II', packets[-1][4:12])
                    sent_at = time.monotonic()
                    record = {'session_id': session_id, 'cycle_id': cycle, 'frame_id': frame,
                              'dataset_id': dataset, 'sim_time_s': float(rows[frame]['sim_time_s']),
                              'media_timestamp': media_timestamp, 'time_base': '1/90000',
                              'rtp_ssrc': sent_ssrc, 'rtp_timestamp': sent_timestamp,
                              'rtp_timestamp_extended': extended, 'rtp_wrap_count': extended >> 32,
                              'vcl_sha256': vcl_hash(nals), 'cycle_start': frame == 0,
                              'rtp_packet_count': len(packets), 'frame_data': rows[frame],
                              'sent_monotonic_s': sent_at,
                              'actual_send_elapsed_s': sent_at - started,
                              'pacing_lag_s': sent_at - (started + index / fps),
                              'schedule_lateness_s': sent_at - (started + index / fps)}
                    log.write(json.dumps(record) + '\n'); log.flush(); mappings.add(record)
                    if frame % fps == 0:
                        # RFC 3550 sender report correlates the actual RTP media clock.
                        connection.send_packet(sender_report(ssrc, sent_timestamp, packet_count, octet_count), channel=1)
                cycle += 1
            time.sleep(1 / fps)
        return log_path
    finally:
        connection.close()


def receive(rtsp_url, mapping_url, *, frame_count=100, output_dir=None, timeout=15):
    """Return exact matched rows and optionally a decodable Annex-B recording.

    Joining waits for an IDR. Mapping lookup uses observed timestamp plus complete VCL bytes; MediaMTX
    can rewrite SSRC, so both published and received SSRC are recorded.
    Decoder output order equals access-unit order only because B frames are
    forbidden by package validation. The optional recording can be decoded with
    FFmpeg; each complete access unit has one JSONL row alongside it.
    """
    _local_url(mapping_url, 'http')
    connection = RTSPConnection(rtsp_url, timeout=timeout)
    matched = []
    recording = None
    try:
        headers, sdp = connection.request('DESCRIBE', headers={'Accept': 'application/sdp'})
        advertised = [line.removeprefix('s=WFRL camera ').strip() for line in sdp.decode().splitlines()
                      if line.startswith('s=WFRL camera ')]
        if len(advertised) != 1:
            raise ValueError('Stream SDP does not identify the publisher session')
        advertised_session = str(uuid.UUID(advertised[0]))
        controls = [line[10:].strip() for line in sdp.decode().splitlines() if line.startswith('a=control:') and line[10:].strip() != '*']
        control = controls[-1]
        track = control if control.startswith('rtsp://') else headers.get('content-base', rtsp_url.rstrip('/') + '/') + control
        connection.request('SETUP', track, {'Transport': 'RTP/AVP/TCP;unicast;interleaved=0-1'})
        connection.request('PLAY')
        if output_dir:
            root = Path(output_dir); root.mkdir(parents=True, exist_ok=True)
            recording = (root / 'received.h264').open('xb')
        assembler = AccessUnits()
        ready = False
        session = None
        discontinuities = 0
        last_keepalive = time.monotonic()
        deadline = time.monotonic() + timeout
        while len(matched) < frame_count:
            if time.monotonic() > deadline:
                raise TimeoutError('Timed out waiting for complete mapped frames')
            if time.monotonic() - last_keepalive > 20:
                connection.keepalive()
                last_keepalive = time.monotonic()
            unit = assembler.feed(connection.receive_packet())
            if assembler.discontinuities != discontinuities:
                ready = False
                discontinuities = assembler.discontinuities
            if unit is None:
                continue
            ssrc, timestamp, nals = unit
            idr = any(n[0] & 31 == 5 for n in nals)
            if not ready and not idr:
                continue
            record = None
            for _ in range(20):
                try:
                    with urlopen(f'{mapping_url}?timestamp={timestamp}&vcl_sha256={vcl_hash(nals)}&session_id={advertised_session}', timeout=2) as response:
                        record = json.load(response)
                    break
                except OSError:
                    time.sleep(0.01)
            if not record or record['session_id'] != advertised_session or record['rtp_timestamp'] != timestamp or record['vcl_sha256'] != vcl_hash(nals):
                raise ValueError('Received media cannot be matched to publisher mapping')
            if session is not None and record['session_id'] != session and not idr:
                raise ValueError('New session must begin at independently decodable IDR')
            if record['cycle_start'] and not idr:
                raise ValueError('Cycle boundary is not independently decodable')
            session, ready = record['session_id'], True
            record = dict(record, received_rtp_ssrc=ssrc, received_rtp_timestamp=timestamp,
                          mapping_method='observed_rtp_timestamp_and_complete_vcl_sha256')
            matched.append(record)
            if recording:
                for nal in nals:
                    recording.write(b'\x00\x00\x00\x01' + nal)
            deadline = time.monotonic() + timeout
        if output_dir:
            (root / 'received.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in matched))
        return matched
    finally:
        if recording:
            recording.close()
        connection.close()
