"""Native World ownership, deleted RNA cleanup and GW184 projection contracts."""
import importlib
import json
import os
from pathlib import Path
import sys

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT), str(ROOT / 'projects/gw184-single')]
import wfrl_blender as addon
from wfrl_blender import atmosphere, scene_builder, tip_tracking, clearance_replay, playback
from gw184 import model, scene as gw_scene, preflight

OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
assert bpy.app.background
checks = {}
addon.register()
source = bpy.context.scene
world = source.world
world.use_nodes = True
original = tuple(world.node_tree.nodes.get('Background').inputs['Color'].default_value)
shared = bpy.data.scenes.new('Shared World preset target')
shared.world = world
atmosphere.apply_preset(shared, 'dusk')
assert shared.world != world and source.world == world
assert tuple(world.node_tree.nodes.get('Background').inputs['Color'].default_value) == original
private = shared.world
atmosphere.apply_preset(shared, 'overcast')
assert shared.world == private
checks['preset_copies_actual_shared_world_once'] = True
built = bpy.data.scenes.new('Shared World build target')
built.world = world
bpy.context.window.scene = built
scene_builder._configure_environment(built.collection)
assert built.world != world and source.world == world
assert tuple(world.node_tree.nodes.get('Background').inputs['Color'].default_value) == original
checks['builder_preserves_other_scene_world_nodes'] = True

g, m, c = model.read_config()
projections = []
for width in (3840, 1920, 320):
    rig = gw_scene.build(g, m, c, width=width, apply_legacy_defects=False)
    result = preflight.projection_checks(rig)
    metadata = gw_scene.camera_metadata(rig)
    assert metadata['resolution'] == [width, round(width * 9/16)]
    projections.append(dict(resolution=metadata['resolution'], result=result))
checks['gw184_projection_3840_1920_320'] = projections

trails = []
for i in range(3):
    scene = bpy.data.scenes.new('Trail owner ' + str(i))
    yaw = bpy.data.objects.new('WFRL.Turbine.T1.YawRoot', None)
    # TipTrail uses scene-local lookup; remove naming collisions from other scenes.
    yaw.name = 'WFRL.Turbine.T1.YawRoot' if i == 0 else 'Trail local yaw'
    scene.collection.objects.link(yaw)
    original_collection = tip_tracking._collection_for
    tip_tracking._collection_for = lambda owner, tid: owner.collection
    try:
        trail = tip_tracking.TipTrail(scene)
    finally:
        tip_tracking._collection_for = original_collection
    tip_tracking._ACTIVE[scene.as_pointer()] = trail
    trails.append(trail)
bpy.data.objects.remove(trails[0].objects[1], do_unlink=True)
bpy.data.curves.remove(trails[1].objects[2].data, do_unlink=True)
bpy.context.window.scene = source
bpy.data.scenes.remove(trails[2].scene)
clearance_replay.unregister()
assert not tip_tracking._ACTIVE and not clearance_replay._READERS
assert tip_tracking.on_load_pre not in bpy.app.handlers.load_pre
assert tip_tracking.update not in bpy.app.handlers.frame_change_post
assert all(callback not in getattr(bpy.app.handlers, name) for name, callback in clearance_replay._HANDLERS)
clearance_replay.unregister()
checks['deleted_scene_object_curve_and_repeated_clearance_unload'] = True

# Genuine errors still propagate after the remaining extension cleanup executes.
class BrokenTrail:
    def disable(self, reason): raise RuntimeError('injected real cleanup error')
tip_tracking._ACTIVE[123] = BrokenTrail()
try:
    addon.unregister()
except RuntimeError as exc:
    assert 'injected real cleanup error' in str(exc)
else:
    raise AssertionError('A genuine cleanup error was hidden')
assert not tip_tracking._ACTIVE
assert not hasattr(bpy.types.Scene, 'wfrl_diagnostics_sampling')
assert not hasattr(bpy.types.Scene, 'wfrl_farm_panel_page')
assert not any(cls.__name__ == 'WFRLSettings' for cls in bpy.types.PropertyGroup.__subclasses__() if getattr(cls, 'is_registered', False))
addon.unregister()
checks['real_error_propagates_after_following_cleanup_and_retry'] = True

# The persistent load handler drops references before native file replacement.
tip_tracking.register()
scene = bpy.data.scenes.new('Load trail owner')
bpy.context.window.scene = scene
collection_for = tip_tracking._collection_for
tip_tracking._collection_for = lambda scene, tid: scene.collection
try:
    trail = tip_tracking.TipTrail(scene)
finally:
    tip_tracking._collection_for = collection_for
tip_tracking._ACTIVE[scene.as_pointer()] = trail
path = OUT / 'load-protection.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
assert not tip_tracking._ACTIVE
assert bpy.app.handlers.load_pre.count(tip_tracking.on_load_pre) == 1
tip_tracking.unregister(); tip_tracking.unregister()
checks['native_file_load_drops_trail_references'] = True
result = dict(status='PASS', module_file=addon.__file__, blender=bpy.app.version_string, checks=checks)
(OUT / 'result.json').write_text(json.dumps(result, indent=2))
print('RESOURCE_LIFECYCLE_CONTRACT_PASS', json.dumps(result), flush=True)
