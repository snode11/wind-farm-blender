"""Check same-source import rollback on native Blender datablocks.

Run with background factory startup and a new WFRL_TEST_OUTPUT directory.
This checks allocation and shared-resource ownership, not rendered pixels.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
from contextlib import contextmanager
from unittest.mock import patch

import bpy


ROOT = Path(__file__).resolve().parents[3]
GROUPS = ('objects', 'meshes', 'materials', 'images', 'shape_keys', 'actions', 'texts', 'cameras', 'node_groups')
OUTPUT = Path(os.environ['WFRL_TEST_OUTPUT']).resolve()
OUTPUT.mkdir(parents=True, exist_ok=False)
runpy.run_path(str(ROOT / 'scripts/blender/open_farm_flex.py'))
from wfrl_blender import blade_recon_review as review, blade_recon_inspection as inspection
from wfrl_blender import split_mappo_texture as texture, split_texture_settings as settings


def inventory():
    return {name: {item.as_pointer(): dict(name=item.name, users=item.users,
                  fake_user=item.use_fake_user) for item in getattr(bpy.data, name)}
            for name in GROUPS}


scene = bpy.context.scene
checks = []
result = dict(status='INCOMPLETE', blender=bpy.app.version_string, module=texture.__file__, checks=checks,
              scope='Native datablock rollback, external ownership and import retry; no rendered-pixel or performance acceptance')


class InjectedFault(RuntimeError):
    pass


def save():
    (OUTPUT / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')


def difference(before, after):
    return {name: dict(added=[row for pointer, row in after[name].items() if pointer not in before[name]],
                       removed=[row for pointer, row in before[name].items() if pointer not in after[name]],
                       changed=[dict(before=row, after=after[name][pointer]) for pointer, row in before[name].items()
                                if pointer in after[name] and row != after[name][pointer]])
            for name in GROUPS if before[name] != after[name]}


def fail_import():
    try:
        texture.ensure(scene)
    except InjectedFault:
        return
    raise AssertionError('Injected import failure was not raised')


@contextmanager
def allocation_fault(group, occurrence=1, *, fake_user=False, captured=None):
    register = texture._ImportResources.register
    count = 0
    def inject(resources, kind, block, **kwargs):
        nonlocal count
        register(resources, kind, block, **kwargs)
        if kind == group:
            count += 1
            if count == occurrence:
                if captured is not None:
                    captured.update(resources=resources, block=block)
                if fake_user:
                    block.use_fake_user = True
                raise InjectedFault(kind)
    with patch.object(texture._ImportResources, 'register', inject):
        yield
    assert count >= occurrence, (group, count, occurrence)


def complete_rollback(label, fault):
    before = inventory()
    properties = texture._import_properties(scene)
    display = settings.capture(scene)
    with fault:
        fail_import()
    after = inventory()
    delta = difference(before, after)
    assert not delta, (label, delta)
    assert texture._import_properties(scene) == properties, label
    assert settings.capture(scene) == display, label
    assert scene.as_pointer() not in texture._SESSIONS, label
    assert scene.as_pointer() not in texture._PENDING_IMPORTS, label
    checks.append(dict(name=label, status='PASS', inventory_restored=True, metadata_restored=True))
    save()


def source_context():
    return {obj.name: (obj.as_pointer(), obj.data.as_pointer(), len(obj.data.vertices))
            for obj in scene.objects if obj.type == 'MESH' and obj.name.startswith('WFRL.Turbine.T1.')}


def remove_mesh_resources(obj, mesh, material, image):
    if obj is not None:
        bpy.data.objects.remove(obj, do_unlink=True)
    assert mesh.users == 0 and not mesh.use_fake_user
    bpy.data.meshes.remove(mesh)
    assert material.users == 0 and not material.use_fake_user
    bpy.data.materials.remove(material)
    assert image.users == 0 and not image.use_fake_user
    bpy.data.images.remove(image)


try:
    scene['split_mappo_texture_user_note'] = 'Preserve saved adapter metadata'
    for name, value in dict(start_s=23., timeline_fps=24., stride=2, samples=99, frame_start=11).items():
        scene['split_reconstruction_' + name] = value
    source = source_context()
    complete_rollback('uv_failure_after_first_mesh',
                      patch.object(review, '_add_uv', side_effect=InjectedFault('uv')))
    for group, occurrence in (('materials', 1), ('images', 1), ('meshes', 1), ('objects', 1),
                               ('shape_keys', 1), ('actions', 1), ('cameras', 1), ('texts', 2), ('node_groups', 1)):
        complete_rollback('allocation_before_helper_return_' + group, allocation_fault(group, occurrence))

    update = inspection.update
    def after_inspection(*args, **kwargs):
        update(*args, **kwargs)
        raise InjectedFault('inspection complete')
    complete_rollback('activation_after_inspection_nodes', patch.object(inspection, 'update', after_inspection))

    context = texture._ensure_structure(scene)
    context_ids = [obj.as_pointer() for obj in context]
    canonical_data = bpy.data.cameras.new('User.CanonicalCameraData')
    canonical = bpy.data.objects.new('SplitRecon.Camera', canonical_data)
    scene.collection.objects.link(canonical)
    external_scene = bpy.data.scenes.new('User.ExternalScene')
    external_camera = bpy.data.objects.new('User.SharedCamera', canonical_data)
    external_scene.collection.objects.link(external_camera)
    external_scene.camera = canonical
    complete_rollback('copied_camera_data_failure_preserves_shared_source', allocation_fault('cameras'))
    assert canonical.data == external_camera.data == canonical_data
    assert external_scene.camera == canonical
    assert [obj.as_pointer() for obj in texture._ensure_structure(scene)] == context_ids

    shared_group = inspection._group()
    user_material = bpy.data.materials.new('User.InspectionMaterial')
    user_material.use_nodes = True
    user_node = user_material.node_tree.nodes.new('ShaderNodeGroup')
    user_node.node_tree = shared_group
    shared_group.use_fake_user = True
    complete_rollback('activation_preserves_existing_shared_inspection_group',
                      patch.object(inspection, 'update', after_inspection))
    assert user_node.node_tree == shared_group and shared_group.use_fake_user

    for group in ('materials', 'images', 'meshes', 'objects', 'shape_keys', 'actions', 'cameras'):
        before = inventory()
        captured = {}
        with allocation_fault(group, fake_user=True, captured=captured):
            fail_import()
        block = captured['block']
        assert block.use_fake_user and block.as_pointer() in inventory()[group], group
        for kind in GROUPS:
            for pointer, row in before[kind].items():
                assert inventory()[kind][pointer] == row, (group, kind, row)
        block.use_fake_user = False
        assert not captured['resources'].rollback(), group
        assert inventory() == before, (group, difference(before, inventory()))
        checks.append(dict(name='protect_fake_user_' + group, status='PASS', retry_reclaims_released_resource=True))
        save()

    for mode in ('shared_mesh', 'external_object_link', 'unregistered_third_party'):
        before = inventory()
        captured = {}
        def external_use(obj, *args):
            mesh, material = obj.data, obj.data.materials[0]
            image = material.node_tree.nodes['SurfaceTexture'].image
            captured.update(obj=obj, mesh=mesh, material=material, image=image)
            if mode == 'shared_mesh':
                foreign = bpy.data.objects.new('User.SharedEstimatedMesh', mesh)
                external_scene.collection.objects.link(foreign)
                captured['foreign'] = foreign
            elif mode == 'external_object_link':
                external_scene.collection.objects.link(obj)
            else:
                foreign_mesh = bpy.data.meshes.new('User.UnregisteredMesh')
                foreign = bpy.data.objects.new('SplitMappoTexture.ThirdParty', foreign_mesh)
                external_scene.collection.objects.link(foreign)
                captured.update(foreign=foreign, foreign_mesh=foreign_mesh)
            raise InjectedFault(mode)
        with patch.object(review, '_add_uv', external_use):
            fail_import()
        after = inventory()
        for kind in GROUPS:
            for pointer, row in before[kind].items():
                assert after[kind][pointer] == row, (mode, kind, row)
        if mode == 'shared_mesh':
            assert captured['foreign'].data == captured['mesh']
            remove_mesh_resources(captured['foreign'], captured['mesh'], captured['material'], captured['image'])
        elif mode == 'external_object_link':
            assert captured['obj'].name in external_scene.objects
            remove_mesh_resources(captured['obj'], captured['mesh'], captured['material'], captured['image'])
        else:
            assert captured['foreign'].name in external_scene.objects
            assert captured['foreign_mesh'].users == 1
            assert not any(row['name'] == texture.PREFIX + 'B1' for row in after['meshes'].values())
            bpy.data.objects.remove(captured['foreign'], do_unlink=True)
            bpy.data.meshes.remove(captured['foreign_mesh'])
        assert inventory() == before, (mode, difference(before, inventory()))
        checks.append(dict(name='protect_' + mode, status='PASS'))
        save()

    resources = texture._ImportResources(scene)
    camera_data = bpy.data.cameras.new('Txn.ExternalSceneReference')
    resources.register('cameras', camera_data)
    camera = bpy.data.objects.new('Txn.ExternalSceneReference', camera_data)
    resources.register('objects', camera)
    scene.collection.objects.link(camera)
    saved_camera = external_scene.camera
    external_scene.camera = camera
    assert not resources.rollback()
    assert external_scene.camera == camera and camera.data == camera_data
    external_scene.camera = saved_camera
    assert not resources.rollback()
    checks.append(dict(name='protect_external_scene_direct_camera_reference', status='PASS'))

    resources = texture._ImportResources(scene)
    text = bpy.data.texts.new('Txn.ExternalTextReference')
    resources.register('texts', text, owned_fake_user=True)
    text.use_fake_user = True
    external_scene['held_text'] = text
    assert not resources.rollback()
    assert external_scene['held_text'] == text and text.use_fake_user
    external_scene.pop('held_text')
    assert not resources.rollback()
    checks.append(dict(name='protect_external_text_user', status='PASS'))

    deleted_scene = bpy.data.scenes.new('Txn.DeletedScene')
    resources = texture._ImportResources(deleted_scene)
    material = bpy.data.materials.new('Txn.DeletedSceneMaterial')
    resources.register('materials', material)
    deleted_pointer = deleted_scene.as_pointer()
    texture._PENDING_IMPORTS[deleted_pointer] = resources
    bpy.data.scenes.remove(deleted_scene)
    assert not resources.belongs_to(scene)
    texture.clear_runtime()
    assert deleted_pointer not in texture._PENDING_IMPORTS
    assert bpy.data.materials.get('Txn.DeletedSceneMaterial') is None
    checks.append(dict(name='deleted_scene_owned_resource_cleanup', status='PASS'))

    value = texture.ensure(scene)
    assert scene[texture.MARKER] and texture.active(scene) and texture.available(scene)
    assert [obj.name for obj in value['blades']] == [texture.PREFIX + f'B{b}' for b in (1, 2, 3)]
    assert all(len(obj.data.shape_keys.key_blocks) == 601 and obj.data.shape_keys.animation_data.action is not None
               for obj in value['blades'])
    assert [obj.as_pointer() for obj in value['structure']] == context_ids
    assert source_context() == source
    assert user_node.node_tree == shared_group and shared_group.use_fake_user
    assert texture.ensure(scene) is value
    checks.append(dict(name='retry_import_commits_three_full_601_sample_blades', status='PASS',
                       shared_source_context_preserved=True, repeated_ensure_reuses_session=True))

    blend_file = OUTPUT / 'portable.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_file))
    resources = texture._ImportResources(scene)
    pending_mesh = bpy.data.meshes.new('Txn.BeforeFileLoad')
    resources.register('meshes', pending_mesh)
    texture._PENDING_IMPORTS[scene.as_pointer()] = resources
    bpy.ops.wm.open_mainfile(filepath=str(blend_file))
    scene = bpy.context.scene
    assert not texture._PENDING_IMPORTS
    assert bpy.data.meshes.get('Txn.BeforeFileLoad') is None
    restored = texture.ensure(scene)
    assert all(len(obj.data.shape_keys.key_blocks) == 601 for obj in restored['blades'])
    checks.append(dict(name='file_load_discards_pending_references_and_restores_saved_import', status='PASS'))
    result['status'] = 'PASS'
    save()
    print('SPLIT_MAPPO_TEXTURE_ROLLBACK_PASS', flush=True)
except Exception as error:
    result.update(status='FAIL', error=repr(error))
    save()
    raise
