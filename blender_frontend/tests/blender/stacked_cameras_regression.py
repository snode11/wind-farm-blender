# Custom narrow-zone RigParameters builder regression; bundled continuous defaults are tested separately.
"""Three-camera reference coverage, physical occlusion and rigid pose regression."""
from pathlib import Path
import json
from dataclasses import replace
import math
import os
import sys
import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [os.environ.get('WFRL_ADDON_ROOT', str(ROOT/'blender_frontend')), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import stacked_camera_rig as rig, custom_cameras as core
from wfrl_blender import camera_projection as projection, custom_camera_preview as preview
addon.register(); addon.load_demo_scene(camera_rig=False)
scene = bpy.context.scene; scene.frame_set(1)
config = rig.RigParameters()
cameras, assembly = rig.build(scene, config)
obj, points, spans, used = rig.blade_geometry(scene)
assert len(core.enabled_cameras(scene)) == 3
assert not any(o.get('wfrl_custom_slot') == 4 for o in scene.objects)
layout = core.layout_dict(scene); core.validate_layout(scene, layout)
root = scene.objects[core.ROOT_NAME]
root_matrix = np.asarray(root.matrix_world)
rows = []
inside_sets = []
with preview.without_annotations(scene, bpy.context.view_layer):
    deps = bpy.context.evaluated_depsgraph_get()
    for cam, interval in zip(cameras, config.span_ranges_m):
        p = core.parameters(cam)
        basis = np.asarray(projection.rotation(p.yaw, p.pitch, p.roll))
        view = (points - np.asarray(p.location)) @ basis
        uv = view[:, :2] / -view[:, 2, None]
        half = np.tan(np.radians([p.fov, p.vfov])/2)
        inside = (view[:, 2] < 0) & (np.abs(uv) <= half+1e-7).all(axis=1)
        target = used[(spans[used] >= interval[0]-1e-4) & (spans[used] <= interval[1]+1e-4)]
        # Regional close-ups deliberately crop the aiming patch in C1/C2.
        # C3 still contains the full selected tip patch.
        if config.framing_scale[cam['wfrl_custom_slot']-1] == 1:
            assert inside[target].all(), (cam.name, 'target outside frustum')
        assert inside[target].any(), (cam.name, 'aiming patch absent')
        inside_sets.append(inside)
        origin = Vector(cam.matrix_world.translation)
        # Test a 17x11 near-field ray grid against physical housing/modules.
        blocked = {}
        for u in np.linspace(-1, 1, 17):
            for v in np.linspace(-1, 1, 11):
                ray = root_matrix[:3, :3] @ basis @ np.array([u*half[0], v*half[1], -1.])
                hit, _, _, _, hitobj, _ = scene.ray_cast(deps, origin, Vector(ray).normalized(), distance=.25)
                if hit:
                    blocked[hitobj.name] = blocked.get(hitobj.name, 0)+1
        # Sample the outward-facing blade surface. Report honest self/hub occlusions.
        visible = 0; total = 0; occluders = {}
        for face in list(obj.data.polygons)[::7]:
            ids = list(face.vertices)
            center = points[ids].mean(axis=0)
            if not interval[0] <= spans[ids].mean() <= interval[1]: continue
            center_view = (center-np.asarray(p.location)) @ basis
            if center_view[2] >= 0 or (np.abs(center_view[:2]) > -center_view[2]*half).any(): continue
            normal = np.cross(points[ids[1]]-points[ids[0]], points[ids[2]]-points[ids[0]])
            if np.dot(normal, np.asarray(p.location)-center) <= 0: continue
            world = root_matrix[:3, :3] @ center + root_matrix[:3, 3]
            delta = world-np.asarray(origin); dist = np.linalg.norm(delta)
            hit, loc, _, _, hitobj, _ = scene.ray_cast(deps, origin, Vector(delta/dist), distance=dist+.1)
            good = hit and hitobj.original == obj and np.linalg.norm(np.asarray(loc)-world) < .15
            visible += int(good); total += 1
            if not good:
                name = hitobj.name if hit else 'none'
                occluders[name] = occluders.get(name, 0)+1
        rows.append({'camera': cam.name, 'target_span_m': interval, 'hfov': p.fov, 'vfov': p.vfov,
                     'near_field_blocked_rays': blocked, 'front_surface_samples': total,
                     'visible_front_surface_samples': visible, 'surface_occluders': occluders})
        print('CAMERA_CHECK', rows[-1], flush=True)
    union_covers_blade = bool(np.logical_or.reduce(inside_sets)[used].all())
    assert not inside_sets[0][used[spans[used]>=19.]].any()
    assert not inside_sets[1][used[spans[used]>=55.35]].any()
    overlaps = []
    for i in (0, 1):
        shared = used[inside_sets[i][used] & inside_sets[i+1][used]]
        overlaps.append([float(spans[shared].min()), float(spans[shared].max())] if len(shared) else None)
# Change nacelle pose; centres remain rigid and cameras never follow rotor.
local = [cam.matrix_basis.copy() for cam in cameras]
old = root.rotation_euler.copy(); root.rotation_euler.z += .4
bpy.context.view_layer.update()
for cam, matrix in zip(cameras, local):
    expected = root.matrix_world @ matrix
    assert max(abs(cam.matrix_world[i][j]-expected[i][j]) for i in range(4) for j in range(4)) < 1e-5
root.rotation_euler = old
scene.frame_set(61); bpy.context.view_layer.update()
for cam, matrix in zip(cameras, local):
    assert cam.matrix_basis == matrix, 'camera tracked moving blade'
assert all(not row['near_field_blocked_rays'] for row in rows), rows
# Replacing a camera through the ordinary UI commit must carry its physical body.
draft = core.begin_draft(scene, 2)
core.apply_research(scene, draft, replace(core.parameters(draft), yaw=240.), confirmed=True)
core.commit_draft(scene, 2, draft)
for body in scene.objects:
    if body.get('stacked_rig_slot') == 2:
        assert body.parent == core.get_camera(scene, 2)
        assert not body.hide_get()
core.clear_slot(scene, 2)
for body in scene.objects:
    if body.get('stacked_rig_slot') == 2:
        assert body.hide_get()
core.restore_layout(scene, layout)
for body in scene.objects:
    if body.get('stacked_rig_slot') == 2:
        assert body.parent == core.get_camera(scene, 2) and not body.hide_get()

report = {'status': 'PASS', 'blender': bpy.app.version_string, 'reference_frame': 1,
          'reference_time_s': 117., 'projection': 'ideal pinhole, no lens distortion or window refraction',
          'frustum_union_all_blade_vertices': union_covers_blade, 'adjacent_shared_span_m': overlaps,
          'cameras': rows, 'limits': 'Reference-pose frustum coverage is not full-surface visibility or stitching proof.'}
out = Path(os.environ.get('WFRL_TEST_OUTPUT', '/tmp/stacked-camera-regression'))
out.mkdir(parents=True, exist_ok=True)
(out/'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print('STACKED_CAMERAS_PASS', flush=True)
