"""Saved RNA scene links, unique ownership recovery and explicit unlinking."""
import json
import os
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import manual_control, manual_scene_links as links

OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
assert bpy.app.background and '--factory-startup' in sys.argv
assert Path(addon.__file__).resolve().parent == ROOT / 'blender_frontend/wfrl_blender'

addon.register()
try:
    source = bpy.context.scene
    source.name = 'Saved source'
    assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
    manual = bpy.context.scene
    manual.name = 'Saved manual'
    manual.wfrl_manual_pitch_rate_deg_s = 0.0
    manual.wfrl_manual_turbines[0].yaw_deg = 17
    manual.wfrl_manual_turbines[0].pitch_deg = 13
    poses = manual_control.pose_snapshot(manual)
    owner = manual['wfrl_manual_owner']

    # RNA pointer serialization is distinct from an ID property with the same name.
    del bpy.types.Scene.wfrl_manual_scene
    bpy.types.Scene.wfrl_manual_scene = bpy.props.PointerProperty(type=bpy.types.Scene)
    source.wfrl_manual_scene = manual
    source.pop(links.OWNER_KEY, None)
    saved = OUT / 'rna-linked-manual.blend'
    assert bpy.ops.wm.save_as_mainfile(filepath=str(saved)) == {'FINISHED'}
    del bpy.types.Scene.wfrl_manual_scene
    bpy.types.Scene.wfrl_manual_scene = property(links.resolve, links.bind)
    assert bpy.ops.wm.open_mainfile(filepath=str(saved)) == {'FINISHED'}

    source = bpy.data.scenes['Saved source']
    manual = bpy.data.scenes['Saved manual']
    assert source.wfrl_manual_scene == manual
    assert source[links.OWNER_KEY] == owner and 'wfrl_manual_scene' not in source
    assert manual.wfrl_manual_return_scene == source
    assert manual_control.pose_snapshot(manual) == poses
    source.name = 'Renamed source'
    manual.name = 'Renamed manual'
    count = len(bpy.data.scenes)
    assert bpy.ops.wfrl.manual_leave() == {'FINISHED'} and bpy.context.scene == source
    assert bpy.ops.wfrl.manual_enter() == {'FINISHED'} and bpy.context.scene == manual
    assert len(bpy.data.scenes) == count and manual_control.pose_snapshot(manual) == poses

    duplicate_source = source.copy()
    assert duplicate_source.wfrl_manual_scene is None
    bpy.data.scenes.remove(duplicate_source)
    duplicate_manual = manual.copy()
    source.pop(links.OWNER_KEY)
    assert source.wfrl_manual_scene is None and links.OWNER_KEY not in source
    bpy.data.scenes.remove(duplicate_manual)
    assert source.wfrl_manual_scene == manual

    source.wfrl_manual_scene = None
    assert source.wfrl_manual_scene is None
    links.restore()
    assert source.wfrl_manual_scene is None
    detached = OUT / 'detached-manual.blend'
    assert bpy.ops.wm.save_as_mainfile(filepath=str(detached)) == {'FINISHED'}
    assert bpy.ops.wm.open_mainfile(filepath=str(detached)) == {'FINISHED'}
    source = bpy.data.scenes['Renamed source']
    manual = bpy.data.scenes['Renamed manual']
    assert source.wfrl_manual_scene is None
    source.wfrl_manual_scene = manual
    assert source.wfrl_manual_scene == manual
    assert manual_control.pose_snapshot(manual) == poses
    result = dict(status='PASS', addon_file=addon.__file__, blender=bpy.app.version_string,
                  rna_pointer_save_migration=True, renamed_reentry_preserves_identity_and_poses=True,
                  duplicate_source_not_adopted=True, ambiguous_manual_scenes_not_adopted=True,
                  explicit_unlink_survives_restore_and_reopen=True)
    (OUT / 'checks.json').write_text(json.dumps(result, indent=2) + '\n')
    print('MANUAL_SCENE_LINK_COMPATIBILITY_PASS', json.dumps(result), flush=True)
finally:
    addon.unregister()
