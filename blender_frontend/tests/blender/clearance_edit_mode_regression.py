"""Saved Edit Mode upgrades must not alter the user's mesh or editor state.

Run with --background --factory-startup --python-exit-code 1 and an isolated
WFRL_TEST_OUTPUT. WFRL_TEST_PACKAGE_ROOT optionally selects an installed build.
"""
from pathlib import Path
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUTPUT.mkdir(parents=True, exist_ok=True)
sys.path[:0] = [os.environ.get('WFRL_TEST_PACKAGE_ROOT', str(ROOT / 'blender_frontend')), str(ROOT)]

import bpy
import bmesh
import wfrl_blender as addon
from wfrl_blender.clearance_visual import ensure_radar


def old_rig(scene, tid):
    yaw = bpy.data.objects.new(f'WFRL.Turbine.{tid}.YawRoot', None)
    scene.collection.objects.link(yaw)
    for i in range(1, 4):
        name = f'WFRL.Turbine.{tid}.ClearanceRadar.Beam{i}'
        curve = bpy.data.curves.new(name, 'CURVE')
        curve.dimensions = '3D'
        curve.splines.new('POLY').points.add(1)
        curve.splines[0].points[0].co = (-2, 0, -.25, 1)
        curve.splines[0].points[1].co = (-2 - i, 0, -60.25, 1)
        beam = bpy.data.objects.new(name, curve)
        scene.collection.objects.link(beam)
        beam.parent = yaw
    return yaw


def editor_state():
    obj = bpy.context.view_layer.objects.active
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.index_update()
    return dict(
        scene=bpy.context.scene.name, view_layer=bpy.context.view_layer.name,
        mode=bpy.context.mode, name=obj.name, mesh_name=obj.data.name,
        parent=obj.parent.name if obj.parent else None,
        transform=[list(row) for row in obj.matrix_basis],
        parent_inverse=[list(row) for row in obj.matrix_parent_inverse],
        selected=sorted(o.name for o in bpy.context.view_layer.objects if o.select_get()),
        vertices=[(tuple(v.co), v.select, v.hide) for v in bm.verts],
        edges=[(tuple(v.index for v in e.verts), e.select, e.hide) for e in bm.edges],
        faces=[(tuple(v.index for v in f.verts), f.select, f.hide) for f in bm.faces],
    )


def beam_directions():
    result = {}
    for obj in bpy.data.objects:
        if '.ClearanceRadar.Beam' in obj.name:
            points = obj.data.splines[0].points
            result[obj.name] = (tuple(points[0].co), tuple((points[1].co.xyz - points[0].co.xyz).normalized()))
    return result


scene = bpy.context.scene
yaw = old_rig(scene, 'T1')
# A second, inactive scene must be upgraded without switching context.
old_rig(bpy.data.scenes.new('Inactive wind farm'), 'T9')
mesh = bpy.data.meshes.new('User tower mesh')
bm = bmesh.new()
bmesh.ops.create_cube(bm, size=2)
bm.to_mesh(mesh)
bm.free()
tower = bpy.data.objects.new('WFRL.Turbine.T1.Tower', mesh)
scene.collection.objects.link(tower)
tower.parent = yaw
tower.location = (2, 3, 4)
tower.rotation_euler = (.1, .2, .3)
tower.scale = (2, 2, 20)
for obj in bpy.context.view_layer.objects:
    obj.select_set(False)
tower.select_set(True)
bpy.context.view_layer.objects.active = tower
bpy.ops.object.mode_set(mode='EDIT')
bm = bmesh.from_edit_mesh(mesh)
for face in bm.faces:
    face.select_set(False)
for edge in bm.edges:
    edge.select_set(False)
for i, vertex in enumerate(bm.verts):
    vertex.select_set(i == 0)
bmesh.update_edit_mesh(mesh)

legacy_path = OUTPUT / 'legacy-edit-mode.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(legacy_path))
# Establish Blender's saved/reloaded state before enabling migration.
bpy.ops.wm.open_mainfile(filepath=str(legacy_path))
before, directions = editor_state(), beam_directions()
addon.register()
bpy.ops.wm.open_mainfile(filepath=str(legacy_path))
assert editor_state() == before, 'Automatic upgrade changed the edited mesh or editor state'
for name, (origin, direction) in directions.items():
    actual_origin, actual_direction = beam_directions()[name]
    assert all(abs(a-b) < 1e-6 for a, b in zip(origin, actual_origin)), name
    assert all(abs(a-b) < 1e-6 for a, b in zip(direction, actual_direction)), name
for tid, owner in [('T1', bpy.context.scene), ('T9', bpy.data.scenes['Inactive wind farm'])]:
    assert owner['wfrl_surface_revision'] == 1
    prefix = f'WFRL.Turbine.{tid}.ClearanceRadar'
    assert len(owner.objects[prefix + '.MountPlate'].data.vertices) == 8
    assert len(owner.objects[prefix + '.CoverScrew0'].data.vertices) == 64
    assert owner.objects[prefix + '.Cable'].parent == owner.objects[f'WFRL.Turbine.{tid}.YawRoot']

counts = (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.materials))
ensure_radar(bpy.context.scene, 'T1')
assert editor_state() == before, 'Refreshing existing fittings changed editor state'
assert counts == (len(bpy.data.objects), len(bpy.data.meshes), len(bpy.data.materials))
upgraded_path = OUTPUT / 'upgraded-edit-mode.blend'
def used_assets():
    # Blender discards orphan datablocks on reload; only used assets persist.
    return tuple(tuple(sorted(item.name for item in group if item.users > 0))
                 for group in (bpy.data.objects, bpy.data.meshes, bpy.data.materials))
saved_assets = used_assets()
bpy.ops.wm.save_as_mainfile(filepath=str(upgraded_path))
bpy.ops.wm.open_mainfile(filepath=str(upgraded_path))
assert editor_state() == before, 'Saving/reopening the upgraded scene changed editor state'
assert used_assets() == saved_assets, 'Saving/reopening changed used assets'
report = dict(status='PASS', blender=bpy.app.version_string, module=addon.__file__,
              saved_edit_mode_upgrade=True, mesh_and_editor_state_preserved=True,
              inactive_scene_upgrade=True, beam_calibration_preserved=True,
              idempotent_refresh=True, upgraded_save_reload=True)
(OUTPUT / 'results.json').write_text(json.dumps(report, indent=2))
addon.unregister()
print('CLEARANCE_EDIT_MODE_REGRESSION_PASS', json.dumps(report), flush=True)
