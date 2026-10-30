"""Backend mode changes preserve animation, visibility and history in other scenes."""
import importlib
import json
import os
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module('wfrl_blender')
runtime = importlib.import_module('wfrl_blender.runtime')
charts = importlib.import_module('wfrl_blender.charts')
from wfrl_blender.state import FrontendState
from wfrl_blender.scene_builder import COLLECTION_NAME

OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
addon.register()
try:
    owner = bpy.context.scene
    managed = bpy.data.collections.new(COLLECTION_NAME)
    owner.collection.children.link(managed)
    turbine = bpy.data.objects.new('WFRL.Turbine.T1.Rotor', None)
    managed.objects.link(turbine)
    turbine.keyframe_insert(data_path='rotation_euler', frame=1)
    fixture = bpy.data.objects.new('WFRL.Fixture.T1.Lidar', None)
    managed.objects.link(fixture)
    other = bpy.data.scenes.new('Independent wind farm')
    copy = bpy.data.objects.new('WFRL.Turbine.T1.Rotor.001', None)
    other.collection.objects.link(copy)
    copy.keyframe_insert(data_path='rotation_euler', frame=1)
    action = copy.animation_data.action
    foreign_fixture = bpy.data.objects.new('WFRL.Fixture.Independent', None)
    other.collection.objects.link(foreign_fixture)
    foreign_wake = bpy.data.objects.new('WFRL.WakeProxy.Independent', None)
    other.collection.objects.link(foreign_wake)
    charts.history_for(owner).session = 'owner history'
    charts.history_for(other).session = 'other history'
    runtime._state = FrontendState(mode='demo', connection='LOCAL DEMO', run_status='READY', confirmed=True)
    runtime.select_mode('replay')
    assert turbine.animation_data is None
    assert fixture.hide_render
    assert copy.animation_data is not None and copy.animation_data.action == action
    assert not foreign_fixture.hide_render and not foreign_wake.hide_render
    assert charts.history_for(other).session == 'other history'
    assert charts.history_for(owner).session is None
    turbine.keyframe_insert(data_path='rotation_euler', frame=1)
    shared_action = turbine.animation_data.action
    other.collection.children.link(managed)
    try:
        runtime.select_mode('interactive_training')
    except ValueError as error:
        assert '场景' in str(error)
    else:
        raise AssertionError('A mode change accepted shared canonical resources')
    assert turbine.animation_data.action == shared_action
    assert copy.animation_data.action == action
    (OUT / 'result.json').write_text(json.dumps(dict(status='PASS', addon_file=addon.__file__,
        blender=bpy.app.version_string, independent_scene_preserved=True,
        shared_resources_rejected=True), indent=2) + '\n')
finally:
    addon.unregister()
print('MODE_SCENE_OWNERSHIP_PASS', flush=True)
