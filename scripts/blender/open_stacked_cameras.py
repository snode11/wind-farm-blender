"""Fresh, three-camera housing demo; optional -- --output DIR --capture.

Use --factory-startup. Existing simulation data are reused, never rerun.
A saved .blend is a static reference snapshot; this launcher restores playback.
"""
from pathlib import Path
import argparse
import json
import sys

import bpy
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import stacked_camera_rig as rig, custom_cameras as core

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path)
parser.add_argument('--capture', action='store_true')
parser.add_argument('--center', type=float, nargs=3, metavar=('X', 'Y', 'Z'), default=rig.RigParameters().center)
parser.add_argument('--spacing', type=float, default=.080, help='Optical-centre spacing in metres')
parser.add_argument('--box-size', type=float, nargs=3, default=(.180, .290, .100), metavar=('WIDTH', 'HEIGHT', 'DEPTH'))
parser.add_argument('--box-yaw', type=float, default=-45., help='Housing rotation about nacelle Z, degrees')
parser.add_argument('--long-edge', type=int, default=1920)
args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
addon.register()
addon.load_demo_scene(camera_rig=False)
scene = bpy.context.scene
scene.frame_set(1)
config = rig.RigParameters(center=tuple(args.center), spacing_m=args.spacing,
    width_m=args.box_size[0], height_m=args.box_size[1], depth_m=args.box_size[2],
    housing_yaw_deg=args.box_yaw, output_long_edge_px=args.long_edge)
cameras, assembly = rig.build(scene, config)
scene.camera = cameras[1]
scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
scene.render.resolution_percentage = 100
for area in bpy.context.screen.areas if bpy.context.screen else []:
    if area.type == 'VIEW_3D':
        area.spaces.active.region_3d.view_perspective = 'CAMERA'
        area.spaces.active.show_region_ui = True
        for region in area.regions:
            if region.type == 'UI':
                try: region.active_panel_category = 'View'
                except AttributeError: pass
if args.output:
    args.output.mkdir(parents=True, exist_ok=True)
    layout_path = args.output/'three-stacked-cameras.json'
    if layout_path.exists() or (args.output/'three-stacked-cameras.blend').exists():
        raise FileExistsError('使用新的输出目录，避免覆盖已有布局或场景')
    layout_path.write_text(json.dumps(core.layout_dict(scene), ensure_ascii=False, indent=2))
    (args.output/'housing-assumptions.json').write_text(assembly['assumptions_json'])
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output/'three-stacked-cameras.blend'))
if args.capture:
    if bpy.app.background or args.output is None:
        raise ValueError('--capture 需要可见 Blender 窗口及 --output')
    def capture():
        from wfrl_blender import custom_camera_capture
        area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(area=area, region=region):
            job = custom_camera_capture.Capture(bpy.context, args.output)
            try:
                while not job.step(bpy.context):
                    pass
            except Exception as exc:
                job.finish(error=str(exc))
                raise
        print('STACKED_CAPTURE_DONE', flush=True)
    bpy.app.timers.register(capture, first_interval=3.)
print('THREE_STACKED_CAMERAS_READY', flush=True)
