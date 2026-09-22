"""Build, verify and serve immutable synthetic nacelle camera video packages."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    build = sub.add_parser('build', help='Render, label, encode, and seal a separate output package')
    build.add_argument('--package', type=Path, default=ROOT / 'blender_frontend/wfrl_blender/assets/mappo')
    build.add_argument('--output', type=Path, required=True)
    build.add_argument('--turbine', default='T1', choices=['T1', 'T2', 'T3'])
    build.add_argument('--fps', type=int, default=20, choices=[15, 20, 25])
    build.add_argument('--resolution', choices=['1440p', '4k'], default='4k')
    build.add_argument('--start-s', type=float)
    build.add_argument('--end-s', type=float)
    build.add_argument('--exposure-s', type=float, default=.001)
    build.add_argument('--samples', type=int, default=32)
    build.add_argument('--engine', choices=['cycles', 'eevee'], default='cycles')
    build.add_argument('--scene-scope', choices=['single', 'farm'], default='single')
    build.add_argument('--exr-codec', choices=['ZIP', 'NONE'], default='ZIP')
    build.add_argument('--exposure-samples', type=int, default=8)
    build.add_argument('--device', choices=['cpu', 'metal'], default='cpu')
    build.add_argument('--blender', default=shutil.which('blender') or '/Applications/Blender.app/Contents/MacOS/Blender')
    build.add_argument('--scene', type=Path)
    build.add_argument('--camera')
    build.add_argument('--crf', type=int, default=18)
    build.add_argument('--preset', default='medium')
    build.add_argument('--ffmpeg', default='ffmpeg')
    build.add_argument('--ffprobe', default='ffprobe')
    for command in ('verify', 'seal'):
        check = sub.add_parser(command)
        check.add_argument('directory', type=Path)
        check.add_argument('--ffprobe', default='ffprobe')
    loop = sub.add_parser('loop', help='Repeat a verified base video with complete per-frame source-cycle labels')
    loop.add_argument('source', type=Path)
    loop.add_argument('--output', type=Path, required=True)
    loop.add_argument('--repeats', type=int, default=5)
    loop.add_argument('--ffmpeg', default='ffmpeg')
    loop.add_argument('--ffprobe', default='ffprobe')
    status = sub.add_parser('status', help='Read recorded progress without claiming integrity verification')
    status.add_argument('directory', type=Path)
    config = sub.add_parser('stream-config', help='Write isolated loopback MediaMTX configuration')
    config.add_argument('output', type=Path)
    config.add_argument('--port', type=int, default=8554)
    config.add_argument('--path', default='windfarm/camera1')
    publish = sub.add_parser('publish', help='Publish existing H.264 plus observed RTP mappings')
    publish.add_argument('directory', type=Path)
    publish.add_argument('--url', default='rtsp://127.0.0.1:8554/windfarm/camera1')
    publish.add_argument('--mapping-port', type=int, default=8555)
    publish.add_argument('--cycles', type=int)
    publish.add_argument('--ffprobe', default='ffprobe')
    receive = sub.add_parser('receive', help='Receive and match complete access units using actual RTP headers')
    receive.add_argument('--url', default='rtsp://127.0.0.1:8554/windfarm/camera1')
    receive.add_argument('--mapping-url', default='http://127.0.0.1:8555/')
    receive.add_argument('--frames', type=int, default=100)
    receive.add_argument('--timeout', type=float, default=30)
    receive.add_argument('--output', type=Path, required=True)
    return p


def build(args):
    from wfrl.camera_video.data import SourceGeometry, export_frame_data
    from wfrl.camera_video.media import encode_video, validate_video
    from wfrl.camera_video.package import read_json, seal_package
    source = SourceGeometry(args.package, turbine_id=args.turbine)
    # Source validation happens before expensive rendering. The renderer enforces
    # the immutable configuration and verifies completed PNG hashes on resume.
    width, height = (3840, 2160) if args.resolution == '4k' else (2560, 1440)
    command = [args.blender, '--background', '--factory-startup', '--python-exit-code', '1',
               '--python', str(ROOT / 'scripts/blender/render_camera_video.py'), '--',
               '--package', str(args.package.resolve()), '--output', str(args.output.resolve()),
               '--turbine', args.turbine, '--fps', str(args.fps), '--width', str(width), '--height', str(height),
               '--samples', str(args.samples), '--exposure-samples', str(args.exposure_samples),
               '--device', args.device, '--engine', args.engine,
               '--scene-scope', args.scene_scope, '--exr-codec', args.exr_codec]
    for key in ('start_s', 'end_s', 'exposure_s', 'scene', 'camera'):
        value = getattr(args, key)
        if value is not None:
            command.extend(['--' + key.replace('_', '-'), str(value)])
    subprocess.run(command, cwd=ROOT, check=True)
    camera = read_json(args.output / 'camera.json')
    if not (args.output / 'data_manifest.json').exists():
        export_frame_data(source, args.output, fps=args.fps,
                          exposure_s=args.exposure_s,
                          start_s=args.start_s, end_s=args.end_s,
                          camera_mount=camera['camera_mount'], camera_config=camera)
    if (args.output / 'video.mp4').exists() and (args.output / 'checks' / 'encoding.json').exists():
        encoding = read_json(args.output / 'checks' / 'encoding.json')
        recorded = encoding['command']
        if str(args.crf) != recorded[recorded.index('-crf') + 1] or args.preset != recorded[recorded.index('-preset') + 1]:
            raise ValueError('Encoding parameters changed; use a new output directory')
        validate_video(args.output / 'video.mp4', args.output / 'frames.csv', args.fps, ffprobe=args.ffprobe)
    else:
        encode_video(args.output, args.fps, pattern='%06d.png', ffmpeg=args.ffmpeg,
                     ffprobe=args.ffprobe, crf=args.crf, preset=args.preset)
    manifest = seal_package(args.output, ffprobe=args.ffprobe)
    return {'directory': str(args.output.resolve()), 'dataset_id': manifest['dataset_id'],
            'frame_count': manifest['frame_count'], 'status': manifest['status']}


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == 'build':
        result = build(args)
    elif args.command == 'loop':
        from wfrl.camera_video.loop import build_loop
        result = build_loop(args.source, args.output, repeats=args.repeats,
                            ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
    elif args.command == 'status':
        from wfrl.camera_video.package import read_json
        root = args.directory
        if (root / 'manifest.json').is_file():
            manifest = read_json(root / 'manifest.json')
            if manifest.get('schema') == 'wfrl.camera-video-loop.v1':
                return print(json.dumps({'frame_count': manifest['frame_count'],
                    'fps': manifest['fps'], 'duration_s': manifest['time_contract']['duration_s'],
                    'source_repeat_count': manifest['source_repeat_count'],
                    'status': manifest['status'], 'integrity_checked': False}, indent=2))
        render = read_json(root / 'render_manifest.json')
        records = read_json(root / 'checks' / 'render_frames.json') if (root / 'checks' / 'render_frames.json').exists() else {}
        seconds = [r['seconds'] for r in records.values() if 'seconds' in r]
        result = {'expected_frames': render['frame_count'], 'recorded_frames': len(records),
                  'fps': render['fps'], 'resolution': [render['width'], render['height']],
                  'mean_render_seconds_per_frame': sum(seconds) / len(seconds) if seconds else None,
                  'estimated_remaining_render_seconds': (render['frame_count'] - len(records)) * sum(seconds) / len(seconds) if seconds else None,
                  'files_present': {n: (root / n).is_file() for n in ('frames.csv', 'frame_geometry.npz', 'video.mp4', 'manifest.json')},
                  'integrity_checked': False}
    elif args.command == 'stream-config':
        from wfrl.camera_video.stream import mediamtx_config
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as f:
            f.write(mediamtx_config(args.port, args.path))
        result = {'configuration': str(args.output.resolve())}
    elif args.command == 'publish':
        from wfrl.camera_video.stream import publish
        log = publish(args.directory, args.url, cycles=args.cycles,
                      mapping_port=args.mapping_port, ffprobe=args.ffprobe)
        result = {'session_log': str(log)}
    elif args.command == 'receive':
        from wfrl.camera_video.stream import receive
        matched = receive(args.url, args.mapping_url, frame_count=args.frames,
                          output_dir=args.output, timeout=args.timeout)
        result = {'matched_frames': len(matched), 'output': str(args.output.resolve())}
    else:
        from wfrl.camera_video.package import seal_package, verify_package
        result = (seal_package if args.command == 'seal' else verify_package)(args.directory, ffprobe=args.ffprobe)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
