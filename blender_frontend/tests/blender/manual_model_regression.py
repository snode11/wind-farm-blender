"""Rigid display geometry, assembly ownership, rollback and persistence in native Blender."""
from array import array
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import traceback
from unittest.mock import patch

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ROOT = Path(__file__).resolve().parents[3]
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT/'blender_frontend'), str(ROOT)]
addon = importlib.import_module('wfrl_blender')
from wfrl_blender import manual_control as control, manual_down as down, manual_geometry as geometry
from wfrl_blender.mechanical_details import blade_root_fitting_mesh
from wfrl_blender.turbine_geometry import blade_mesh, MANUAL_DISPLAY_SAMPLING

OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
RESULT = dict(status='RUNNING', addon_file=addon.__file__, blender=bpy.app.version_string,
              source_sha256={name: hashlib.sha256((ROOT/'blender_frontend/wfrl_blender'/name).read_bytes()).hexdigest()
                             for name in ('manual_scene.py', 'manual_geometry.py', 'manual_control.py',
                                          'manual_down.py', 'manual_video_render.py', 'turbine_geometry.py',
                                          'mechanical_details.py', 'materials.py', 'scene_builder.py',
                                          'panels/manual_control.py')},
              scope='Source native background; geometric/display checks, no physical validation or FPS claim')


def meshes(scene):
    return {obj.data.as_pointer(): obj.data for obj in scene.objects if obj.type == 'MESH'}


def blades(scene):
    return [getattr(entry, f'blade{i}_object') for entry in scene.wfrl_manual_turbines for i in (1, 2, 3)]


def bindings(scene):
    return {obj.name: obj.data.as_pointer() for obj in scene.objects if obj.type == 'MESH'}


def snapshot(scene):
    return {obj.name: (obj.data.as_pointer(), geometry.mesh_fingerprint(obj.data),
                      tuple(mat.as_pointer() if mat else 0 for mat in obj.data.materials))
            for obj in scene.objects if obj.type == 'MESH'}


def metrics(scene):
    scene.view_layers[0].update()
    graph = scene.view_layers[0].depsgraph
    triangles = {}
    for obj in scene.objects:
        if obj.type != 'MESH':
            continue
        evaluated = obj.evaluated_get(graph)
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            triangles[obj.name] = len(mesh.loop_triangles)
        finally:
            evaluated.to_mesh_clear()
    return dict(meshes=len(meshes(scene)), triangles=sum(triangles.values()),
                turbine_triangles=sum(value for name, value in triangles.items() if '.Turbine.' in name),
                environment_triangles=sum(value for name, value in triangles.items() if '.Turbine.' not in name),
                blade_triangles=sum(triangles[obj.name] for obj in blades(scene)),
                blade_vertices=len(blades(scene)[0].data.vertices))


def reject(call):
    try:
        call()
    except (ValueError, RuntimeError):
        return
    raise AssertionError('Expected a protected mesh or injected failure to reject the operation')


def check_static_protection(scene, source):
    objects = [next(obj for obj in entry.yaw_object.children if obj.get('wfrl_nacelle_shell'))
               for entry in scene.wfrl_manual_turbines]
    canonical = objects[0].data
    assert all(obj.data == canonical for obj in objects)
    for obj in objects[1:]:
        obj.data = canonical.copy()
        obj.data['wfrl_manual_static_name'] = obj.data.name
    before, count = bindings(scene), len(bpy.data.meshes)
    assign, seen = geometry._bind_mesh, [0]
    def fail(obj, mesh):
        seen[0] += 1
        if seen[0] == 2:
            raise RuntimeError('Injected binding failure')
        assign(obj, mesh)
    with patch.object(geometry, '_bind_mesh', side_effect=fail):
        reject(lambda: geometry.share_static_meshes(scene))
    assert bindings(scene) == before and len(bpy.data.meshes) == count
    assert geometry.share_static_meshes(scene) == 2
    for kind in ('edited', 'fake_user', 'shape_key', 'weights', 'foreign_consumer', 'custom', 'renamed'):
        obj = objects[1]
        duplicate = canonical.copy()
        obj.data = duplicate
        duplicate['wfrl_manual_static_name'] = duplicate.name
        foreign = None
        if kind == 'edited':
            duplicate.vertices[0].co.x += .01
        elif kind == 'fake_user':
            duplicate.use_fake_user = True
        elif kind == 'shape_key':
            obj.shape_key_add(name='Basis')
        elif kind == 'weights':
            obj.vertex_groups.new(name='User weights').add([0], .7, 'REPLACE')
        elif kind == 'foreign_consumer':
            foreign = bpy.data.objects.new('Foreign static consumer', duplicate)
            source.collection.objects.link(foreign)
        elif kind == 'custom':
            del duplicate['wfrl_manual_generated_digest']
        else:
            duplicate.name = 'User independent nacelle'
        geometry.share_static_meshes(scene)
        assert obj.data == duplicate, kind
        obj.data = canonical
        obj.vertex_groups.clear()
        if foreign:
            assert foreign.data == duplicate
            bpy.data.objects.remove(foreign, do_unlink=True)
        duplicate.use_fake_user = False
        if duplicate.shape_keys:
            obj.data = duplicate
            obj.shape_key_clear()
            obj.data = canonical
        bpy.data.meshes.remove(duplicate)
    assert len({obj.data.as_pointer() for obj in objects}) == 1
    assert all(not obj.get('wfrl_manual_static_role') for obj in blades(scene))
    towers = [obj for obj in scene.objects if obj.name.endswith('.Tower')]
    assert len({obj.data.as_pointer() for obj in towers}) == 3
    RESULT['immutable_mesh_guards'] = True
    RESULT['static_binding_failure_rollback'] = True


def check_surface_errors():
    errors = {}
    for model in ('rootkink', 'nrel5mw'):
        high_vertices, high_faces = blade_mesh(model=model)
        high = BVHTree.FromPolygons(high_vertices, high_faces)
        errors[model] = {}
        for detail in ('OVERVIEW', 'BALANCED'):
            vertices, faces = blade_mesh(*MANUAL_DISPLAY_SAMPLING[detail], model=model)
            low = BVHTree.FromPolygons(vertices, faces)
            errors[model][detail] = dict(
                low_vertices_to_high_surface_max_m=max(high.find_nearest(Vector(p))[3] for p in vertices),
                high_vertices_to_low_surface_max_m=max(low.find_nearest(Vector(p))[3] for p in high_vertices))
    RESULT['sampled_vertex_surface_distances'] = errors


def check_surface_rebinding(scene, source):
    from wfrl_blender import manual_scene, materials
    shaft = next(obj for obj in scene.objects if obj.name.endswith('.MainShaft'))
    owner = scene['wfrl_manual_owner']
    old_mesh = shaft.data
    old_mesh.materials[0] = bpy.data.materials[owner + '.hub']
    foreign = bpy.data.objects.new('Foreign surface consumer', old_mesh)
    source.collection.objects.link(foreign)
    del scene['wfrl_manual_surface_roles_revision']
    before, foreign_before = snapshot(scene), snapshot(source)
    counts = len(bpy.data.meshes), len(bpy.data.materials)
    refresh = materials.refresh_turbine_surfaces
    def fail(objects, **options):
        refresh(list(objects)[:1], **options)
        raise RuntimeError('Injected material binding failure')
    with patch.object(materials, 'refresh_turbine_surfaces', side_effect=fail):
        reject(lambda: manual_scene._ensure_surface_roles(scene))
    assert snapshot(scene) == before and snapshot(source) == foreign_before
    assert (len(bpy.data.meshes), len(bpy.data.materials)) == counts
    manual_scene._ensure_surface_roles(scene)
    assert shaft.data != old_mesh and foreign.data == old_mesh
    assert old_mesh.materials[0].name == owner + '.hub'
    assert shaft.data.materials[0].name.endswith('.metal')
    assert shaft.data.materials[0]['wfrl_manual_owner'] == owner
    bpy.data.objects.remove(foreign, do_unlink=True)
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)
    RESULT['private_surface_rebinding_and_rollback'] = True


def check_unclassified_root_assembly(scene):
    """Accept a verified high-sampling rootkink blade with the standard NREL fittings."""
    old = {obj.data.as_pointer(): obj.data for obj in blades(scene)}
    for mesh in old.values():
        del mesh['wfrl_manual_generated_digest']
        mesh['wfrl_manual_geometry'] = 'rootkink:cant:5'
    for blade in blades(scene):
        for role in geometry._FITTINGS:
            obj = next(child for child in blade.parent.children if child.name.endswith('.' + role))
            old[obj.data.as_pointer()] = obj.data
            mesh = blade_root_fitting_mesh(role, 'nrel5mw')
            for material in obj.data.materials:
                mesh.materials.append(material)
            obj.data = mesh
            old[mesh.as_pointer()] = mesh
    geometry.set_detail(scene, 'OVERVIEW')
    assert scene['wfrl_manual_blade_model'] == 'rootkink'
    assert len(blades(scene)[0].data.vertices) == 4417
    for blade in blades(scene):
        for obj in blade.parent.children:
            if obj.type == 'MESH' and obj != blade:
                assert obj.data.get('wfrl_manual_fitting', '').startswith('rootkink:root-neck.v1:')
    for mesh in old.values():
        try:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        except ReferenceError:
            pass
    geometry.set_detail(scene, 'CLOSEUP')
    RESULT['verified_unclassified_rootkink_assembly'] = True


def check_assembly_sweep(scene):
    """Sample rigid contact and actual Down rays at explicit azimuth and pitch values."""
    saved = control.pose_snapshot(scene)
    entry = scene.wfrl_manual_turbines[0]
    camera = down.ensure_down_camera(scene, 'T1')
    fittings = [obj for obj in entry.blade1_object.parent.children if obj.type == 'MESH'
                and obj != entry.blade1_object]
    hub = next(obj for obj in entry.rotor_object.children if obj.name.endswith('.Hub'))
    shell = next(obj for obj in entry.yaw_object.children if obj.get('wfrl_nacelle_shell'))
    shaft = next(obj for obj in entry.yaw_object.children if obj.name.endswith('.MainShaft'))

    def tree(obj, graph):
        evaluated = obj.evaluated_get(graph)
        mesh = evaluated.to_mesh()
        try:
            return BVHTree.FromPolygons([evaluated.matrix_world @ v.co for v in mesh.vertices],
                                       [list(face.vertices) for face in mesh.polygons])
        finally:
            evaluated.to_mesh_clear()

    report = {}
    for model in ('nrel5mw', 'rootkink'):
        scene.wfrl_manual_blade_shape = model
        geometry.set_detail(scene, 'CLOSEUP')
        hits, contact, nacelle_contacts = {}, [], []
        for azimuth in range(0, 360, 15):
            entry.azimuth_deg = azimuth
            scene.view_layers[0].update()
            graph = scene.view_layers[0].depsgraph
            hub_tree, shaft_tree, shell_tree = tree(hub, graph), tree(shaft, graph), tree(shell, graph)
            fit_trees = [tree(obj, graph) for obj in fittings]
            contact.append(any(t.overlap(hub_tree) or t.overlap(shaft_tree) for t in fit_trees))
            nacelle_contacts.append(sum(bool(t.overlap(shell_tree)) for t in fit_trees))
            for pitch in (-30, 0, 30, 60, 90):
                entry.pitch_deg = pitch
                scene.view_layers[0].update()
                frame = camera.data.view_frame(scene=scene)
                origin = camera.matrix_world.translation
                for u in (.05, .275, .5, .725, .95):
                    for v in (.05, .275, .5, .725, .95):
                        top = frame[3].lerp(frame[0], u)
                        bottom = frame[2].lerp(frame[1], u)
                        direction = (camera.matrix_world @ bottom.lerp(top, v) - origin).normalized()
                        hit = scene.ray_cast(graph, origin, direction, distance=1000)
                        name = hit[4].name if hit[0] else 'sky'
                        kind = ('root_fitting' if any(name.endswith('.' + role) for role in geometry._FITTINGS)
                                else 'blade' if any(name.endswith('.Blade' + str(i)) for i in (1, 2, 3))
                                else 'nacelle' if name.endswith('.Nacelle') else 'other')
                        hits[kind] = hits.get(kind, 0) + 1
        report[model] = dict(azimuth_samples=24, pitch_samples=[-30, 0, 30, 60, 90],
                             down_rays=hits, root_fitting_hub_or_shaft_contact_azimuths=sum(contact),
                             root_fitting_nacelle_intersections_by_azimuth=nacelle_contacts)
        assert all(contact), (model, 'Root fittings lost hub/shaft contact')
        assert hits.get('blade', 0), (model, 'No source blade appeared in sampled Down rays')
    control._apply_poses(scene, saved)
    RESULT['assembly_and_camera_sweep'] = report


def run():
    assert bpy.app.background and bpy.context.scene.objects.get('Cube')
    assert Path(addon.__file__).resolve().parent == ROOT/'blender_frontend/wfrl_blender'
    addon.register()
    addon.load_demo_scene(camera_rig=False)
    source = bpy.context.scene
    source_before = snapshot(source)
    source_materials = {mat.as_pointer() for obj in source.objects if obj.type == 'MESH'
                        for mat in obj.data.materials if mat}
    assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
    scene = bpy.context.scene
    assert control.validate_scene(scene)[0]
    assert scene.wfrl_manual_display_detail == 'OVERVIEW'
    RESULT['default'] = metrics(scene)
    assert RESULT['default']['meshes'] < 80
    assert RESULT['default']['blade_vertices'] == 4417
    assert len({obj.data.as_pointer() for obj in blades(scene)}) == 1
    assert set(meshes(scene)).isdisjoint(meshes(source))
    assert source_materials.isdisjoint({mat.as_pointer() for obj in scene.objects if obj.type == 'MESH'
                                      for mat in obj.data.materials if mat})
    for obj in scene.objects:
        kind = ('metal' if any(obj.name.endswith(role) for role in ('MainShaft', 'RootFlange', 'YawBearing'))
                else 'rubber' if any(obj.name.endswith(role) for role in ('PitchSeal', 'RoofGasket')) else None)
        if kind:
            material = obj.data.materials[0]
            assert material.name == scene['wfrl_manual_owner'] + '.' + kind
            assert material['wfrl_manual_owner'] == scene['wfrl_manual_owner']
            metallic = material.node_tree.nodes.get('Principled BSDF').inputs['Metallic'].default_value
            assert abs(metallic - (.82 if kind == 'metal' else 0)) < 1e-5
    RESULT['independent_material_roles'] = True
    check_static_protection(scene, source)
    check_surface_rebinding(scene, source)
    poses = control.pose_snapshot(scene)
    RESULT['sampling'] = {}
    for model in ('nrel5mw', 'rootkink'):
        scene.wfrl_manual_blade_shape = model
        assert scene['wfrl_manual_blade_model'] == model
        RESULT['sampling'][model] = {}
        for detail in MANUAL_DISPLAY_SAMPLING:
            geometry.set_detail(scene, detail)
            RESULT['sampling'][model][detail] = metrics(scene)
            assert control.pose_snapshot(scene) == poses
            expected, _ = blade_mesh(*MANUAL_DISPLAY_SAMPLING[detail], model=model)
            coordinates = array('f', (value for point in expected for value in point))
            actual = array('f', [0]) * len(coordinates)
            blades(scene)[0].data.vertices.foreach_get('co', actual)
            assert actual == coordinates
        for role in ('RootFairing', 'RootFlange', 'PitchSeal'):
            obj = next(o for o in blades(scene)[0].parent.children if o.name.endswith('.' + role))
            reference = blade_root_fitting_mesh(role, model)
            try:
                assert geometry.mesh_fingerprint(obj.data) == geometry.mesh_fingerprint(reference)
            finally:
                bpy.data.meshes.remove(reference)
    check_surface_errors()
    check_unclassified_root_assembly(scene)
    check_assembly_sweep(scene)
    before, count = snapshot(scene), len(bpy.data.meshes)
    assign, seen = geometry._bind_mesh, [0]
    def fail(obj, mesh):
        seen[0] += 1
        if seen[0] == 4:
            raise RuntimeError('Injected display binding failure')
        assign(obj, mesh)
    with patch.object(geometry, '_bind_mesh', side_effect=fail):
        reject(lambda: geometry.ensure_model(scene, detail='OVERVIEW'))
    assert snapshot(scene) == before and len(bpy.data.meshes) == count
    RESULT['display_binding_failure_rollback'] = True
    seen[0] = 0
    with patch.object(geometry, '_bind_mesh', side_effect=fail):
        scene.wfrl_manual_blade_shape = 'nrel5mw'
    assert scene.wfrl_manual_blade_shape == scene['wfrl_manual_blade_model'] == 'rootkink'
    assert snapshot(scene) == before and len(bpy.data.meshes) == count
    assert scene.get('wfrl_manual_geometry_error')
    down.apply_blade_model(scene)
    scene.pop('wfrl_manual_geometry_error', None)
    RESULT['model_property_failure_rollback'] = True
    mesh = blades(scene)[0].data
    coordinate = mesh.vertices[0].co.copy()
    mesh.vertices[0].co.x += .01
    reject(lambda: geometry.ensure_model(scene, detail='OVERVIEW'))
    mesh.vertices[0].co = coordinate
    mesh.use_fake_user = True
    reject(lambda: geometry.ensure_model(scene, detail='OVERVIEW'))
    mesh.use_fake_user = False
    assert snapshot(scene) == before
    RESULT['edited_display_mesh_preserved'] = True
    geometry.set_detail(scene, 'OVERVIEW')
    original = snapshot(scene)
    clone = down._clone_scene(scene)
    try:
        camera = down.ensure_down_camera(clone, 'T2')
        assert clone.wfrl_manual_display_detail == 'CLOSEUP'
        assert len(blades(clone)[0].data.vertices) == 15361
        assert camera.parent == clone.wfrl_manual_turbines[1].yaw_object
        assert snapshot(scene) == original and scene.wfrl_manual_display_detail == 'OVERVIEW'
    finally:
        down._drop_scene(clone)
    RESULT['export_clone_high_detail_source_unchanged'] = True
    down.ensure_down_camera(scene, 'T1')
    down.ensure_down_camera(scene, 'T2')
    down.ensure_down_camera(scene, 'T3')
    assert scene.wfrl_manual_display_detail == 'CLOSEUP'
    assert len({o.data.as_pointer() for o in blades(scene)}) == 1
    cameras = {o.name: (tuple(o.location), tuple(o.rotation_euler), o.data.lens)
               for o in scene.objects if o.type == 'CAMERA'}
    t1 = scene.wfrl_manual_turbines[0]
    scene.wfrl_manual_trial.duration_s = .4
    control.confirm_trial(scene, now=0)
    control.trial_action(scene, 'PAUSE', now=.1)
    trial = scene.wfrl_manual_trial.configuration
    trial_time = scene.wfrl_manual_trial.elapsed_s
    held = control.pose_snapshot(scene, now=.1)
    geometry.set_detail(scene, 'OVERVIEW')
    geometry.set_detail(scene, 'CLOSEUP')
    assert scene.wfrl_manual_trial.configuration == trial
    assert scene.wfrl_manual_trial.elapsed_s == trial_time
    assert control.pose_snapshot(scene, now=.1) == held
    assert cameras == {o.name: (tuple(o.location), tuple(o.rotation_euler), o.data.lens)
                       for o in scene.objects if o.type == 'CAMERA'}
    RESULT['simultaneous_down_cameras_and_frozen_trial'] = True
    scene_name, source_name = scene.name, source.name
    geometry.set_detail(scene, 'BALANCED')
    saved_digest = {obj.name: geometry.mesh_fingerprint(obj.data) for obj in scene.objects if obj.type == 'MESH'}
    assert snapshot(source) == source_before
    path = OUT/'manual-model.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    scene, source = bpy.data.scenes[scene_name], bpy.data.scenes[source_name]
    assert control.validate_scene(scene)[0]
    assert scene.wfrl_manual_display_detail == scene['wfrl_manual_display_applied'] == 'BALANCED'
    assert saved_digest == {obj.name: geometry.mesh_fingerprint(obj.data) for obj in scene.objects if obj.type == 'MESH'}
    assert all(not e.running for e in scene.wfrl_manual_turbines)
    assert len(blades(scene)[0].data.vertices) == 8065
    control.trial_action(scene, 'END')
    geometry.set_detail(scene, 'CLOSEUP')
    RESULT['save_reopen'] = True
    bpy.context.preferences.edit.use_global_undo = True
    bpy.ops.ed.undo_push(message='Manual model state')
    assert bpy.ops.wm.context_set_enum('EXEC_DEFAULT', True,
        data_path='scene.wfrl_manual_display_detail', value='OVERVIEW') == {'FINISHED'}
    assert len(blades(bpy.context.scene)[0].data.vertices) == 4417
    assert bpy.ops.ed.undo() == {'FINISHED'}
    assert bpy.context.scene.wfrl_manual_display_detail == 'CLOSEUP'
    assert len(blades(bpy.context.scene)[0].data.vertices) == 15361
    assert bpy.ops.ed.redo() == {'FINISHED'}
    assert bpy.context.scene.wfrl_manual_display_detail == 'OVERVIEW'
    assert len(blades(bpy.context.scene)[0].data.vertices) == 4417
    RESULT['native_undo_redo'] = True
    RESULT['source_flexible_blades_preserved'] = True
    addon.unregister()
    RESULT['status'] = 'PASS'


try:
    run()
except BaseException:
    RESULT.update(status='FAIL', error=traceback.format_exc())
    raise
finally:
    (OUT/'checks.json').write_text(json.dumps(RESULT, ensure_ascii=False, indent=2))
    print('MANUAL_MODEL_REGRESSION_' + RESULT['status'] + ' ' + json.dumps(RESULT), flush=True)
