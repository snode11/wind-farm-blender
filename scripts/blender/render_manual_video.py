"""Render a manual T1 Down video job using workspace source in background Blender."""
import argparse
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]


def main():
    if not bpy.app.background:
        raise RuntimeError('请使用独立 Blender --background --factory-startup 运行视频作业')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', required=True, type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    import wfrl_blender
    from wfrl_blender.manual_video_render import render_job
    if Path(wfrl_blender.__file__).resolve().parent != ROOT / 'blender_frontend/wfrl_blender':
        raise RuntimeError('实际加载模块不是当前工作区源码')
    render_job(args.job)


if __name__ == '__main__':
    main()
