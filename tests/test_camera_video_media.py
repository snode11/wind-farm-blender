"""Actual FFmpeg integration: decoded frame timestamps are the contract."""
import csv
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from wfrl.camera_video.media import encode_video, h264_samples, read_sample, sha256, validate_video


@pytest.fixture
def encoded_package(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg/ffprobe required')
    master = tmp_path / 'master_frames'; master.mkdir()
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=128x72:rate=20',
                    '-frames:v', '40', '-start_number', '0', str(master / '%06d.png')], check=True)
    with (tmp_path / 'frames.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['frame_id', 'video_pts', 'sim_time_s'])
        writer.writeheader()
        writer.writerows({'frame_id': i, 'video_pts': i, 'sim_time_s': 117 + (i + .5) / 20} for i in range(40))
    encode_video(tmp_path, 20, preset='ultrafast')
    (tmp_path / 'manifest.json').write_text(json.dumps({'dataset_id': 'synthetic-test-only',
        'time_contract': {'fps': 20}, 'files': {n: sha256(tmp_path / n) for n in ('video.mp4', 'frames.csv')}}))
    return tmp_path


def test_decoded_cfr_pts_and_access_units(encoded_package):
    root = encoded_package
    report = validate_video(root / 'video.mp4', root / 'frames.csv', 20)
    assert report['pts'] == list(range(40))
    assert report['keyframes'] == [0, 20]
    assert len(list((root / 'master_frames').glob('*.png'))) == 40
    parameters, length_size, samples = h264_samples(root / 'video.mp4')
    assert {n[0] & 31 for n in parameters} == {7, 8}
    assert len(samples) == 40
    with (root / 'video.mp4').open('rb') as handle:
        assert any(n[0] & 31 == 5 for n in read_sample(handle, samples[0], length_size))


def test_pts_mismatch_is_rejected(encoded_package):
    path = encoded_package / 'frames.csv'
    text = path.read_text().replace('1,1,', '1,2,')
    path.write_text(text)
    with pytest.raises(ValueError, match='contiguous'):
        validate_video(encoded_package / 'video.mp4', path, 20)


def test_existing_video_never_overwritten(encoded_package):
    with pytest.raises(FileExistsError):
        encode_video(encoded_package, 20)


def test_resume_committed_video_before_report(encoded_package, monkeypatch):
    from wfrl.camera_video import media
    root = encoded_package
    (root / 'video.mp4').unlink()
    real_write = media._write_json_atomic
    def interrupt_report(path, value):
        if Path(path).name == 'encoding.json':
            raise OSError('simulated interruption after MP4 commit')
        real_write(path, value)
    monkeypatch.setattr(media, '_write_json_atomic', interrupt_report)
    with pytest.raises(OSError, match='simulated'):
        encode_video(root, 20, preset='ultrafast')
    assert (root / 'video.mp4').exists()
    monkeypatch.setattr(media, '_write_json_atomic', real_write)
    assert encode_video(root, 20, preset='ultrafast')['valid']
    assert not (root / 'checks' / 'encoding.pending.json').exists()


def test_resume_owned_partial_video_and_reject_changed_parameters(encoded_package, monkeypatch):
    from wfrl.camera_video import media
    root = encoded_package
    (root / 'video.mp4').unlink()
    real_run = media._run
    def interrupt_ffmpeg(args):
        if '-framerate' in args:
            (root / 'video.encoding.mp4').write_bytes(b'partial interrupted encoding')
            raise OSError('simulated encoder termination')
        return real_run(args)
    monkeypatch.setattr(media, '_run', interrupt_ffmpeg)
    with pytest.raises(OSError, match='simulated'):
        encode_video(root, 20, preset='ultrafast')
    with pytest.raises(ValueError, match='different'):
        encode_video(root, 20, preset='medium')
    monkeypatch.setattr(media, '_run', real_run)
    assert encode_video(root, 20, preset='ultrafast')['valid']


@pytest.mark.parametrize('fps', [15, 25])
def test_other_supported_frame_rates(encoded_package, tmp_path, fps):
    target = tmp_path / f'fps-{fps}'
    target.mkdir()
    shutil.copytree(encoded_package / 'master_frames', target / 'master_frames')
    with (target / 'frames.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['frame_id', 'video_pts', 'sim_time_s'])
        writer.writeheader()
        writer.writerows({'frame_id': i, 'video_pts': i, 'sim_time_s': 117 + (i + .5) / fps} for i in range(40))
    report = encode_video(target, fps, preset='ultrafast')
    assert report['time_base'] == f'1/{fps}'
    assert report['pts'] == list(range(40))
    assert report['keyframes'] == list(range(0, 40, fps))


def test_srgb_transfer_and_bt709_matrix_roundtrip(tmp_path):
    import struct
    import zlib
    colors = [(190, 70, 20), (10, 180, 40), (30, 70, 200), (128, 128, 128)]
    width, height = 128, 64
    pixels = bytes(component for y in range(height) for x in range(width) for component in colors[x // 32])
    def chunk(name, data):
        return struct.pack('!I', len(data)) + name + data + struct.pack('!I', zlib.crc32(name + data) & 0xffffffff)
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', width, height, 8, 2, 0, 0, 0))
    scanlines = b''.join(b'\0' + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
    png += chunk(b'IDAT', zlib.compress(scanlines)) + chunk(b'IEND', b'')
    (tmp_path / 'master_frames').mkdir()
    (tmp_path / 'master_frames' / '000000.png').write_bytes(png)
    (tmp_path / 'frames.csv').write_text('frame_id,video_pts,sim_time_s\n0,0,117.025\n')
    report = encode_video(tmp_path, 20, crf=12)
    assert report['color_contract'] == {'color_space': 'bt709', 'color_transfer': 'iec61966-2-1',
                                       'color_primaries': 'bt709', 'color_range': 'tv'}
    decoded = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(tmp_path / 'video.mp4'),
                                      '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])
    for i, reference in enumerate(colors):
        offset = ((height // 2) * width + i * 32 + 16) * 3
        assert max(abs(a - b) for a, b in zip(decoded[offset:offset + 3], reference)) <= 4
