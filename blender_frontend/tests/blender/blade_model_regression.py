"""Native blade selection, recorded motion, camera preservation and persistence."""
from pathlib import Path
import hashlib
import importlib
import json
import os
import sys

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_TEST_ADDON_MODULE', 'wfrl_blender')
SOURCE_MODE = MODULE == 'wfrl_blender'
if SOURCE_MODE:
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
addon_dir = Path(addon.__file__).resolve().parent
expected_dir = os.environ.get('WFRL_EXPECTED_ADDON_DIR')
if expected_dir:
    assert addon_dir == Path(expected_dir).resolve(), (addon_dir, expected_dir)
if SOURCE_MODE:
    assert addon_dir == ROOT / 'blender_frontend/wfrl_blender'
else:
    assert not addon_dir.is_relative_to(ROOT / 'blender_frontend')
    assert MODULE in bpy.context.preferences.addons, 'Installed add-on must be enabled in this profile'
assert bpy.app.background, 'This regression requires Blender --background'
if SOURCE_MODE:
    assert not hasattr(bpy.types.Scene, 'wfrl_blade_model'), 'Source checks require --factory-startup'
else:
    assert hasattr(bpy.types.Scene, 'wfrl_blade_model'), 'Installed add-on is not registered'
output = Path(os.environ['WFRL_TEST_OUTPUT']).resolve()
output.mkdir(parents=True, exist_ok=False)
if SOURCE_MODE:
    addon.register()
cameras, clearance_replay, farm_flex = [importlib.import_module(MODULE + '.' + name)
    for name in ('cameras', 'clearance_replay', 'farm_flex')]
editor = importlib.import_module(MODULE + '.nrel_defects.editor')
gimbal = importlib.import_module(MODULE + '.panels.gimbal')

addon.load_demo_scene()
scene = bpy.context.scene


def camera_pose(camera):
    return dict(name=camera.name, data=camera.data.name,
                parent=camera.parent.name if camera.parent else None,
                location=list(camera.location), rotation=list(camera.rotation_euler),
                scale=list(camera.scale), parent_inverse=[list(row) for row in camera.matrix_parent_inverse],
                lens=camera.data.lens, sensor_width=camera.data.sensor_width,
                sensor_height=camera.data.sensor_height, sensor_fit=camera.data.sensor_fit,
                type=camera.data.type, shift_x=camera.data.shift_x, shift_y=camera.data.shift_y,
                clip_start=camera.data.clip_start, clip_end=camera.data.clip_end,
                yaw=camera.get('gimbal_yaw'), pitch=camera.get('gimbal_pitch'),
                roll=camera.get('gimbal_roll'), fov=camera.get('gimbal_fov'))


def camera_states():
    return {obj.name: camera_pose(obj) for obj in scene.objects if obj.type == 'CAMERA'}


def render_state():
    render = scene.render
    return {key: getattr(render, key) for key in (
        'fps', 'fps_base', 'resolution_x', 'resolution_y', 'resolution_percentage',
        'pixel_aspect_x', 'pixel_aspect_y', 'use_border', 'use_crop_to_border')}


def clock_state():
    return dict(frame=scene.frame_current, subframe=scene.frame_subframe,
                time_s=clearance_replay.sample(scene)['time_s'],
                turbine=scene['wfrl_clearance_turbine'])


def source_identity(active):
    arrays = {name: hashlib.sha256(np.ascontiguousarray(getattr(active, name)).tobytes()).hexdigest()
              for name in ('times', 'transforms', 'poses')}
    arrays['telemetry'] = hashlib.sha256(json.dumps(active.telemetry, sort_keys=True,
        default=lambda value: value.tolist() if isinstance(value, np.ndarray) else value).encode()).hexdigest()
    arrays['manifest'] = hashlib.sha256(json.dumps(active.manifest, sort_keys=True).encode()).hexdigest()
    return arrays


def rest_meshes(active):
    return {obj.name: rest.copy() for obj, rest, *_ in active.blades}


def mesh_topology(active):
    return {obj.name: tuple(tuple(p.vertices) for p in obj.data.polygons)
            for obj, *_ in active.blades}


def world_meshes(active, turbine=None):
    result = {}
    for obj, *_ in active.blades:
        if turbine is not None and not obj.name.startswith(f'WFRL.Turbine.{turbine}.'):
            continue
        vertices = np.empty(len(obj.data.vertices) * 3, np.float32)
        obj.data.vertices.foreach_get('co', vertices)
        matrix = np.array(obj.matrix_world)
        result[obj.name] = vertices.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
    return result


def assert_model(model):
    active = farm_flex.active_for(scene)
    assert active is not None and active.enabled
    assert scene.wfrl_blade_model == model, (scene.wfrl_blade_model, model)
    assert scene.get('wfrl_blade_model', 'nrel5mw') == model
    assert active.model == model, (active.model, model)
    assert len(active.blades) == 9
    assert len({obj.data.as_pointer() for obj, *_ in active.blades}) == 9
    return active


def assert_preserved(expected_clock, expected_cameras, expected_render, camera_name, visibility):
    active = farm_flex.active_for(scene)
    assert clock_state() == expected_clock, (clock_state(), expected_clock)
    assert camera_states() == expected_cameras, 'Model selection changed camera pose or optics'
    assert render_state() == expected_render, (render_state(), expected_render)
    assert scene.camera.name == camera_name
    assert active.visible_turbines == visibility, (active.visible_turbines, visibility)
    turbine = expected_clock['turbine']
    assert clearance_replay.reader_for(scene) is active.readers[turbine]


baseline = assert_model('nrel5mw')
source = source_identity(baseline)
baseline_rest = rest_meshes(baseline)
baseline_topology = mesh_topology(baseline)
for turbine in ('T1', 'T2', 'T3'):
    camera = cameras.ensure_gimbal(scene, turbine)
    cameras.down_gimbal(camera)
    assert [camera[key] for key in ('gimbal_yaw', 'gimbal_pitch', 'gimbal_roll', 'gimbal_fov')] == [180, -90, 180, 110]
    cameras.ensure_nacelle_gimbal(scene, turbine)

assert bpy.ops.wfrl.farm_flex_view(turbine='T2', angle='DOWN') == {'FINISHED'}
gimbal.change(scene.camera, dx=4, dy=-2, zoom=-3)
scene.camera.data.shift_y += .013
scene.render.fps = 48
scene.render.fps_base = 1.001
scene.render.resolution_percentage = 75
scene.frame_set(902, subframe=.25)
saved_clock = clock_state()
saved_cameras = camera_states()
saved_render = render_state()
saved_camera = scene.camera.name
visibility = set(baseline.visible_turbines)
assert visibility == {1}
baseline_world = world_meshes(baseline, 'T2')
baseline_measurement = clearance_replay.sample(scene)

saved_loader = farm_flex._load_package
saved_reader = clearance_replay.reader_for(scene)


def unavailable_package(*args, **kwargs):
    raise OSError('Recorded result package unavailable')


try:
    farm_flex._load_package = unavailable_package
    scene.wfrl_blade_model = 'rootkink'
finally:
    farm_flex._load_package = saved_loader
assert assert_model('nrel5mw') is baseline and baseline.enabled
assert scene.get('wfrl_blade_model') == 'nrel5mw'
assert_preserved(saved_clock, saved_cameras, saved_render, saved_camera, visibility)
assert clearance_replay.reader_for(scene) is saved_reader
assert scene.get('wfrl_blade_model_error'), 'Unavailable source requires visible feedback'

scene.wfrl_blade_model = 'rootkink'
alternate = assert_model('rootkink')
assert not scene.get('wfrl_blade_model_error'), scene.get('wfrl_blade_model_error')
assert_preserved(saved_clock, saved_cameras, saved_render, saved_camera, visibility)
assert source_identity(alternate) == source
assert clearance_replay.sample(scene) == baseline_measurement
assert mesh_topology(alternate) == baseline_topology
alternate_rest = rest_meshes(alternate)
changed_vertices = {}
for name, vertices in alternate_rest.items():
    assert vertices.shape == baseline_rest[name].shape
    changed = np.linalg.norm(vertices - baseline_rest[name], axis=1) > 1e-5
    changed_vertices[name] = int(changed.sum())
    assert changed_vertices[name] > 1000, name
    assert np.isfinite(vertices).all()

try:
    editor.EditorSession(scene)
except ValueError as exc:
    assert 'NREL' in str(exc), str(exc)
else:
    raise AssertionError('NREL defect editor accepted alternate display geometry')

split_mappo_texture, split_reconstruction = [importlib.import_module(MODULE + '.' + name)
    for name in ('split_mappo_texture', 'split_reconstruction')]
for enter_reconstruction in (
        lambda: split_mappo_texture._validate_source(scene, {}, check_files=False),
        lambda: split_reconstruction.enter(bpy.context)):
    try:
        enter_reconstruction()
    except ValueError as exc:
        assert 'NREL' in str(exc), str(exc)
    else:
        raise AssertionError('NREL reconstruction accepted alternate display geometry')

scene.wfrl_blade_model = 'nrel5mw'
restored = assert_model('nrel5mw')
assert_preserved(saved_clock, saved_cameras, saved_render, saved_camera, visibility)
assert source_identity(restored) == source
assert mesh_topology(restored) == baseline_topology
for name, vertices in rest_meshes(restored).items():
    np.testing.assert_array_equal(vertices, baseline_rest[name], err_msg=name)
roundtrip_error = 0.0
for name, vertices in world_meshes(restored, 'T2').items():
    error = float(np.max(np.abs(vertices - baseline_world[name])))
    roundtrip_error = max(roundtrip_error, error)
    assert error < 1e-4, (name, error)


def mesh_counts():
    return dict(total=len(bpy.data.meshes), unused=sum(mesh.users == 0 for mesh in bpy.data.meshes))


load_calls = []


def counted_loader(path):
    load_calls.append(str(path))
    return saved_loader(path)


switch_mesh_counts = [mesh_counts()]
try:
    farm_flex._load_package = counted_loader
    for index in range(20):
        chosen = 'rootkink' if index % 2 == 0 else 'nrel5mw'
        previous_calls = len(load_calls)
        scene.wfrl_blade_model = chosen
        active = assert_model(chosen)
        assert len(load_calls) == previous_calls + 1, load_calls
        assert not scene.get('wfrl_blade_model_error')
        assert_preserved(saved_clock, saved_cameras, saved_render, saved_camera, visibility)
        assert source_identity(active) == source
        switch_mesh_counts.append(mesh_counts())
        assert switch_mesh_counts[-1] == switch_mesh_counts[0], switch_mesh_counts
finally:
    farm_flex._load_package = saved_loader

# Rebinding must leave external mesh users and deliberate retained assets intact.
protected_scene = bpy.data.scenes.new('Blade model resource protection')
shared = []
for suffix in ('Blade1', 'Tower'):
    mesh = scene.objects['WFRL.Turbine.T1.' + suffix].data
    obj = bpy.data.objects.new('External shared ' + suffix, mesh)
    protected_scene.collection.objects.link(obj)
    shared.append((obj, mesh, np.asarray([vertex.co[:] for vertex in mesh.vertices])))
fake_mesh = scene.objects['WFRL.Turbine.T1.Blade2'].data
fake_mesh.use_fake_user = True
shape_obj = scene.objects['WFRL.Turbine.T1.Blade3']
shape_obj.shape_key_add(name='Basis')
shape_mesh = shape_obj.data
unrelated_mesh = bpy.data.meshes.new('User unused mesh')
unrelated_mesh.from_pydata([(1, 2, 3)], [], [])
scene.wfrl_blade_model = 'rootkink'
assert_model('rootkink')
for obj, mesh, coordinates in shared:
    assert obj.data == mesh and mesh.users > 0
    np.testing.assert_array_equal([vertex.co[:] for vertex in mesh.vertices], coordinates)
assert fake_mesh.use_fake_user and fake_mesh.users == 1
assert shape_mesh.shape_keys is not None
assert unrelated_mesh.users == 0 and list(unrelated_mesh.vertices[0].co) == [1, 2, 3]
for obj, mesh, _coordinates in shared:
    bpy.data.objects.remove(obj, do_unlink=True)
    assert mesh.users == 0
    bpy.data.meshes.remove(mesh)
bpy.data.scenes.remove(protected_scene)
fake_mesh.use_fake_user = False
for mesh in (fake_mesh, shape_mesh, unrelated_mesh):
    bpy.data.meshes.remove(mesh)
scene.wfrl_blade_model = 'nrel5mw'
assert_model('nrel5mw')

failure_mesh_counts = []
saved_replace = farm_flex._MeshBindings.replace
clearance_visual = importlib.import_module(MODULE + '.clearance_visual')
saved_radar = clearance_visual.ensure_radar
for failure_kind in ('partial_mesh_assignment', 'after_mesh_assignments'):
    before_counts = mesh_counts()
    before_meshes = {mesh.as_pointer() for mesh in bpy.data.meshes}
    previous_calls = len(load_calls)
    injected = []

    def failed_replace(bindings, obj, mesh):
        saved_replace(bindings, obj, mesh)
        if len(bindings.replaced) == 3 and not injected:
            injected.append(failure_kind)
            raise RuntimeError('Injected partial mesh binding failure')

    def failed_radar(*args, **kwargs):
        if not injected:
            injected.append(failure_kind)
            raise RuntimeError('Injected late model binding failure')
        return saved_radar(*args, **kwargs)

    try:
        farm_flex._load_package = counted_loader
        if failure_kind == 'partial_mesh_assignment':
            farm_flex._MeshBindings.replace = failed_replace
        else:
            clearance_visual.ensure_radar = failed_radar
        scene.wfrl_blade_model = 'rootkink'
    finally:
        farm_flex._MeshBindings.replace = saved_replace
        clearance_visual.ensure_radar = saved_radar
        farm_flex._load_package = saved_loader
    restored = assert_model('nrel5mw')
    assert injected == [failure_kind]
    assert len(load_calls) == previous_calls + 1, 'Failure restoration re-read the validated package'
    assert 'Injected' in scene.get('wfrl_blade_model_error', '')
    assert_preserved(saved_clock, saved_cameras, saved_render, saved_camera, visibility)
    assert source_identity(restored) == source
    assert mesh_counts() == before_counts, (failure_kind, before_counts, mesh_counts(),
        [(mesh.name, mesh.users, dict(mesh)) for mesh in bpy.data.meshes
         if mesh.as_pointer() not in before_meshes and mesh.users == 0])
    failure_mesh_counts.append(dict(case=failure_kind, before=before_counts, after=mesh_counts()))
    for name, vertices in rest_meshes(restored).items():
        np.testing.assert_array_equal(vertices, baseline_rest[name], err_msg=name)

scene['wfrl_nrel_defect_enabled'] = True
scene.wfrl_blade_model = 'rootkink'
assert_model('nrel5mw')
assert scene.get('wfrl_blade_model_error'), 'Blocked selection requires visible feedback'
scene['wfrl_nrel_defect_enabled'] = False
scene.wfrl_blade_model = 'rootkink'
alternate = assert_model('rootkink')
assert not scene.get('wfrl_blade_model_error'), scene.get('wfrl_blade_model_error')

assert bpy.ops.wfrl.farm_flex_view(turbine='all') == {'FINISHED'}
scene.frame_set(902, subframe=.25)
at_start = world_meshes(alternate)
scene.frame_set(1102, subframe=.5)
at_later = world_meshes(alternate)
motion = {}
for name, vertices in at_later.items():
    assert np.isfinite(vertices).all(), name
    motion[name] = float(np.max(np.linalg.norm(vertices - at_start[name], axis=1)))
    assert motion[name] > .01, (name, motion[name])
scene.frame_set(902, subframe=.25)
for name, vertices in world_meshes(alternate).items():
    np.testing.assert_allclose(vertices, at_start[name], atol=1e-5, rtol=0, err_msg=name)
assert source_identity(alternate) == source

assert bpy.ops.wfrl.farm_flex_view(turbine='T2', angle='DOWN') == {'FINISHED'}
gimbal.change(scene.camera, dx=4, dy=-2, zoom=-3)
scene.camera.data.shift_y += .013
scene.frame_set(902, subframe=.25)
saved_clock = clock_state()
saved_cameras = camera_states()
saved_camera = scene.camera.name
saved_render = render_state()
path = output / 'rootkink.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
scene = bpy.context.scene
reopened = assert_model('rootkink')
assert clock_state() == saved_clock, (clock_state(), saved_clock)
assert camera_states() == saved_cameras
assert scene.camera.name == saved_camera
assert clearance_replay.reader_for(scene) is reopened.readers['T2']
assert source_identity(reopened) == source
for name, vertices in rest_meshes(reopened).items():
    np.testing.assert_array_equal(vertices, alternate_rest[name], err_msg=name)

result = dict(status='PASS', addon_module=MODULE, addon_file=addon.__file__, blender=bpy.app.version_string,
              default_model='nrel5mw', alternate_model='rootkink', blades=9,
              changed_vertices=changed_vertices, source_identity=source,
              nrel_world_roundtrip_max_error_m=roundtrip_error,
              rootkink_motion_max_displacement_m=motion,
              model_switch_preserves_camera_time_reader_and_visibility=True,
              repeated_switch_count=20, package_reads_per_switch=1,
              repeated_switch_mesh_counts=switch_mesh_counts,
              binding_failure_mesh_counts=failure_mesh_counts,
              shared_fake_user_shape_key_and_unrelated_meshes_preserved=True,
              binding_failure_reuses_validated_package=True,
              unavailable_package_preserves_runtime=True, reconstruction_identity_guard=True,
              nrel_defect_identity_guard=True, save_reopen_model_camera_clock_reader=True,
              saved_scene=str(path), visible_window='NOT_RUN')
(output / 'checks.json').write_text(json.dumps(result, indent=2) + '\n')
print('BLADE_MODEL_REGRESSION_PASS', json.dumps(result), flush=True)
