"""Saved manual mesh compatibility and custom vertex-weight protection.

Use a new WFRL_TEST_OUTPUT directory and isolated background factory startup.
The unversioned fixture contains high-detail blades and NREL-sized fittings.
"""
import json
import math
import os
from pathlib import Path
import sys
import traceback
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT/'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import manual_control, manual_down, manual_geometry, manual_scene
from wfrl_blender.turbine_geometry import ROOT_CANT_DEG, blade_mesh

OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
assert bpy.app.background and '--factory-startup' in sys.argv
assert Path(addon.__file__).resolve().parent == ROOT/'blender_frontend/wfrl_blender'
RESULT = {'status': 'RUNNING', 'blender': bpy.app.version_string,
          'addon_file': addon.__file__, 'checks': {}}


def blades(scene):
    return [getattr(entry, f'blade{i}_object') for entry in scene.wfrl_manual_turbines for i in (1, 2, 3)]


def unversioned_model(scene):
    model = scene.wfrl_manual_blade_shape
    signature = f'{model}:cant:{ROOT_CANT_DEG.get(model, 0.0):g}'
    objects = blades(scene)
    if all(obj.data.get('wfrl_manual_geometry') == signature for obj in objects):
        return
    vertices, _faces = blade_mesh(8, 96, model=model)
    sources = {obj.data.as_pointer(): obj.data for obj in objects}
    replacements = {}
    for key, mesh in sources.items():
        replacement = mesh.copy()
        replacement.name = scene['wfrl_manual_owner'] + '.' + model + '.Blade'
        replacement.vertices.foreach_set('co', [value for vertex in vertices for value in vertex])
        replacement['wfrl_manual_blade_model'] = model
        replacement['wfrl_manual_geometry'] = signature
        replacement['wfrl_manual_mesh_owner'] = scene['wfrl_manual_owner']
        replacement.update()
        replacements[key] = replacement
    for obj in objects:
        obj.data = replacements[obj.data.as_pointer()]
    scene['wfrl_manual_blade_model'] = model


def fresh_scene(name, *, unversioned=None, share=True):
    source = bpy.data.scenes.new(name + '.Source')
    bpy.context.window.scene = source
    if unversioned is not None:
        with patch.object(manual_geometry, 'tag_generated_assembly'), \
                patch.object(manual_geometry, 'share_static_meshes'), \
                patch.object(manual_scene, '_ensure_blade_model', unversioned_model):
            scene = manual_scene.create(bpy.context)
            scene.wfrl_manual_blade_shape = unversioned
    elif share:
        scene = manual_scene.create(bpy.context)
    else:
        with patch.object(manual_geometry, 'share_static_meshes'):
            scene = manual_scene.create(bpy.context)
    scene.name = name
    return scene


def reject(function):
    try:
        function()
    except ValueError:
        return
    raise AssertionError('Customized geometry must not be replaced')


def migration():
    expected = {}
    for model in ('rootkink', 'nrel5mw'):
        scene = fresh_scene('Unversioned.' + model, unversioned=model)
        scene.wfrl_manual_turbines[0].yaw_deg = 17
        scene.wfrl_manual_turbines[1].azimuth_deg = 37
        scene.camera.data.lens = 57
        expected[scene.name] = (manual_control.pose_snapshot(scene), scene.camera.data.lens, model)
    saved = OUT/'unversioned-manual.blend'
    assert bpy.ops.wm.save_as_mainfile(filepath=str(saved)) == {'FINISHED'}
    assert bpy.ops.wm.open_mainfile(filepath=str(saved)) == {'FINISHED'}
    for name, (pose, lens, model) in expected.items():
        scene = bpy.data.scenes[name]
        bpy.context.window.scene = scene
        manual_geometry.set_detail(scene, 'BALANCED')
        assert scene['wfrl_manual_display_applied'] == 'BALANCED'
        assert all(len(obj.data.vertices) == 8065 for obj in blades(scene))
        assert manual_control.pose_snapshot(scene) == pose
        assert scene.camera.data.lens == lens
        for obj in blades(scene):
            fairing = next(child for child in obj.parent.children if child.name.endswith('.RootFairing'))
            radius = max(math.hypot(v.co.x, v.co.y) for v in fairing.data.vertices)
            assert radius < 1.4 if model == 'rootkink' else radius > 1.7
        manual_down.ensure_down_camera(scene, 'T1')
        assert scene['wfrl_manual_display_applied'] == 'CLOSEUP'
        assert all(len(obj.data.vertices) == 15361 for obj in blades(scene))
        assert manual_control.pose_snapshot(scene) == pose


def preserve_unversioned_edits():
    scene = fresh_scene('CustomizedUnversioned', unversioned='rootkink')
    fairing = next(child for child in blades(scene)[0].parent.children if child.name.endswith('.RootFairing'))
    fairing.data.vertices[0].co.x += .02
    before = {obj.name: (obj.data.as_pointer(), manual_geometry.mesh_fingerprint(obj.data))
              for obj in scene.objects if obj.type == 'MESH'}
    count = len(bpy.data.meshes)
    reject(lambda: manual_geometry.set_detail(scene, 'BALANCED'))
    assert before == {obj.name: (obj.data.as_pointer(), manual_geometry.mesh_fingerprint(obj.data))
                      for obj in scene.objects if obj.type == 'MESH'}
    assert len(bpy.data.meshes) == count


def preserve_blade_weights():
    scene = fresh_scene('WeightedBlade')
    blade = blades(scene)[0]
    pointer = blade.data.as_pointer()
    group = blade.vertex_groups.new(name='User deformation weights')
    group.add([0, 1, 2], .7, 'REPLACE')
    expected = [group.weight(i) for i in (0, 1, 2)]
    reject(lambda: manual_geometry.set_detail(scene, 'BALANCED'))
    assert blade.data.as_pointer() == pointer
    assert [group.weight(i) for i in (0, 1, 2)] == expected
    assert scene['wfrl_manual_display_applied'] == 'OVERVIEW'


def preserve_static_weights():
    scene = fresh_scene('WeightedStaticPart', share=False)
    shells = [next(obj for obj in entry.yaw_object.children if obj.get('wfrl_nacelle_shell'))
              for entry in scene.wfrl_manual_turbines]
    shell = shells[1]
    pointer = shell.data.as_pointer()
    group = shell.vertex_groups.new(name='User bevel weights')
    group.add([0, 1, 2], .4, 'REPLACE')
    expected = [group.weight(i) for i in (0, 1, 2)]
    manual_geometry.share_static_meshes(scene)
    assert shell.data.as_pointer() == pointer
    assert [group.weight(i) for i in (0, 1, 2)] == expected
    assert shells[0].data == shells[2].data and shell.data != shells[0].data


addon.register()
try:
    for name, function in (('saved_unversioned_models_upgrade', migration),
                           ('edited_unversioned_fittings_preserved', preserve_unversioned_edits),
                           ('display_change_preserves_user_weights', preserve_blade_weights),
                           ('static_reuse_preserves_user_weights', preserve_static_weights)):
        try:
            function()
            RESULT['checks'][name] = {'status': 'PASS'}
        except Exception:
            RESULT['checks'][name] = {'status': 'FAIL', 'error': traceback.format_exc()}
    RESULT['status'] = 'PASS' if all(row['status'] == 'PASS' for row in RESULT['checks'].values()) else 'FAIL'
finally:
    addon.unregister()
    (OUT/'checks.json').write_text(json.dumps(RESULT, ensure_ascii=False, indent=2) + '\n')
    print('MANUAL_GEOMETRY_COMPATIBILITY_' + RESULT['status'], json.dumps(RESULT), flush=True)
assert RESULT['status'] == 'PASS'
