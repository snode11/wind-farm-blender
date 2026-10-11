"""Native shared turbine context regression; never saves a canonical asset.

Run Blender --background --factory-startup --python-exit-code 1 --python this_file.
WFRL_TEST_OUTPUT must select a new directory. WFRL_ADDON_MODULE defaults to the
workspace wfrl_blender; an installed bl_ext module also requires an exact
WFRL_EXPECTED_ADDON_ROOT. WFRL_SPLIT_INPUT may select another legacy split file.
The result is background data evidence only, not visible-window acceptance.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import traceback

import bpy
import numpy as np
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[3]
GEOMETRY_SHA256 = '3475e8e43e489076481da2365b3d7179f8667c065ee8dc580486d374c98f616b'
SOURCE = 'WFRL.Turbine.T1.'
GEOMETRY = dict(prefix='SplitRecon.Reference.', offset=(0., 10000., 0.),
                names_key='split_reconstruction_structure_names')
TEXTURE = dict(prefix='SplitMappoTexture.Reference.', offset=(10000., 0., 0.),
               names_key='split_mappo_texture_structure_names')
CORE = ('Tower', 'Nacelle', 'Hub', 'Spinner')
REPRESENTATIVES = CORE + ('YawSeal', 'TowerWeld28', 'NacelleRoof',
                           'ServiceDoor.Panel', 'RoofGasket', 'Vent.Louvre1.0')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def array(collection, field, width, dtype=np.float32):
    values = np.empty(len(collection) * width, dtype=dtype)
    collection.foreach_get(field, values)
    return values


def action_signature(action):
    if action is None:
        return None
    rows = []
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                for curve in bag.fcurves:
                    rows.append(dict(path=curve.data_path, index=curve.array_index,
                        extrapolation=curve.extrapolation, points=[
                            [list(p.co), list(p.handle_left), list(p.handle_right),
                             p.interpolation, p.easing, p.handle_left_type, p.handle_right_type]
                            for p in curve.keyframe_points]))
    return digest(json.dumps(rows, sort_keys=True).encode())


def mesh_signature(obj):
    """Include every saved key and action, UV data and packed image content."""
    mesh = obj.data
    keys = mesh.shape_keys
    shape = hashlib.sha256()
    if keys:
        for key in keys.key_blocks:
            shape.update(array(key.data, 'co', 3).tobytes())
            shape.update(json.dumps([key.name, key.frame, key.interpolation]).encode())
    images = []
    for material in mesh.materials:
        if material and material.use_nodes:
            for node in material.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    image = node.image
                    packed = image.packed_file
                    images.append(dict(node=node.name, size=list(image.size),
                                       packed_sha256=digest(packed.data) if packed else None))
    return dict(vertices=digest(array(mesh.vertices, 'co', 3).tobytes()),
                loops=digest(array(mesh.loops, 'vertex_index', 1, np.int32).tobytes()),
                uv={uv.name: digest(array(uv.data, 'uv', 2).tobytes()) for uv in mesh.uv_layers},
                shape_keys=len(keys.key_blocks) if keys else 0,
                shape_coordinates_sha256=shape.hexdigest() if keys else None,
                shape_action=action_signature(keys.animation_data.action)
                    if keys and keys.animation_data else None,
                object_action=action_signature(obj.animation_data.action) if obj.animation_data else None,
                images=images)


def right_signature(scene):
    return {obj.name: mesh_signature(obj) for obj in scene.objects
            if obj.type == 'MESH' and any(obj.name == prefix + str(bid)
                for prefix in ('SplitRecon.B', 'SplitMappoTexture.B') for bid in (1, 2, 3))}


def source_blade_signature(scene):
    return {obj.name: mesh_signature(obj) for obj in scene.objects
            if obj.type == 'MESH' and obj.name.startswith('WFRL.Turbine.') and '.Blade' in obj.name}


def modifiers_signature(obj):
    result = []
    for modifier in obj.modifiers:
        properties = {'type': modifier.type}
        for prop in modifier.bl_rna.properties:
            if prop.identifier == 'rna_type' or prop.type == 'COLLECTION' or prop.is_readonly:
                continue
            value = getattr(modifier, prop.identifier)
            if getattr(prop, 'is_array', False):
                value = list(value)
            elif prop.type == 'POINTER':
                value = value.name if value else None
            if isinstance(value, (type(None), bool, str, int, float, list)):
                properties[prop.identifier] = value
        result.append(properties)
    return result


def expected_matrix(scene, source, offset):
    # Independent expression of the capture's nacelle-local model coordinates.
    flip = Matrix.Diagonal((-1., -1., 1., 1.))
    flip.translation = (0., 0., -87.6)
    origin = scene.objects[SOURCE + 'YawRoot'].matrix_world @ flip
    return Matrix.Translation(offset) @ origin.inverted() @ source.matrix_world


def context_snapshot(scene, group):
    names = list(scene[group['names_key']])
    assert len(names) == len(set(names)) and len(names) > 4, names
    result = {}
    for name in names:
        obj = scene.objects[name]
        assert name.startswith(group['prefix'])
        suffix = name.removeprefix(group['prefix'])
        assert not suffix.startswith(('Blade', 'ClearanceRadar', 'Sensor', 'Wake', 'Camera', 'Beam'))
        source_name = obj['split_turbine_context_source']
        assert source_name == SOURCE + suffix, (name, source_name)
        source = scene.objects[source_name]
        assert obj.data is source.data, (name, 'source mesh was copied or left stale')
        assert obj.parent is None and not obj.animation_data
        assert obj['split_turbine_context_classification'] == 'SIMULATED_MODEL_CONTEXT'
        assert obj['split_turbine_context_component'] == suffix
        assert list(obj.data.materials) == list(source.data.materials)
        assert [(slot.link, slot.material) for slot in obj.material_slots] == [
            (slot.link, slot.material) for slot in source.material_slots]
        assert modifiers_signature(obj) == modifiers_signature(source), name
        assert np.array_equal(array(obj.data.polygons, 'use_smooth', 1, np.bool_),
                              array(source.data.polygons, 'use_smooth', 1, np.bool_))
        error = float(np.max(np.abs(np.asarray(obj.matrix_world) -
                                    np.asarray(expected_matrix(scene, source, group['offset'])))))
        assert error < 2e-3, (scene.frame_current, name, error)
        result[suffix] = dict(matrix=list(map(list, obj.matrix_world)),
                             mesh_sha256=digest(array(obj.data.vertices, 'co', 3).tobytes()),
                             vertices=len(obj.data.vertices), polygons=len(obj.data.polygons),
                             smooth_polygons=sum(p.use_smooth for p in obj.data.polygons),
                             matrix_error=error)
    assert all(suffix in result for suffix in REPRESENTATIVES), list(result)
    # These are the detailed source meshes, not the original cylinder/cuboid.
    assert result['Tower']['vertices'] > 64 and result['Nacelle']['vertices'] > 8
    assert result['Hub']['vertices'] > 64
    return result


class Validation:
    def __init__(self):
        self.module = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
        if self.module == 'wfrl_blender':
            sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
        self.addon = importlib.import_module(self.module)
        self.root = Path(self.addon.__file__).resolve().parent
        expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
        if expected:
            assert self.root == Path(expected).expanduser().resolve(), (self.root, expected)
        if self.module.startswith('bl_ext.'):
            assert expected, 'Installed evidence requires WFRL_EXPECTED_ADDON_ROOT'
        self.context = importlib.import_module(self.module + '.split_turbine_context')
        self.same = importlib.import_module(self.module + '.split_mappo_texture')
        self.farm = importlib.import_module(self.module + '.farm_flex')
        self.clearance = importlib.import_module(self.module + '.clearance_replay')

    def restore(self):
        if not hasattr(bpy.types.Scene, 'wfrl_recon_show_texture'):
            self.addon.register()
        if not self.farm.is_active(bpy.context.scene):
            self.clearance.on_load(None)
        assert self.farm.is_active(bpy.context.scene), bpy.context.scene.get('wfrl_clearance_status')
        return bpy.context.scene

    def ensure_unchanged(self, scene, group):
        before_right = right_signature(scene)
        before_source = source_blade_signature(scene)
        objects = self.context.ensure(scene, **group)
        assert right_signature(scene) == before_right
        assert source_blade_signature(scene) == before_source
        return objects

    def stable_runtime(self, scene):
        """Stable refresh must preserve Blender data without a full rebind."""
        self.same.refresh(scene)
        bpy.context.view_layer.update()
        before = right_signature(scene)
        pointers = [obj.as_pointer() for obj in self.same.objects(scene)]
        names = ('_sources', '_existing', '_apply')
        originals = {name: getattr(self.context, name) for name in names}
        calls = {name: 0 for name in names}

        def counted(name):
            def invoke(*args, **kwargs):
                calls[name] += 1
                return originals[name](*args, **kwargs)
            return invoke

        try:
            for name in names:
                setattr(self.context, name, counted(name))
            for _ in range(20):
                self.same.refresh(scene)
                bpy.context.view_layer.update()
            assert calls == dict.fromkeys(names, 0), calls
        finally:
            for name, function in originals.items():
                setattr(self.context, name, function)
        assert [obj.as_pointer() for obj in self.same.objects(scene)] == pointers
        assert right_signature(scene) == before
        return dict(refreshes=20, full_rebind_calls=calls, identity_and_payload_preserved=True,
                    scope='paused native data refresh; not an FPS measurement')

    def legacy_texture_fixture(self, scene):
        """Recreate the saved three-placeholder contract from the old asset."""
        assert TEXTURE['names_key'] not in scene
        names = [GEOMETRY['prefix'] + part for part in CORE[:3]]
        asset = self.root / 'assets/examples/mappo_reconstruction_split.blend'
        with bpy.data.libraries.load(str(asset), link=False) as (available, requested):
            assert all(name in available.objects for name in names)
            requested.objects = names
        for part, obj in zip(CORE[:3], requested.objects):
            obj.name = TEXTURE['prefix'] + part
            obj.parent = None
            obj.animation_data_clear()
            obj['split_mappo_texture_owned'] = True
            obj['split_texture_owned'] = True
            obj['split_mappo_texture_structure'] = 'SIMULATED_MODEL_CONTEXT'
            obj['split_mappo_texture_structure_component'] = part
            obj['split_mappo_texture_structure_source'] = GEOMETRY['prefix'] + part
            scene.collection.objects.link(obj)
        scene[TEXTURE['names_key']] = [obj.name for obj in requested.objects]
        return {part: len(obj.data.vertices) for part, obj in zip(CORE[:3], requested.objects)}

    def rejects_damaged_saved(self, scene, group):
        key = group['names_key']
        saved = list(scene[key])
        for invalid in ([*saved, saved[0]], ['missing saved component', *saved[1:]]):
            scene[key] = invalid
            try:
                try:
                    self.context.ensure(scene, **group)
                except ValueError:
                    pass
                else:
                    raise AssertionError('Invalid saved context was accepted')
            finally:
                scene[key] = saved
        obj = scene.objects[saved[0]]
        original = obj['split_turbine_context_classification']
        obj['split_turbine_context_classification'] = 'RECONSTRUCTED'
        try:
            try:
                self.context.ensure(scene, **group)
            except ValueError:
                pass
            else:
                raise AssertionError('Context misclassified as reconstructed was accepted')
        finally:
            obj['split_turbine_context_classification'] = original
        self.context.ensure(scene, **group)

    def visibility(self, scene, groups):
        """Source turbine selection must not hide either comparison's supports."""
        objects = [scene.objects[name] for group in groups for name in scene[group['names_key']]]
        previous = {obj.name: obj.hide_get() for obj in objects}
        independent = {obj.name: bool(index % 2) for index, obj in enumerate(objects)}

        def check():
            for obj in objects:
                assert not obj.hide_viewport and not obj.hide_render, obj.name
                assert obj.hide_get() == independent[obj.name], (obj.name, 'display hide_set changed')

        try:
            for obj in objects:
                obj.hide_set(independent[obj.name])
            for turbine in ('T2', 'T3', 'T1', 'all'):
                assert bpy.ops.wfrl.farm_flex_view(turbine=turbine, angle='DOWN') == {'FINISHED'}
                hidden = turbine in {'T2', 'T3'}
                assert scene.objects[SOURCE + 'Tower'].hide_get() == hidden
                assert scene.objects[SOURCE + 'Tower'].hide_render == hidden
                check()
            # Older revisions saved the source selector's flags on both display
            # groups. Test both explicit ensure and cached playback repair while
            # the source remains hidden, preserving independent display hides.
            bpy.ops.wfrl.farm_flex_view(turbine='T2', angle='DOWN')
            for obj in objects:
                obj.hide_viewport = obj.hide_render = True
            for group in groups:
                self.context.ensure(scene, **group)
            check()
            for obj in objects:
                obj.hide_viewport = obj.hide_render = True
            self.context.sync(scene)
            check()
        finally:
            for obj in objects:
                obj.hide_set(previous[obj.name])
            bpy.ops.wfrl.farm_flex_view(turbine='T1', angle='FRONT')
        print('SPLIT_CONTEXT_SOURCE_SELECTION_VISIBILITY_PASS', flush=True)

    def lifecycle(self, canonical):
        """Exercise public MAPPO rebuild and an actual failed texture import."""
        bpy.ops.wm.open_mainfile(filepath=str(canonical), load_ui=False, use_scripts=False)
        scene = self.restore()
        core = importlib.import_module(self.module + '.split_reconstruction')
        core.ensure_geometry_structure(scene)
        right = right_signature(scene)
        geometry_ids = {name: scene.objects[name].as_pointer()
                        for name in scene[GEOMETRY['names_key']]}
        # This replaces WFRL_Scene and its YawRoot while the reconstruction
        # display objects remain in their independent collection.
        self.addon.load_demo_scene(camera_rig=False)
        assert self.farm.is_active(scene), scene.get('wfrl_clearance_status')
        for frame in (52, 601, 52):
            scene.frame_set(frame)
            self.farm.active_for(scene).update(scene)
            self.context.sync(scene)
            bpy.context.view_layer.update()
            context_snapshot(scene, GEOMETRY)
        assert {name: scene.objects[name].as_pointer() for name in geometry_ids} == geometry_ids
        assert right_signature(scene) == right
        assert not scene.get(self.same.MARKER)
        original_create_camera = self.same._create_camera

        def fail_after_context_creation(*_args, **_kwargs):
            raise ValueError('injected camera failure after shared context creation')

        self.same._create_camera = fail_after_context_creation
        try:
            try:
                self.same.ensure(scene)
            except ValueError as exc:
                assert 'injected camera failure' in str(exc), str(exc)
            else:
                raise AssertionError('Texture failure fixture was not reached')
        finally:
            self.same._create_camera = original_create_camera
        assert not any(obj.name.startswith(self.same.PREFIX) for obj in scene.objects)
        assert not scene.get(self.same.MARKER) and TEXTURE['names_key'] not in scene
        # Native handlers log exceptions rather than necessarily raising them;
        # call the real source update directly so stale object references fail.
        self.farm.active_for(scene).update(scene)
        self.context.sync(scene)
        scene.frame_set(55)
        bpy.context.view_layer.update()
        context_snapshot(scene, GEOMETRY)
        assert {name: scene.objects[name].as_pointer() for name in geometry_ids} == geometry_ids
        assert right_signature(scene) == right
        # A new comparison can be created after the left pane selected T2.
        # Object.copy must not retain the hidden source's display/render flags.
        assert bpy.ops.wfrl.farm_flex_view(turbine='T2', angle='DOWN') == {'FINISHED'}
        assert scene.objects[SOURCE + 'Tower'].hide_get()
        self.same.ensure(scene)
        for name in scene[TEXTURE['names_key']]:
            obj = scene.objects[name]
            assert not obj.hide_viewport and not obj.hide_render and not obj.hide_get(), name
        scene.frame_set(52)
        self.farm.active_for(scene).update(scene)
        bpy.context.view_layer.update()
        context_snapshot(scene, GEOMETRY)
        context_snapshot(scene, TEXTURE)
        assert {name: value for name, value in right_signature(scene).items()
                if name.startswith('SplitRecon.')} == right
        return dict(ordinary_mappo_rebuild_rebound_sources=True,
                    existing_geometry_objects_and_blades_preserved=True,
                    texture_failure_after_context_creation_cleaned=True,
                    playback_continues_after_failed_import=True,
                    texture_retry_succeeded=True,
                    hidden_source_texture_creation_visible=True)

    def reopen(self, scene, output, label, groups, right):
        saved = output / (label + '.blend')
        assert not saved.exists(), ('refusing to overwrite', saved)
        scene.frame_set(52)
        # Reproduce a file saved by the old visibility coupling. Reopening must
        # repair the global flags without changing the right mode's hide_set.
        display_hides = {}
        for group in groups:
            for index, name in enumerate(scene[group['names_key']]):
                obj = scene.objects[name]
                display_hides[name] = bool(index % 2)
                obj.hide_set(display_hides[name])
                obj.hide_viewport = obj.hide_render = True
        bpy.ops.wm.save_as_mainfile(filepath=str(saved))
        self.context.clear_runtime()
        self.same.clear_runtime()
        bpy.ops.wm.open_mainfile(filepath=str(saved), load_ui=False, use_scripts=False)
        scene = self.restore()
        if TEXTURE in groups:
            self.same.ensure(scene)
        for group in groups:
            self.context.ensure(scene, **group)
        for name, hidden in display_hides.items():
            obj = scene.objects[name]
            assert not obj.hide_viewport and not obj.hide_render, name
            assert obj.hide_get() == hidden, (name, 'saved display hide_set changed')
        assert scene.frame_current == 52
        assert right_signature(scene) == right
        # attach replaces the saved source tower mesh. Both displays must follow
        # the replacement during the actual frame handler, without an ensure.
        old = scene.objects[SOURCE + 'Tower'].data
        self.farm.attach(scene, self.farm.saved_package(scene))
        scene.frame_set(52)
        bpy.context.view_layer.update()
        assert scene.objects[SOURCE + 'Tower'].data is not old
        snapshots = {group['prefix']: context_snapshot(scene, group) for group in groups}
        assert right_signature(scene) == right
        return scene, dict(path=str(saved), tower_mesh_rebound=True,
                           saved_visibility_flags_repaired=True, groups=snapshots)


def main():
    output = Path(os.environ['WFRL_TEST_OUTPUT']).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = dict(status='RUNNING', scope='NATIVE_BACKGROUND_SHARED_MODEL_CONTEXT',
                  visible_window_evidence=False, reconstruction_accuracy_accepted=False)
    try:
        assert bpy.app.background
        validation = Validation()
        canonical = validation.root / 'assets/examples/mappo_reconstruction_split.blend'
        canonical_before = digest(canonical.read_bytes())
        assert canonical_before == os.environ.get('WFRL_EXPECTED_GEOMETRY_SHA256', GEOMETRY_SHA256)
        opened = Path(os.environ.get('WFRL_SPLIT_INPUT', canonical)).expanduser().resolve()
        input_before = digest(opened.read_bytes())
        bpy.ops.wm.open_mainfile(filepath=str(opened), load_ui=False, use_scripts=False)
        legacy = {part: len(bpy.context.scene.objects[GEOMETRY['prefix'] + part].data.vertices)
                  for part in CORE[:3]}
        scene = validation.restore()
        scene.frame_set(52)
        original_right = right_signature(scene)
        assert len(original_right) == 3
        assert all(row['shape_keys'] == 601 and row['shape_action'] for row in original_right.values())
        assert bpy.ops.wfrl.farm_flex_view(turbine='T2', angle='DOWN') == {'FINISHED'}
        assert scene.objects[SOURCE + 'Tower'].hide_get()
        geometry = validation.ensure_unchanged(scene, GEOMETRY)
        assert all(not obj.hide_viewport and not obj.hide_render for obj in geometry)
        original_pointers = [obj.as_pointer() for obj in geometry]
        assert [obj.as_pointer() for obj in validation.ensure_unchanged(scene, GEOMETRY)] == original_pointers
        assert len([obj for obj in scene.objects if obj.name.startswith(GEOMETRY['prefix'])]) == len(geometry)
        report['geometry_migration'] = dict(legacy_vertices=legacy, components=len(geometry),
                                            detail=context_snapshot(scene, GEOMETRY))
        validation.rejects_damaged_saved(scene, GEOMETRY)
        scene, report['geometry_reopen'] = validation.reopen(scene, output, 'geometry-context',
                                                              [GEOMETRY], original_right)
        before_source = source_blade_signature(scene)
        report['legacy_texture_vertices'] = validation.legacy_texture_fixture(scene)
        validation.same.ensure(scene)
        assert source_blade_signature(scene) == before_source
        assert {key: value for key, value in right_signature(scene).items()
                if key.startswith('SplitRecon.')} == original_right
        right = right_signature(scene)
        assert len(right) == 6 and all(row['shape_keys'] == 601 for row in right.values())
        textured = [row for name, row in right.items() if name.startswith('SplitMappoTexture.')]
        assert all(len(row['images']) == 1 and row['images'][0]['packed_sha256'] for row in textured)
        texture = validation.ensure_unchanged(scene, TEXTURE)
        pointers = [obj.as_pointer() for obj in texture]
        assert [obj.as_pointer() for obj in validation.ensure_unchanged(scene, TEXTURE)] == pointers
        assert len([obj for obj in scene.objects if obj.name.startswith(TEXTURE['prefix'])]) == len(texture)
        validation.rejects_damaged_saved(scene, TEXTURE)
        validation.visibility(scene, (GEOMETRY, TEXTURE))
        frames = []
        revisited = []
        for frame in (52, 1, 601, 1801, 3601, 52):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            snapshots = {group['prefix']: context_snapshot(scene, group) for group in (GEOMETRY, TEXTURE)}
            assert right_signature(scene) == right
            frames.append(dict(frame=frame, groups=snapshots))
            if frame == 52:
                revisited.append(snapshots)
        assert revisited[0] == revisited[1], 'Backwards seek did not reproduce context'
        assert frames[0]['groups'] != frames[2]['groups'], 'Context did not move with source'
        report['frames'] = frames
        report['stable_runtime'] = validation.stable_runtime(scene)
        scene, report['texture_reopen'] = validation.reopen(scene, output, 'texture-context',
                                                            [GEOMETRY, TEXTURE], right)
        report['lifecycle'] = validation.lifecycle(canonical)
        assert digest(canonical.read_bytes()) == canonical_before
        assert digest(opened.read_bytes()) == input_before
        report.update(status='PASS', addon_module=validation.module, addon_file=validation.addon.__file__,
                      opened_blend=str(opened), canonical_geometry_sha256=canonical_before,
                      geometry_components=len(geometry), texture_components=len(texture),
                      complete_blade_identity=right, source_blades_unchanged_by_ensure=True,
                      shape_keys_actions_uv_and_atlases_preserved=True,
                      excluded_source_blades_and_radar=True, saved_damage_rejected=True,
                      seek_reproducible=True, legacy_asset_bytes_preserved=True)
    except Exception:
        report.update(status='FAIL', error=traceback.format_exc())
        (output / 'shared-context-validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        raise
    (output / 'shared-context-validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print('SPLIT_TURBINE_CONTEXT_NATIVE_PASS', flush=True)


if __name__ == '__main__':
    main()
