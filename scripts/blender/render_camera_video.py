"""Background Blender CLI for fixed nacelle camera masters.

Example:
  Blender --background --factory-startup --python-exit-code 1 --python \
    scripts/blender/render_camera_video.py -- --output /tmp/camera --end-s 117.1
"""
from pathlib import Path
import argparse
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
from wfrl_blender import farm_flex
from wfrl_blender.camera_video import render_package


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--package', '--source', default=str(farm_flex.default_package()))
    p.add_argument('--output', required=True)
    p.add_argument('--scene', help='Saved .blend containing the selected fixed nacelle camera; read in isolated process')
    p.add_argument('--camera', help='Camera object name; default target turbine NacelleGimbal')
    p.add_argument('--turbine', default='T1')
    p.add_argument('--fps', type=int, choices=(15, 20, 25), default=20)
    p.add_argument('--start-s', type=float)
    p.add_argument('--end-s', type=float)
    p.add_argument('--exposure-s', type=float, default=.001, help='Default fixed 1 ms global shutter; long exposures require convergence checks')
    p.add_argument('--width', type=int, default=3840)
    p.add_argument('--height', type=int, default=2160)
    p.add_argument('--device', choices=('cpu', 'metal'), default='cpu')
    p.add_argument('--samples', type=int, default=32)
    p.add_argument('--engine', choices=('cycles', 'eevee'), default='cycles')
    p.add_argument('--scene-scope', choices=('single', 'farm'), default='single')
    p.add_argument('--exr-codec', choices=('ZIP', 'NONE'), default='ZIP')
    p.add_argument('--exposure-samples', type=int, default=8)
    p.add_argument('--focus-distance', type=float)
    p.add_argument('--aperture', type=float)
    p.add_argument('--max-frames', type=int, help='Render initial frame subset; later invocation resumes same configuration')
    p.add_argument('--test-resolution', action='store_true', help='Permit small diagnostic images, explicitly marked non-delivery')
    return p


if __name__ == '__main__':
    render_package(parser().parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []))
