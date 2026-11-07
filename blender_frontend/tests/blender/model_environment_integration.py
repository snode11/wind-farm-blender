"""Cross-scene model, display detail, camera and saved-environment source checks."""
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import farm_flex, manual_control, manual_down, manual_geometry

OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
assert Path(addon.__file__).resolve().parent == ROOT / 'blender_frontend/wfrl_blender'
assert bpy.app.background


def mesh_digest(mesh):
    points = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get('co', points)
    return hashlib.sha256(points.tobytes()).hexdigest()


def source_signature(scene):
    return {obj.name: dict(mesh=mesh_digest(obj.data),
                          matrix=tuple(value for row in obj.matrix_world for value in row),
                          materials=tuple((mat.name, tuple(mat.diffuse_color), mat.metallic, mat.roughness)
                                          for mat in obj.data.materials if mat))
            for obj in scene.objects if obj.type == 'MESH'}


def blades(scene):
    return [getattr(entry, f'blade{index}_object')
            for entry in scene.wfrl_manual_turbines for index in (1, 2, 3)]


def root_radius(obj):
    return max(math.hypot(vertex.co.x, vertex.co.y) for vertex in obj.data.vertices)


def check_replay_fittings(scene, model):
    for turbine in ('T1', 'T2', 'T3'):
        for index in (1, 2, 3):
            obj = scene.objects[f'WFRL.Turbine.{turbine}.Blade{index}.RootFairing']
            radius = root_radius(obj)
            assert (radius < 1.4 if model == 'rootkink' else radius > 1.7), (model, obj.name, radius)
    active = farm_flex.active_for(scene)
    assert active is not None and active.model == model
    mutable = [scene.objects[f'WFRL.Turbine.{turbine}.Blade{index}'].data.as_pointer()
               for turbine in ('T1', 'T2', 'T3') for index in (1, 2, 3)]
    mutable += [scene.objects[f'WFRL.Turbine.{turbine}.Tower'].data.as_pointer()
                for turbine in ('T1', 'T2', 'T3')]
    assert len(set(mutable)) == 12, 'Flexible leaves and towers require independent mesh data'


asset_hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (ROOT / 'blender_frontend/wfrl_blender/assets').rglob('*') if path.is_file()}
addon.register()
try:
    print('CHECK replay construction', flush=True)
    addon.load_demo_scene(camera_rig=True)
    source = bpy.context.scene
    source.name = 'Integrated replay source'
    source.frame_set(151)
    assert source.objects['WFRL.Terrain']['wfrl_landscape_detail'] == 'balanced'
    terrain_digest = mesh_digest(source.objects['WFRL.Terrain'].data)
    cameras = {obj.name: tuple(value for row in obj.matrix_world for value in row)
               for obj in source.objects if obj.type == 'CAMERA'}
    source_before = source_signature(source)
    print('CHECK independent manual construction', flush=True)
    assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
    manual = bpy.context.scene
    manual.name = 'Integrated manual model'
    assert manual_control.validate_scene(manual)[0]
    assert manual.get('wfrl_manual_blade_model') == 'rootkink'
    source_meshes = {obj.data.as_pointer() for obj in source.objects if obj.type == 'MESH'}
    assert source_meshes.isdisjoint(obj.data.as_pointer() for obj in manual.objects if obj.type == 'MESH')
    source_materials = {mat.as_pointer() for obj in source.objects
                        for mat in getattr(obj.data, 'materials', ()) if mat}
    manual_materials = {mat.as_pointer() for obj in manual.objects
                        for mat in getattr(obj.data, 'materials', ()) if mat}
    assert source_materials.isdisjoint(manual_materials)
    poses = manual_control.pose_snapshot(manual)
    detail_counts = {}
    for model in ('nrel5mw', 'rootkink'):
        print('CHECK manual detail and fittings', model, flush=True)
        manual.wfrl_manual_blade_shape = model
        assert manual['wfrl_manual_blade_model'] == model
        for detail, expected in (('OVERVIEW', 4417), ('BALANCED', 8065), ('CLOSEUP', 15361)):
            manual_geometry.set_detail(manual, detail)
            assert len({obj.data.as_pointer() for obj in blades(manual)}) == 1
            assert all(len(obj.data.vertices) == expected for obj in blades(manual))
            assert manual_control.pose_snapshot(manual) == poses
            detail_counts[model + ':' + detail] = expected
        for entry in manual.wfrl_manual_turbines:
            fairing = next(child for child in entry.blade1_object.parent.children
                           if child.name.endswith('.RootFairing'))
            radius = root_radius(fairing)
            assert (radius < 1.4 if model == 'rootkink' else radius > 1.7)
    manual_geometry.set_detail(manual, 'OVERVIEW')
    print('CHECK independent high-detail export clone', flush=True)
    clone = manual_down._clone_scene(manual)
    try:
        for turbine in ('T1', 'T2', 'T3'):
            camera = manual_down.ensure_down_camera(clone, turbine)
            assert camera.parent == manual_down._entry(clone, turbine).yaw_object
        assert clone['wfrl_manual_display_applied'] == 'CLOSEUP'
        assert all(len(obj.data.vertices) == 15361 for obj in blades(clone))
        assert all(len(obj.data.vertices) == 4417 for obj in blades(manual))
        assert manual_control.pose_snapshot(manual) == poses
    finally:
        manual_down._drop_scene(clone)
    print('CHECK return to replay', flush=True)
    assert bpy.ops.wfrl.manual_leave() == {'FINISHED'}
    assert bpy.context.scene == source and source.frame_current == 151
    assert source_signature(source) == source_before
    assert cameras == {obj.name: tuple(value for row in obj.matrix_world for value in row)
                       for obj in source.objects if obj.type == 'CAMERA'}
    print('CHECK replay fitting rollback and user protection', flush=True)
    root = source.objects['WFRL.Turbine.T1.Blade1.RootFairing']
    root_mesh = root.data
    coordinate = root_mesh.vertices[0].co.copy()
    root_mesh.vertices[0].co.x += .02
    edited = root_mesh.vertices[0].co.copy()
    source.wfrl_blade_model = 'rootkink'
    assert source.wfrl_blade_model == 'nrel5mw' and root.data == root_mesh
    assert root_mesh.vertices[0].co == edited
    root_mesh.vertices[0].co = coordinate
    protected = bpy.data.scenes.new('External fitting consumer')
    protected.collection.objects.link(root)
    try:
        source.wfrl_blade_model = 'rootkink'
        assert source.wfrl_blade_model == 'nrel5mw' and root.data == root_mesh
    finally:
        bpy.data.scenes.remove(protected)
    original_attach = farm_flex.attach
    attempts = []

    def reject_attachment(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError('Injected replay binding failure')
        return original_attach(*args, **kwargs)

    with patch.object(farm_flex, 'attach', reject_attachment):
        source.wfrl_blade_model = 'rootkink'
    assert len(attempts) == 2 and source.wfrl_blade_model == 'nrel5mw'
    assert root.data == root_mesh and root_mesh.vertices[0].co == coordinate
    check_replay_fittings(source, 'nrel5mw')
    for model in ('rootkink', 'nrel5mw', 'rootkink'):
        print('CHECK replay fittings', model, flush=True)
        source.wfrl_blade_model = model
        check_replay_fittings(source, model)
        assert source.frame_current == 151
        assert mesh_digest(source.objects['WFRL.Terrain'].data) == terrain_digest
    saved = OUT / 'integrated-models.blend'
    print('CHECK save and reopen', flush=True)
    duplicate = source.copy()
    assert duplicate.wfrl_manual_scene is None
    bpy.data.scenes.remove(duplicate)
    source['wfrl_manual_scene'] = manual
    assert bpy.ops.wm.save_as_mainfile(filepath=str(saved)) == {'FINISHED'}
    assert bpy.ops.wm.open_mainfile(filepath=str(saved)) == {'FINISHED'}
    source = bpy.data.scenes['Integrated replay source']
    manual = bpy.data.scenes['Integrated manual model']
    check_replay_fittings(source, 'rootkink')
    assert manual_control.validate_scene(manual)[0]
    assert source.wfrl_manual_scene == manual and manual.wfrl_manual_return_scene == source
    assert 'wfrl_manual_scene' not in source
    assert not any(entry.running for entry in manual.wfrl_manual_turbines)
    assert manual['wfrl_manual_display_applied'] == 'OVERVIEW'
    assert all(len(obj.data.vertices) == 4417 for obj in blades(manual))
    assert mesh_digest(source.objects['WFRL.Terrain'].data) == terrain_digest
    assert asset_hashes == {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in asset_hashes}
    result = dict(status='PASS', addon_file=addon.__file__, blender=bpy.app.version_string,
                  detail_vertices_per_blade=detail_counts, independent_materials_and_meshes=True,
                  independent_flexible_meshes=True, replay_root_fittings_follow_model=True,
                  replay_root_rollback_and_edited_shared_protection=True,
                  source_scene_copy_does_not_claim_manual=True, saved_scene_link_restore=True,
                  down_export_clone_closeup_source_overview=True,
                  source_pose_cameras_environment_preserved=True,
                  saved_reopen=True, formal_assets_unchanged=len(asset_hashes),
                  scope='Background source integration; no FPS, physical input or scientific acceptance')
    (OUT / 'checks.json').write_text(json.dumps(result, indent=2) + '\n')
    print('MODEL_ENVIRONMENT_INTEGRATION_PASS', json.dumps(result), flush=True)
finally:
    addon.unregister()
