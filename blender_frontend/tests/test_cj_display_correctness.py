"""Wake, tip-trail and clearance replay failure paths with owned bpy doubles."""
import math
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wfrl_blender import clearance_replay as replay, tip_tracking as tracking, wake
from test_tip_tracking import trails  # noqa: F401
from test_wake_payload import channel


class Slots(list):
    def append(self, material):
        super().append(material)
        material.users += 1

    def clear(self):
        for material in self:
            material.users -= 1
        super().clear()


class Sockets(dict):
    def __missing__(self, key):
        self[key] = value = NS(default_value=None)
        return value


class Nodes(list):
    def __init__(self, fail):
        super().__init__()
        self.fail = fail

    def new(self, kind):
        self.fail('shader')
        node = NS(type=kind, inputs=Sockets(), outputs=Sockets())
        self.append(node)
        return node


class Block(dict):
    def __init__(self, name):
        super().__init__()
        self.name, self.users = name, 0
        self.library, self.use_fake_user = None, False

    __hash__ = object.__hash__
    __eq__ = object.__eq__

    def as_pointer(self):
        return id(self)


class Data(Block):
    def __init__(self, name, fail):
        super().__init__(name)
        self.materials = Slots()
        self.splines, self.vertices, self.polygons = [], [], []
        self.fail = fail

    def from_pydata(self, vertices, edges, faces):
        self.fail('geometry')
        self.vertices = [NS(co=co) for co in vertices]
        self.polygons = list(faces)

    def update(self):
        self.fail('mesh_update')

    def clear_geometry(self):
        self.vertices.clear()
        self.polygons.clear()


class Object(Block):
    def __init__(self, name, data):
        super().__init__(name)
        self.data = data
        if data is not None:
            data.users += 1
        self.users_collection, self.users_scene = [], []
        self.hidden = self.hide_render = False

    def hide_get(self):
        return self.hidden

    def hide_set(self, hidden):
        self.hidden = hidden


class Store(list):
    def __init__(self, kind, fail):
        super().__init__()
        self.kind, self.fail = kind, fail

    def get(self, name):
        return next((block for block in self if block.name == name), None)

    def new(self, name, data=None):
        self.fail(self.kind)
        if self.get(name) is not None:
            name += '.' + str(len(self))
        if self.kind == 'objects':
            block = Object(name, data)
        elif self.kind == 'materials':
            block = Block(name)
            block.node_tree = NS(nodes=Nodes(self.fail), links=NS(new=lambda *args: None))
        else:
            block = Data(name, self.fail)
        self.append(block)
        return block

    def remove(self, block, *, do_unlink=False):
        if self.kind == 'objects':
            if do_unlink:
                for collection in list(block.users_collection):
                    collection.objects.unlink(block)
            if block.data is not None:
                block.data.users -= 1
        elif self.kind in ('curves', 'meshes'):
            block.materials.clear()
        assert block.users == 0, 'Must not delete a retained/shared datablock'
        super().remove(block)


class Links(list):
    def __init__(self, scene, collection, fail):
        super().__init__()
        self.scene, self.collection, self.fail = scene, collection, fail

    def get(self, name):
        return next((obj for obj in self if obj.name == name), None)

    def link(self, obj):
        self.fail('link')
        self.append(obj)
        obj.users += 1
        obj.users_collection.append(self.collection)
        obj.users_scene.append(self.scene)
        self.scene.objects[obj.name] = obj

    def unlink(self, obj):
        self.remove(obj)
        obj.users -= 1
        obj.users_collection.remove(self.collection)
        obj.users_scene.remove(self.scene)
        self.scene.objects.pop(obj.name)


class ObjectMap(dict):
    def __iter__(self):
        return iter(self.values())


class Scene(dict):
    __eq__ = object.__eq__
    __ne__ = object.__ne__

    def __init__(self):
        super().__init__()
        self.objects = ObjectMap()
        self.render = NS(fps=40, fps_base=1.)
        self.frame_current, self.frame_subframe = 3, .25
        self.frame_start, self.frame_end = 1, 401

    def as_pointer(self):
        return id(self)


@pytest.fixture
def display(monkeypatch):
    failure = NS(stage=None, calls=0, on=1)

    def fail(stage):
        if stage == failure.stage:
            failure.calls += 1
            if failure.calls == failure.on:
                raise RuntimeError('injected ' + stage)

    scene = Scene()
    collection = NS(name='WFRL_Scene', children_recursive=[])
    collection.objects = Links(scene, collection, fail)
    scene.collection = collection
    data = NS(**{kind: Store(kind, fail) for kind in ('objects', 'meshes', 'curves', 'materials')})
    data.scenes = [scene]
    bpy = NS(data=data, context=NS(scene=scene))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    from wfrl_blender import materials
    material = data.materials.new('Existing wake material')
    monkeypatch.setattr(materials, 'get_material', lambda name: material)
    monkeypatch.setattr(tracking, '_collection_for', lambda *_: collection)
    return NS(scene=scene, bpy=bpy, data=data, failure=failure, collection=collection)


def disxy_frame():
    return wake.validate_wake_payload({'encoding': 'json', 'turbine_ids': ['T1'],
        'data': channel({'kind': 'disxy', 'shape': [2, 2], 'values': [1, 2, 3, 4],
                         'x': [0, 1], 'y': [2, 3], 'z': 90.})})


@pytest.mark.parametrize('stage', ['geometry', 'mesh_update', 'objects', 'link'])
def test_disxy_creation_failure_releases_only_new_blocks(display, stage):
    kept = display.data.meshes.new('User mesh')
    display.failure.stage = stage
    with pytest.raises(RuntimeError, match='injected'):
        wake.apply_disxy_frame(disxy_frame(), scene=display.scene)
    assert list(display.data.meshes) == [kept]
    assert not display.data.objects and not display.scene.objects
    assert [m.name for m in display.data.materials] == ['Existing wake material']


@pytest.mark.parametrize('shared', ['object', 'mesh'])
def test_disxy_shared_geometry_is_rejected_before_writing(display, shared):
    obj = wake.apply_disxy_frame(disxy_frame(), scene=display.scene)
    before = [vertex.co for vertex in obj.data.vertices]
    if shared == 'object':
        other = Scene()
        other.objects[obj.name] = obj
        obj.users_scene.append(other)
        display.data.scenes.append(other)
    else:
        display.data.objects.new('User mesh instance', obj.data)
    from dataclasses import replace
    with pytest.raises(wake.WakePayloadError, match='shared'):
        wake.apply_disxy_frame(replace(disxy_frame(), x=(8., 9.)), scene=display.scene)
    assert [vertex.co for vertex in obj.data.vertices] == before


@pytest.mark.parametrize('stage,on', [('shader', 1), ('objects', 1), ('link', 1), ('link', 2)])
def test_tip_creation_failure_releases_all_new_blocks(display, stage, on):
    kept = display.data.curves.new('User curve', 'CURVE')
    display.failure.stage, display.failure.on = stage, on
    with pytest.raises(RuntimeError, match='injected'):
        tracking.TipTrail(display.scene)
    assert list(display.data.curves) == [kept]
    assert not display.data.objects and not display.scene.objects
    assert [m.name for m in display.data.materials] == ['Existing wake material']
    assert not display.scene.get('wfrl_tip_trail_enabled', False)


@pytest.mark.parametrize('shared', ['object', 'curve'])
def test_tip_shared_curve_is_rejected_without_rebinding_materials(display, shared):
    obj = tracking._ensure_curve(display.scene, 'T1', 1)
    before = list(obj.data.materials)
    if shared == 'object':
        other = Scene()
        other.objects[obj.name] = obj
        obj.users_scene.append(other)
        display.data.scenes.append(other)
    else:
        display.data.objects.new('User trail instance', obj.data)
    with pytest.raises(ValueError, match='shared'):
        tracking._ensure_curve(display.scene, 'T1', 1)
    assert list(obj.data.materials) == before


def test_tip_missing_rotor_clears_gap_instead_of_connecting_samples(trails):
    scene, trail, tick = trails()
    tick(0.)
    tick(.05)
    rotor = scene.objects.pop('WFRL.Turbine.T1.Rotor')
    scene.time, scene.frame_current = .1, 3
    trail.update(scene)
    assert all(not points for points in trail.points.values())
    assert all(not obj.data.splines for obj in trail.objects.values())
    assert scene['wfrl_tip_trail_clear_reason'] == 'rotor_unavailable'
    scene.objects['WFRL.Turbine.T1.Rotor'] = rotor
    tick(.15)
    assert all(len(points) == 1 for points in trail.points.values())


@pytest.mark.parametrize('angle', [math.nan, math.inf, -math.inf])
def test_tip_nonfinite_rotor_clears_capture_and_recovers(trails, angle):
    scene, trail, tick = trails()
    tick(0.)
    tick(.05)
    scene.objects['WFRL.Turbine.T1.Rotor'].rotation_euler.x = angle
    scene.time, scene.frame_current = .1, 3
    trail.update(scene)
    assert all(not points for points in trail.points.values())
    assert scene['wfrl_tip_trail_clear_reason'] == 'rotor_unavailable'
    tick(.15, .2)
    assert math.isfinite(trail.turn_angle)
    assert all(len(points) == 1 for points in trail.points.values())


@pytest.mark.parametrize('time_s', [math.nan, math.inf, -math.inf])
def test_tip_nonfinite_time_retracts_geometry(trails, time_s):
    scene, trail, tick = trails()
    tick(0.)
    tick(.05)
    scene.time, scene.frame_current = time_s, 3
    trail.update(scene)
    assert scene['wfrl_tip_trail_clear_reason'] == 'time_unavailable'
    assert all(not obj.data.splines for obj in trail.objects.values())


@pytest.fixture
def replay_clock(monkeypatch):
    scene = Scene()
    scene['wfrl_scene_kind'] = 'clearance_replay'
    scene['wfrl_clearance_timebase_fps'] = 40.
    reader = NS(start_s=10., end_s=20., at=Mock(side_effect=lambda t: {'time_s': t}))
    monkeypatch.setattr(replay, '_READERS', {scene.as_pointer(): reader})
    return scene, reader


@pytest.mark.parametrize('timebase', [0., -40., math.nan, math.inf, None, True])
def test_replay_invalid_timebase_cannot_produce_an_endpoint_sample(replay_clock, timebase):
    scene, reader = replay_clock
    scene['wfrl_clearance_timebase_fps'] = timebase
    with pytest.raises(ValueError, match='timebase'):
        replay.sample(scene)
    reader.at.assert_not_called()


@pytest.mark.parametrize('subframe', [math.nan, math.inf, -math.inf])
def test_replay_nonfinite_frame_cannot_produce_an_endpoint_sample(replay_clock, subframe):
    scene, reader = replay_clock
    scene.frame_subframe = subframe
    with pytest.raises(ValueError, match='time'):
        replay.sample(scene)
    reader.at.assert_not_called()


def test_saved_timebase_does_not_evaluate_unused_render_fps(replay_clock):
    scene, reader = replay_clock
    scene.render.fps_base = 0.
    assert replay.sample(scene)['time_s'] == 10.05625
    assert tracking._simulation_time(scene) == 10.05625


def test_trail_legacy_replay_uses_same_clock_fallback_as_cards(replay_clock):
    scene, reader = replay_clock
    del scene['wfrl_clearance_timebase_fps']
    assert tracking._simulation_time(scene) == replay.sample(scene)['time_s']


@pytest.mark.parametrize('timebase', [0., -40., math.nan, math.inf, None, True])
def test_preview_trail_invalid_timebase_is_not_a_zero_sample(replay_clock, timebase):
    scene, reader = replay_clock
    scene['wfrl_scene_kind'] = 'preview'
    scene['wfrl_tip_trail_timebase_fps'] = timebase
    with pytest.raises(ValueError, match='timebase'):
        tracking._simulation_time(scene)


def test_replay_clear_finishes_state_cleanup_after_beam_error(replay_clock, monkeypatch):
    from wfrl_blender import clearance_visual
    scene, reader = replay_clock
    scene['wfrl_clearance_status'] = 'READY'
    monkeypatch.setattr(clearance_visual, 'reset_beams', Mock(side_effect=RuntimeError('beam reset failed')))
    reset = Mock()
    monkeypatch.setattr(tracking, 'reset', reset)
    with pytest.raises(RuntimeError, match='beam reset failed'):
        replay.clear(scene, 'data unavailable')
    assert replay.reader_for(scene) is None
    assert scene['wfrl_clearance_status'] == 'data unavailable'
    assert scene.wfrl_clearance_show_config
    reset.assert_called_once_with(scene, 'replay_clear')


def test_load_pre_releases_reader_and_queued_scene_before_cache_cleanup(replay_clock, monkeypatch):
    from wfrl_blender import farm_flex
    scene, reader = replay_clock
    monkeypatch.setattr(replay, '_END_REQUEST', (scene, 2, reader))
    monkeypatch.setattr(farm_flex, 'clear_package_cache', Mock(side_effect=RuntimeError('cache cleanup failed')))
    with pytest.raises(RuntimeError, match='cache cleanup failed'):
        replay.on_load_pre(None)
    assert not replay._READERS and replay._END_REQUEST is None


def test_disxy_material_failure_does_not_leave_new_orphans(display, monkeypatch):
    from wfrl_blender import materials, runtime
    monkeypatch.setattr(runtime, 'get_state', lambda: NS(error=''))

    def material_failure(name):
        display.data.materials.new('Unfinished shader')
        raise RuntimeError('shader unavailable')

    monkeypatch.setattr(materials, 'get_material', material_failure)
    obj = wake.apply_disxy_frame(disxy_frame(), scene=display.scene)
    assert obj in display.data.objects and not obj.data.materials
    assert [m.name for m in display.data.materials] == ['Existing wake material']


def test_disxy_visibility_failure_releases_new_object_and_mesh(display, monkeypatch):
    monkeypatch.setattr(wake, 'set_disxy_validity', Mock(side_effect=RuntimeError('visibility failed')))
    with pytest.raises(RuntimeError, match='visibility failed'):
        wake.apply_disxy_frame(disxy_frame(), scene=display.scene)
    assert not display.data.meshes and not display.data.objects
    assert not display.scene.objects
    assert [m.name for m in display.data.materials] == ['Existing wake material']


def test_tip_visibility_failure_releases_new_blocks_and_disabled_state(display, monkeypatch):
    monkeypatch.setattr(tracking.TipTrail, '_set_visible', Mock(side_effect=RuntimeError('visibility failed')))
    with pytest.raises(RuntimeError, match='visibility failed'):
        tracking.TipTrail(display.scene)
    assert not display.data.curves and not display.data.objects
    assert not display.scene.get('wfrl_tip_trail_enabled', False)
    assert [m.name for m in display.data.materials] == ['Existing wake material']


def test_tip_name_collision_reuses_scene_owned_curve_on_rebuild(display):
    foreign = display.data.objects.new('WFRL.Turbine.T1.TipTrail.B1', None)
    first = tracking.TipTrail(display.scene)
    first.disable()
    second = tracking.TipTrail(display.scene, max_points=64)
    assert second.objects == first.objects
    assert len(display.data.curves) == 3 and len(display.data.objects) == 4
    assert foreign.data is None and not foreign


def test_tip_missing_curve_drops_invalid_runtime_reference(trails, monkeypatch):
    scene, trail, tick = trails()
    tick(0.)
    tick(.05)

    class DeletedCurve:
        def hide_set(self, value):
            raise ReferenceError('Object has been removed')

        @property
        def data(self):
            raise ReferenceError('Curve has been removed')

    trail.objects[2] = DeletedCurve()
    monkeypatch.setattr(tracking, '_ACTIVE', {id(scene): trail})
    scene.time, scene.frame_current = .1, 3
    tracking.update(scene)
    assert tracking.active(scene) is None
    assert not trail.enabled and not scene['wfrl_tip_trail_enabled']
    assert scene['wfrl_tip_trail_clear_reason'] == 'scene_data_unavailable'


def test_tip_invalid_scene_reference_cannot_match_reused_pointer(monkeypatch):
    scene = Scene()

    class DeletedScene:
        def __eq__(self, other):
            raise ReferenceError('Scene has been removed')

    monkeypatch.setattr(tracking, '_ACTIVE', {scene.as_pointer(): NS(scene=DeletedScene())})
    assert tracking.active(scene) is None
    assert not tracking._ACTIVE


def test_replay_bad_clock_retracts_ready_state_in_frame_handler(replay_clock, monkeypatch):
    from wfrl_blender import clearance_visual
    scene, reader = replay_clock
    scene['wfrl_clearance_status'] = 'READY'
    scene['wfrl_clearance_timebase_fps'] = math.nan
    monkeypatch.setattr(clearance_visual, 'reset_beams', Mock())
    monkeypatch.setattr(tracking, 'reset', Mock())
    with pytest.raises(ValueError, match='timebase'):
        replay.update(scene)
    assert replay.reader_for(scene) is None
    assert 'timebase' in scene['wfrl_clearance_status']
    assert scene.wfrl_clearance_show_config


def test_restore_runtime_error_clears_failed_scene_and_continues(monkeypatch):
    from wfrl_blender import clearance_visual
    scenes = [Scene(), Scene()]
    for scene in scenes:
        scene['wfrl_scene_kind'] = 'clearance_replay'
        scene.frame_set = Mock()
    monkeypatch.setitem(sys.modules, 'bpy', NS(data=NS(scenes=scenes), path=NS(abspath=str)))
    monkeypatch.setattr(replay, '_READERS', {})
    monkeypatch.setattr(clearance_visual, 'reset_beams', Mock())
    monkeypatch.setattr(tracking, 'reset', Mock())

    def load(scene, *args, **kwargs):
        scene['wfrl_clearance_status'] = 'READY'
        replay._READERS[scene.as_pointer()] = object()
        if scene is scenes[0]:
            raise RuntimeError('restored geometry unavailable')

    monkeypatch.setattr(replay, 'load', load)
    replay.on_load(None)
    assert replay.reader_for(scenes[0]) is None
    assert 'restored geometry unavailable' in scenes[0]['wfrl_clearance_status']
    assert replay.reader_for(scenes[1]) is not None
    scenes[1].frame_set.assert_called_once_with(3, subframe=.25)
