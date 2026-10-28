"""Real camera/constraint lifecycle with a minimal background viewport adapter.

Source mode requires factory startup. Installed mode requires the exact package
root. This checks Blender data and evaluated transforms, not window pixels/FPS.
"""
from dataclasses import replace
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import bpy

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
native = importlib.import_module(MODULE + '.native_camera_views')
core = importlib.import_module(MODULE + '.custom_cameras')
expected = Path(os.environ.get('WFRL_EXPECTED_ADDON_ROOT', ROOT / 'blender_frontend/wfrl_blender'))
assert Path(addon.__file__).resolve().parent == expected.resolve()
OUT = Path(os.environ['WFRL_TEST_OUTPUT']).resolve()
OUT.mkdir(parents=True, exist_ok=False)
scene = bpy.context.scene
camera = bpy.data.objects.new('ProxySource', bpy.data.cameras.new('ProxySource.Data'))
scene.collection.objects.link(camera)
camera['wfrl_custom_camera'] = True
camera['wfrl_custom_slot'] = 1
params = core.CameraParameters(location=(0., 0., 0.))
core.apply_parameters(camera, params)
view = NS(view_perspective='PERSP', view_camera_zoom=23., view_camera_offset=(.12, -.17))
space = NS(type='VIEW_3D', use_local_camera=False, camera=None, region_3d=view)
area = NS(spaces=NS(active=space), regions=[NS(type='WINDOW', width=960, height=540)], tag_redraw=lambda: None)
session = native.NativeSession.__new__(native.NativeSession)
session.scene, session.window = scene, NS(screen=NS(areas=[area]))
session.slots, session.proxy_owner, session.save_state, session.diagram_area = [1], 'proxy-regression', None, None
entry = dict(area=area, slot=1, proxy=None, key=None, view_key=('manual-view',))
session.entries = [entry]
session.sync()
proxy = entry['proxy']
identity = (proxy.as_pointer(), proxy.data.as_pointer())
counts = (len(bpy.data.objects), len(bpy.data.cameras))
for index in range(60):
    core.apply_parameters(camera, replace(params, location=(index * .01, .1, -.2),
        yaw=float(index), pitch=10., roll=20., output_long_edge_px=2048,
        focal_length_mm_record=50.))
    session.sync()
    bpy.context.view_layer.update()
    assert (entry['proxy'].as_pointer(), entry['proxy'].data.as_pointer()) == identity
    assert counts == (len(bpy.data.objects), len(bpy.data.cameras))
    assert max(abs(proxy.matrix_world[i][j] - camera.matrix_world[i][j])
               for i in range(4) for j in range(4)) < 2e-6
    assert view.view_perspective == 'PERSP'
    assert view.view_camera_zoom == 23. and view.view_camera_offset == (.12, -.17)
    assert not proxy.data.show_passepartout and proxy.data.passepartout_alpha == 0
checks = ['60 pose/metadata changes retain one proxy/data pair and manual view',
          'evaluated COPY_TRANSFORMS follows source pose', 'proxy masks remain disabled']
for field, value in (('fov', 90.), ('vfov', 40.), ('clip_near_m', .007), ('clip_far_m', 15000.)):
    previous = entry['proxy'].session_uid
    params = replace(core.parameters(camera), **{field: value})
    core.apply_parameters(camera, params)
    session.sync()
    proxy = entry['proxy']
    assert proxy.session_uid != previous
    assert counts == (len(bpy.data.objects), len(bpy.data.cameras))
    aspect = scene.render.resolution_x * scene.render.pixel_aspect_x / (scene.render.resolution_y * scene.render.pixel_aspect_y)
    outer = proxy.data.sensor_width / (2 * proxy.data.lens)
    assert abs(math.degrees(2 * math.atan(outer * entry['fx'])) - params.fov) < 1e-4
    assert abs(math.degrees(2 * math.atan(outer / aspect * entry['fy'])) - params.vfov) < 1e-4
    assert abs(proxy.data.clip_start - params.clip_near_m) < 1e-6
    assert abs(proxy.data.clip_end - params.clip_far_m) < 1e-3
checks.append('FOV and clip changes rebuild one calibrated proxy without resource growth')
previous = entry['proxy'].session_uid
scene.render.pixel_aspect_x = 1.2
session.sync()
assert entry['proxy'].session_uid != previous
checks.append('pixel-aspect changes invalidate optical proxy')
previous = entry['proxy'].session_uid
camera['wfrl_custom_camera'] = False
replacement = camera.copy()
replacement.data = camera.data
replacement['wfrl_custom_camera'] = True
scene.collection.objects.link(replacement)
session.sync()
assert entry['proxy'].session_uid != previous
assert entry['proxy'].constraints[0].target == replacement
assert camera.data == replacement.data
checks.append('source replacement rebinds constraint while preserving shared camera data')
assert session.release_proxy(entry) == []
assert not any(obj.get(native._PROXY_OWNER) == session.proxy_owner for obj in bpy.data.objects)
assert not any(data.get(native._PROXY_OWNER) == session.proxy_owner for data in bpy.data.cameras)
checks.append('proxy release removes owned object and data')
result = dict(status='PASS', module=MODULE, addon_file=addon.__file__, blender=bpy.app.version_string,
    background=bpy.app.background, pose_updates=60, proxy_replacements_during_pose_updates=0,
    source_sha256=hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(), checks=checks,
    scope='Real Blender camera data and evaluated constraints; viewport adapter, no window/GPU/FPS acceptance')
(OUT / 'checks.json').write_text(json.dumps(result, indent=2) + '\n')
print('NATIVE_CAMERA_PROXY_PASS', flush=True)
