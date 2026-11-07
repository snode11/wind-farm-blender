"""Saved FarmFlex timing, output settings and identity in an isolated process."""
import hashlib
import json
import os
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import clearance_replay as replay, farm_flex as farm

assert bpy.app.background
assert Path(addon.__file__).resolve().parent == ROOT / 'blender_frontend' / 'wfrl_blender'
output = Path(os.environ['WFRL_TEST_OUTPUT'])
output.mkdir(parents=True, exist_ok=False)
variant = os.environ.get('WFRL_REOPEN_VARIANT', 'farm')
packages = dict(farm=farm.default_package, dual=farm.default_dual_package,
                tls=farm.default_dual_tls_package)
addon.register()
addon.load_demo_scene(packages[variant](), camera_rig=False)


def state(scene):
    return dict(fps=scene.render.fps, fps_base=scene.render.fps_base,
                frame_start=scene.frame_start, frame_end=scene.frame_end,
                frame_step=scene.frame_step, sync_mode=scene.sync_mode,
                use_preview_range=scene.use_preview_range,
                frame_preview_start=scene.frame_preview_start,
                frame_preview_end=scene.frame_preview_end,
                frame_current=scene.frame_current, frame_subframe=scene.frame_subframe,
                timebase=scene.get('wfrl_clearance_timebase_fps'),
                has_timebase='wfrl_clearance_timebase_fps' in scene,
                turbine=scene['wfrl_clearance_turbine'],
                sample=replay.sample(scene))


scene = bpy.context.scene
assert (scene.render.fps, scene.render.fps_base, scene.frame_start, scene.frame_end) == (60, 1., 1, 3601)
assert scene['wfrl_clearance_timebase_fps'] == 60.
assert scene.sync_mode == 'FRAME_DROP' and not scene.use_preview_range
selected = 'T2' if variant == 'farm' else 'T3'
assert bpy.ops.wfrl.farm_flex_view(turbine=selected) == {'FINISHED'}
scene.render.fps, scene.render.fps_base = 24, 1.001
scene.frame_start, scene.frame_end, scene.frame_step = 11, 101, 2
scene.sync_mode, scene.use_preview_range = 'NONE', True
scene.frame_preview_start, scene.frame_preview_end = 21, 81
scene['wfrl_clearance_timebase_fps'] = 90.
if os.environ.get('WFRL_REOPEN_TIMEBASE') == 'render':
    del scene['wfrl_clearance_timebase_fps']
scene.frame_set(40, subframe=.25)
before = state(scene)
assert abs(before['sample']['time_s'] - (118.21996875 if not before['has_timebase'] else 117.325)) < 1e-9
assert replay.reader_for(scene) is farm.active_for(scene).readers[selected]
saved = output / 'saved.blend'
assert bpy.ops.wm.save_as_mainfile(filepath=str(saved)) == {'FINISHED'}
assert bpy.ops.wm.open_mainfile(filepath=str(saved)) == {'FINISHED'}
scene = bpy.context.scene
after = state(scene)
assert farm.is_active(scene), scene.get('wfrl_clearance_status')
assert after == before, (before, after)
assert replay.reader_for(scene) is farm.active_for(scene).readers[selected]
assert scene['wfrl_clearance_time_s'] == after['sample']['time_s']

scene['wfrl_farm_manifest_sha256'] = '0' * 64
invalid = output / 'identity-mismatch.blend'
assert bpy.ops.wm.save_as_mainfile(filepath=str(invalid)) == {'FINISHED'}
assert bpy.ops.wm.open_mainfile(filepath=str(invalid)) == {'FINISHED'}
scene = bpy.context.scene
assert not farm.is_active(scene) and replay.reader_for(scene) is None
assert not scene['wfrl_flex_active'] and '结果包不匹配' in scene['wfrl_clearance_status']
result = dict(status='PASS', variant=variant, blender=bpy.app.version_string,
              scope='Source; isolated background process; native save and reopen operators',
              addon_file=addon.__file__, before=before, after=after,
              mismatched_identity_status=scene['wfrl_clearance_status'],
              sha256={name: hashlib.sha256((Path(addon.__file__).parent / name).read_bytes()).hexdigest()
                      for name in ('farm_flex.py', 'clearance_replay.py')})
addon.unregister()
(output / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print('FARM_REOPEN_SETTINGS_PASS ' + json.dumps(result), flush=True)
