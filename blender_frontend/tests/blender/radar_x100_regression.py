"""Native X100 source-time, replay, ownership and camera isolation contracts."""
from pathlib import Path
import importlib
import json
import os
import sys
import numpy as np
import bpy

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected:
    assert Path(addon.__file__).resolve().parent == Path(expected).resolve()
if MODULE == 'wfrl_blender':
    addon.register()
else:
    assert MODULE in bpy.context.preferences.addons
    assert not any(name == 'wfrl.radar_x100' for name in sys.modules)
farm = importlib.import_module(MODULE + '.farm_flex')
radar = importlib.import_module(MODULE + '.radar_x100.runtime')
scene_api = importlib.import_module(MODULE + '.radar_x100.scene')
api = importlib.import_module(MODULE + '.radar_x100.core')
if MODULE != 'wfrl_blender':
    import hashlib
    build_info = json.loads((Path(addon.__file__).parent / 'build-info.json').read_text())
    assert all(hashlib.sha256((Path(api.schema.__file__).parent / name).read_bytes()).hexdigest() == expected
               for name, expected in build_info['radar_core_sha256'].items())
preview = importlib.import_module(MODULE + '.custom_camera_preview')
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
addon.load_demo_scene(farm.default_package())
scene = bpy.context.scene
scene.wfrl_x100_candidates = 256
session = radar.start(scene)
flex = farm.active_for(scene)
assert session.frame.source_kind == 'GEOMETRY_SIMULATION'
assert len(session.frame.points) > 0, session.frame.provenance
clock = (scene.frame_current, scene.frame_subframe, scene.get('wfrl_clearance_time_s'))
pose_before = np.array(scene.objects['WFRL.Turbine.T1.YawRoot'].matrix_world)
t = float(flex.times[0]) + 10.237
snapshot = session.sampler.sample(t)
assert (scene.frame_current, scene.frame_subframe, scene.get('wfrl_clearance_time_s')) == clock
assert np.array_equal(np.array(scene.objects['WFRL.Turbine.T1.YawRoot'].matrix_world), pose_before)
flex.update(scene, sim_time_s=t, record_telemetry=False)
bpy.context.view_layer.update()
actual_mount = np.array(scene.objects['WFRL.Turbine.T1.YawRoot'].matrix_world) @ session.sampler.mount_from_sensor
assert np.max(np.abs(snapshot.world_from_sensor - actual_mount)) < 2e-5
assert np.max(np.abs(snapshot.world_from_sensor - np.array(session.mount.matrix_world))) < 2e-5
for mount in ('WORLD', 'TOWER_BASE'):
    alternate = scene_api.SceneSampler(flex, mount=mount,
        mount_from_sensor=scene_api.installation_matrix((3,4,5),(17,23,41)))
    a, b = alternate.sample(t), alternate.sample(t + .1)
    assert np.array_equal(a.world_from_sensor, b.world_from_sensor)
    assert np.max(np.abs(a.vertices - snapshot.vertices)) == 0
checks = dict(readonly_time_sampling=True, same_time_mount_max_error=float(np.max(np.abs(snapshot.world_from_sensor - actual_mount))),
              simulation_points=len(session.frame.points), alternate_mounts=True)

depsgraph = bpy.context.evaluated_depsgraph_get()
before_hash = preview.simulation_state_hash(scene, depsgraph)
session.fov.data.vertices[1].co.x += 1
session.fov.data.update()
bpy.context.view_layer.update()
assert preview.simulation_state_hash(scene, bpy.context.evaluated_depsgraph_get()) == before_hash
session.mount.location.x += .1
bpy.context.view_layer.update()
assert preview.simulation_state_hash(scene, bpy.context.evaluated_depsgraph_get()) != before_hash
layer = scene.view_layers[0]
session.fov.hide_set(False, view_layer=layer)
housing = next(obj for obj in scene.objects if obj.get('wfrl_x100_role') == 'physical')
try:
    with preview.without_annotations(scene, layer):
        assert session.fov.hide_get(view_layer=layer)
        assert not housing.hide_get(view_layer=layer)
        raise RuntimeError('intentional restoration probe')
except RuntimeError:
    pass
assert not session.fov.hide_get(view_layer=layer)
checks['raw_camera_hash_and_visibility'] = True

record_path = OUT / 'recording'
sink = api.recording.Recorder(record_path)
for sequence in range(7):
    frame = api.simulation.simulate(session.sampler.sample, float(flex.times[0]) + sequence / 30,
        sensor_id='X100-T1', session_id='native-contract', sequence=sequence, interval_s=session.sampler.interval,
        candidate_budget=128, visibility=scene_api.bvh_visibility)
    sink.append(frame, sequence / 30)
sink.close()
replay = api.recording.Replay(record_path)
assert replay.at(.2).sequence == 6 and replay.at(0).sequence == 0
radar.stop(scene)
scene.wfrl_x100_source, scene.wfrl_x100_path = 'REPLAY', str(record_path)
session = radar.start(scene)
scene.wfrl_x100_replay_s = session.replay.duration_s
assert bpy.ops.wfrl.x100_play() == {'FINISHED'}
assert session.playing and scene.wfrl_x100_replay_s == session.replay.times[0]
assert bpy.ops.wfrl.x100_play() == {'FINISHED'}
assert not session.playing
assert bpy.ops.wfrl.x100_play() == {'FINISHED'}
scene.wfrl_x100_replay_s = .05
assert not session.playing, 'Explicit seek pauses the independent replay clock'
assert bpy.ops.wfrl.x100_step(direction=1) == {'FINISHED'}
assert not session.playing and session.frame.sequence == 2
checks['native_file_play_pause_restart_and_seek_operators'] = True
for t, seq in [(0,0),(.1,3),(.2,6),(.05,1),(0,0),(.2,6)]:
    scene.wfrl_x100_replay_s = t
    session.update()
    assert session.frame.sequence == seq
    assert session.frame.source_kind == 'GEOMETRY_SIMULATION'
other = bpy.data.scenes.new('X100.Other')
assert radar.session_for(other) is None
clock = (scene.frame_current, scene.frame_subframe, scene.get('wfrl_clearance_time_s'))
radar.tick()
assert (scene.frame_current, scene.frame_subframe, scene.get('wfrl_clearance_time_s')) == clock
checks['replay_forward_backward_end_origin_and_ownership'] = True
radar.stop(scene)
scene.wfrl_x100_source = 'SIMULATION'
session = radar.start(scene)
owned = session.owner
shared = bpy.data.scenes.new('X100.SharedResources')
shared['wfrl_x100_owner'] = session.owner
owned_collection = next(collection for collection in scene.collection.children if collection.get('wfrl_x100_owner') == session.owner)
shared.collection.children.link(owned_collection)
view = importlib.import_module(MODULE + '.radar_x100.view')
view.remove_objects(shared)
assert session.mount.name in scene.objects and session.fov.name in scene.objects
assert session.mount.name not in shared.objects
bpy.data.scenes.remove(shared)
checks['linked_scene_cleanup_preserves_original_collection_objects'] = True
scene.frame_set(121, subframe=.25)
session.update()
saved = OUT / 'x100-saved.blend'
clock = (scene.frame_current, scene.frame_subframe, session.frame.sequence)
assert bpy.ops.wm.save_as_mainfile(filepath=str(saved)) == {'FINISHED'}
assert bpy.ops.wm.open_mainfile(filepath=str(saved)) == {'FINISHED'}
scene = bpy.context.scene
radar.tick()
restored = radar.session_for(scene)
assert restored is not None and scene.wfrl_x100_enabled, scene.get('wfrl_x100_error')
assert (scene.frame_current, scene.frame_subframe, restored.frame.sequence) == clock, (clock, scene.frame_current, scene.frame_subframe, restored.frame.sequence)
checks['save_reopen_restores_configuration_and_paused_sample'] = True
radar.stop(scene)
assert not any(obj.get('wfrl_x100_owner') == owned for obj in scene.objects)
checks['resource_cleanup'] = True
deleted = bpy.data.scenes.new('X100.DeletedOwner')
deleted.wfrl_x100_source, deleted.wfrl_x100_path = 'REPLAY', str(record_path)
deleted_session = radar.start(deleted)
view = importlib.import_module(MODULE + '.radar_x100.view')
view.install(deleted, deleted_session.owner, np.eye(4))
deleted_key, deleted_owner = deleted.as_pointer(), deleted_session.owner
bpy.data.scenes.remove(deleted)
radar.tick()
assert deleted_key not in radar._SESSIONS
assert not any(obj.get('wfrl_x100_owner') == deleted_owner for obj in bpy.data.objects)
checks['deleted_owner_no_reassignment_or_orphan_objects'] = True
checks.update(addon_path=addon.__file__, core_path=api.schema.__file__, status='PASS')
(OUT / 'result.json').write_text(json.dumps(checks, indent=2))
addon.unregister()
print('X100_NATIVE_PASS', json.dumps(checks))
