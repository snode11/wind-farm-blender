"""Demo rebuilds release generated meshes and retain protected data."""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender
from wfrl_blender import farm_flex, repair_marks, scene_builder, split_turbine_context, stacked_camera_rig

assert Path(wfrl_blender.__file__).resolve().parent == ROOT / 'blender_frontend/wfrl_blender'
assert bpy.app.background, 'Run this regression in an independent background Blender'
assert not hasattr(bpy.types.Scene, 'wfrl_blade_model'), 'Use --factory-startup'
wfrl_blender.register()

user_meshes = [bpy.data.meshes.new(name) for name in
               ('User.Unused.Keep', 'WFRL.User.Unused.Keep')]
counts = []
for _ in range(4):
    wfrl_blender.load_demo_scene()
    counts.append(len(bpy.data.meshes))
    assert all(bpy.data.meshes.get(mesh.name) == mesh for mesh in user_meshes)
    assert not any(mesh.name.startswith('WFRL.Turbine.') and '.Tower.Mesh' in mesh.name
                   for mesh in bpy.data.meshes), 'Committed bindings left initial towers unused'
    assert all(obj.data.name.startswith(stacked_camera_rig.PREFIX)
               for obj in bpy.context.scene.objects if obj.get('stacked_rig_physical'))
assert len(set(counts)) == 1, counts
print('REBUILD_MESH_COUNTS', counts)

scene = bpy.context.scene
external = bpy.data.scenes.new('User mesh references')


def share(mesh, name):
    obj = bpy.data.objects.new(name, mesh)
    external.collection.objects.link(obj)
    return obj


def mesh_ids():
    return {mesh.as_pointer() for mesh in bpy.data.meshes}


def assert_present(mesh):
    assert any(candidate == mesh for candidate in bpy.data.meshes), mesh.name


fake = scene.objects[stacked_camera_rig.PREFIX + '.Back'].data
fake.use_fake_user = True
shape_object = scene.objects[stacked_camera_rig.PREFIX + '.Side']
shape_object.shape_key_add(name='Basis')
shape = shape_object.data
shared = scene.objects[stacked_camera_rig.PREFIX + '.End'].data
shared_object = share(shared, 'User shared housing')
shared_vertices = tuple(tuple(v.co) for v in shared.vertices)
attached_user = bpy.data.meshes.new('User.Attached.Keep')
attached_object = bpy.data.objects.new('WFRL.UserMeshHolder', attached_user)
bpy.data.collections[scene_builder.COLLECTION_NAME].objects.link(attached_object)

with tempfile.TemporaryDirectory(prefix='mesh-library-', dir=os.environ.get('WFRL_TEST_TEMP')) as temp:
    library_path = Path(temp) / 'protected.blend'
    source = bpy.data.meshes.new('WFRL.Protected.LinkedMesh')
    source['wfrl_farm_flex_owned'] = True
    bpy.data.libraries.write(str(library_path), {source})
    bpy.data.meshes.remove(source)
    with bpy.data.libraries.load(str(library_path), link=True) as (available, loaded):
        loaded.meshes = [available.meshes[0]]
    linked = loaded.meshes[0]
    linked_object = bpy.data.objects.new('WFRL.LinkedMeshHolder', linked)
    bpy.data.collections[scene_builder.COLLECTION_NAME].objects.link(linked_object)
    wfrl_blender.load_demo_scene()
    for mesh in (*user_meshes, fake, shape, shared, attached_user, linked):
        assert_present(mesh)
    assert fake.use_fake_user and fake.users == 1
    assert shape.shape_keys is not None and shape.users == 0
    assert shared_object.data == shared and shared.users == 1
    assert tuple(tuple(v.co) for v in shared.vertices) == shared_vertices
    assert attached_user.users == 0
    assert linked.library is not None and linked.users == 0
    farm_flex._MeshBindings._release(linked)
    assert_present(linked)
    protected_counts = [len(bpy.data.meshes)]
    for _ in range(2):
        wfrl_blender.load_demo_scene()
        protected_counts.append(len(bpy.data.meshes))
    assert len(set(protected_counts)) == 1, protected_counts
    for mesh in (*user_meshes, fake, shape, shared, attached_user, linked):
        assert_present(mesh)

# Initial binding failures preserve the original cosmetic attachment as well
# as the structural meshes, including failures at shared-structure rebinding.
for failure in ('tower', 'shared_structure'):
    wfrl_blender.build_demo_geometry()
    before_ids = mesh_ids()
    before_bindings = {obj.name: obj.data for obj in scene.objects if obj.type == 'MESH'}
    original_patch = scene.objects[repair_marks.NAME]
    original_source = scene.objects[repair_marks.SOURCE_NAME]
    patch_coordinates = tuple(tuple(v.co) for v in original_patch.data.vertices)
    replace = farm_flex._MeshBindings.replace
    rebind = split_turbine_context.rebind_meshes
    injected = []

    def fail_replace(bindings, obj, mesh):
        replace(bindings, obj, mesh)
        if failure == 'tower' and obj.name == 'WFRL.Turbine.T2.Tower':
            injected.append(failure)
            raise RuntimeError('Injected initial binding failure')

    def fail_rebind(current_scene, bindings):
        rebind(current_scene, bindings)
        if failure == 'shared_structure':
            injected.append(failure)
            raise RuntimeError('Injected initial binding failure')

    with patch.object(farm_flex._MeshBindings, 'replace', fail_replace), \
            patch.object(split_turbine_context, 'rebind_meshes', fail_rebind):
        try:
            farm_flex.FarmFlex(scene, farm_flex.default_package())
        except RuntimeError as exc:
            assert str(exc) == 'Injected initial binding failure'
        else:
            raise AssertionError('Initial binding failure was not reached')
    assert injected == [failure]
    assert mesh_ids() == before_ids, failure
    assert all(scene.objects[name].data == mesh for name, mesh in before_bindings.items()), failure
    assert scene.objects[repair_marks.NAME] == original_patch
    assert scene.objects[repair_marks.SOURCE_NAME] == original_source
    assert tuple(tuple(v.co) for v in original_patch.data.vertices) == patch_coordinates
    assert original_source.data == scene.objects[repair_marks.TARGET_NAME].data

# The initial generated towers remain available until binding commits.
wfrl_blender.build_demo_geometry()
towers = [scene.objects[f'WFRL.Turbine.T{index}.Tower'] for index in (1, 2, 3)]
initial = [obj.data for obj in towers]
assert all(mesh.get('wfrl_farm_flex_owned') for mesh in initial)
before_ids = mesh_ids()
before_bindings = {obj.name: obj.data for obj in scene.objects if obj.type == 'MESH'}
bindings = farm_flex._MeshBindings()
injected = []


try:
    for obj in towers:
        bindings.replace(obj, bindings.own(obj.data.copy()))
        if obj.name == 'WFRL.Turbine.T2.Tower':
            injected.append(obj.name)
            raise RuntimeError('Injected tower binding failure')
except RuntimeError as exc:
    assert str(exc) == 'Injected tower binding failure'
    bindings.rollback()
else:
    raise AssertionError('Binding failure injection was not reached')
assert injected == ['WFRL.Turbine.T2.Tower']
assert mesh_ids() == before_ids, 'Rolled-back tower transaction changed the mesh inventory'
assert all(scene.objects[name].data == mesh for name, mesh in before_bindings.items())
assert [obj.data for obj in towers] == initial

# Successful binding retains initial towers with external or deliberate users.
shared_tower = share(initial[0], 'User shared initial tower')
initial[1].use_fake_user = True
towers[2].shape_key_add(name='Basis')
farm_flex.attach(scene, farm_flex.default_package())
assert all(obj.data != mesh for obj, mesh in zip(towers, initial))
assert all(any(candidate == mesh for candidate in bpy.data.meshes) for mesh in initial)
assert shared_tower.data == initial[0] and initial[0].users == 1
assert initial[1].use_fake_user and initial[1].users == 1
assert initial[2].shape_keys is not None and initial[2].users == 0

report = {
    'status': 'PASS',
    'blender_version': bpy.app.version_string,
    'addon_source': str(Path(wfrl_blender.__file__).resolve()),
    'rebuild_mesh_counts': counts,
    'protected_rebuild_mesh_counts': protected_counts,
    'unreferenced_and_attached_user_meshes_preserved': True,
    'shared_fake_user_shape_key_and_linked_data_preserved': True,
    'initial_tower_mesh_transaction_rollback_inventory_and_bindings_preserved': True,
    'initial_tower_commit_retention_preserved': True,
    'initial_binding_attachment_rollback': ['tower', 'shared_structure'],
}
if os.environ.get('WFRL_TEST_OUTPUT'):
    output = Path(os.environ['WFRL_TEST_OUTPUT']).resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / 'checks.json').write_text(json.dumps(report, indent=2) + '\n')
print('REBUILD_MESH_DETAILS', json.dumps(report))
print('REBUILD_MESH_REGRESSION=PASS')
