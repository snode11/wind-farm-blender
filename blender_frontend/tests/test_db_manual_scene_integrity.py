"""Manual construction rollback and view restoration with Blender stand-ins."""
import json
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wfrl_blender import manual_scene


class Block:
    def __init__(self, name, **attributes):
        self.name = name
        self.tags = {}
        self.users = 0
        self.use_fake_user = False
        self.library = None
        self.shape_keys = None
        self.__dict__.update(attributes)

    def as_pointer(self):
        return id(self)

    def get(self, key, default=None):
        return self.tags.get(key, default)

    def __setitem__(self, key, value):
        self.tags[key] = value

    def __getitem__(self, key):
        return self.tags[key]


class Links(list):
    def __init__(self, owner, relation):
        super().__init__()
        self.owner, self.relation = owner, relation

    def link(self, block):
        self.append(block)
        block.users += 1
        if self.relation == 'objects':
            block.users_collection.append(self.owner)

    def __getitem__(self, key):
        if isinstance(key, str):
            return next(block for block in self if block.name == key)
        return super().__getitem__(key)


class Pool(list):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.fail_property = False

    def new(self, name, data=None):
        if self.kind == 'scenes':
            class Scene(Block):
                def __setitem__(scene, key, value):
                    if self.fail_property:
                        raise RuntimeError('scene property failed')
                    super().__setitem__(key, value)
            block = Scene(name, wfrl_manual_scene=None)
            block.collection = NS(children=Links(block, 'children'))
        elif self.kind == 'collections':
            block = Block(name)
            block.objects = Links(block, 'objects')
        else:
            block = Block(name, data=data, users_scene=[], users_collection=[])
            if data is not None:
                data.users += 1
        self.append(block)
        return block

    def remove(self, block, **kwargs):
        super().remove(block)
        if self.kind == 'scenes':
            for collection in block.collection.children:
                collection.users -= 1
        elif self.kind == 'collections':
            for obj in list(block.objects):
                obj.users -= 1
                obj.users_collection.remove(block)
        elif self.kind == 'objects':
            if block.data is not None:
                block.data.users -= 1
            for collection in list(block.users_collection):
                collection.objects.remove(block)
                obj_users = block.users - 1
                block.users = obj_users
            block.users_collection.clear()


@pytest.fixture
def construction(monkeypatch, stub_module):
    kinds = ('scenes', 'collections', 'objects', 'meshes', 'curves', 'lights',
             'cameras', 'materials', 'worlds', 'images')
    data = NS(**{kind: Pool(kind) for kind in kinds})
    source = data.scenes.new('Source')
    old_manual = data.scenes.new('Existing damaged manual scene')
    source.wfrl_manual_scene = old_manual
    window = NS(scene=source)

    class Context:
        @property
        def scene(self):
            return window.scene

    context = Context()
    context.window = window
    context.view_layer = NS(objects=NS(active=None), update=Mock())
    monkeypatch.setitem(sys.modules, 'bpy', NS(data=data))
    monkeypatch.setattr(manual_scene, 'entry_error', lambda context: '')
    monkeypatch.setattr(manual_scene.uuid, 'uuid4', lambda: NS(hex='db1234567890'))
    stub_module('wfrl_blender.materials', get_material=lambda *a, **k: None)
    stub_module('wfrl_blender.manual_control', apply_scene=lambda scene: True,
                sync_batch=lambda scene: None)
    builder = stub_module('wfrl_blender.scene_builder', _make_turbine=Mock(), _cone=Mock(), _finish=Mock())
    return NS(data=data, source=source, old_manual=old_manual, context=context, builder=builder)


def test_manual_failure_reclaims_unlinked_objects_and_preserves_existing_ids(construction):
    case = construction
    prefix = 'WFRLManual.db1234567890'
    existing_mesh = case.data.meshes.new(prefix + '.UserMesh')
    existing_object = case.data.objects.new('User object', existing_mesh)
    retained = case.data.materials.new('User material')

    def fail(collection, turbine, **kwargs):
        collection.objects.link(existing_object)
        mesh = case.data.meshes.new(prefix + '.Unlinked.Mesh')
        case.data.objects.new(prefix + '.Unlinked', mesh)
        case.data.materials.new(prefix + '.NewMaterial')
        raise RuntimeError('object link failed')

    case.builder._make_turbine = fail
    with pytest.raises(RuntimeError, match='object link failed'):
        manual_scene.create(case.context)
    assert case.context.scene is case.source
    assert case.source.wfrl_manual_scene is case.old_manual
    assert case.data.scenes == [case.source, case.old_manual]
    assert case.data.collections == []
    assert case.data.objects == [existing_object]
    assert case.data.meshes == [existing_mesh]
    assert case.data.materials == [retained]


def test_manual_failure_does_not_remove_new_objects_shared_with_another_scene(construction):
    case = construction
    created = []

    def fail(collection, turbine, **kwargs):
        mesh = case.data.meshes.new(kwargs['object_prefix'] + '.Shared.Mesh')
        obj = case.data.objects.new(kwargs['object_prefix'] + '.Shared', mesh)
        collection.objects.link(obj)
        obj.users_scene = [case.source, case.context.scene]
        created.extend((obj, mesh))
        raise RuntimeError('build failed')

    case.builder._make_turbine = fail
    with pytest.raises(RuntimeError, match='build failed'):
        manual_scene.create(case.context)
    assert case.data.objects == [created[0]]
    assert case.data.meshes == [created[1]]


def test_manual_scene_initialization_failure_removes_new_scene(construction):
    case = construction
    case.data.scenes.fail_property = True
    with pytest.raises(RuntimeError, match='scene property failed'):
        manual_scene.create(case.context)
    assert case.context.scene is case.source
    assert case.data.scenes == [case.source, case.old_manual]


def test_manual_cleanup_continues_when_one_removal_fails(construction):
    case = construction
    remove = case.data.meshes.remove

    def fail_remove(block, **kwargs):
        if block.name.endswith('Blocked'):
            raise RuntimeError('mesh removal failed')
        return remove(block, **kwargs)

    case.data.meshes.remove = fail_remove

    def fail(collection, turbine, **kwargs):
        case.data.meshes.new(kwargs['object_prefix'] + '.Blocked')
        case.data.meshes.new(kwargs['object_prefix'] + '.Free')
        case.data.materials.new(kwargs['object_prefix'] + '.Material')
        raise ValueError('construction failed')

    case.builder._make_turbine = fail
    with pytest.raises(ValueError, match='construction failed') as caught:
        manual_scene.create(case.context)
    assert len(case.data.meshes) == 1
    assert case.data.meshes[0].name.endswith('Blocked')
    assert not case.data.materials
    assert any('mesh removal failed' in note for note in caught.value.__notes__)


def test_manual_pose_failure_keeps_the_previous_source_scene_link(construction, monkeypatch, stub_module):
    case = construction
    original_new = case.data.scenes.new

    def new_scene(name):
        scene = original_new(name)
        scene.wfrl_manual_turbines = NS(add=lambda: NS())
        return scene

    def make_turbine(collection, turbine, **kwargs):
        for role in ('YawRoot', 'Rotor', 'Blade1', 'Blade2', 'Blade3'):
            collection.objects.link(case.data.objects.new(kwargs['object_prefix'] + '.' + role))

    monkeypatch.setattr(case.data.scenes, 'new', new_scene)
    monkeypatch.setattr(manual_scene, '_environment', lambda *args: None)
    case.builder._make_turbine = make_turbine
    stub_module('wfrl_blender.manual_control', apply_scene=lambda scene: False,
                validate_scene=lambda scene: (False, 'pose failed'))
    with pytest.raises(ValueError, match='pose failed'):
        manual_scene.create(case.context)
    assert case.source.wfrl_manual_scene is case.old_manual
    assert case.context.scene is case.source


@pytest.mark.parametrize('retention', ['user_name', 'shape_keys', 'animation_data', 'library', 'use_fake_user'])
def test_environment_replacement_preserves_user_ground_mesh(monkeypatch, stub_module, retention):
    prefix = 'WFRLManual.environment'

    class Meshes(Pool):
        def new(self, name, data=None):
            mesh = super().new(name, data)
            mesh.materials, mesh.polygons = [], []
            mesh.from_pydata = Mock()
            mesh.animation_data = None
            return mesh

    meshes = Meshes('meshes')
    original = meshes.new('User ground' if retention == 'user_name' else prefix + '.Ground.Mesh')
    if retention != 'user_name':
        setattr(original, retention, True if retention == 'use_fake_user' else object())

    class Ground(Block):
        def __init__(self):
            super().__init__(prefix + '.Ground')
            self.data = original

    ground = Ground()
    scene = NS(objects={ground.name: ground, prefix + '.FarGround': object()})
    data = NS(meshes=meshes, materials=Pool('materials'),
              worlds=NS(new=Mock(side_effect=RuntimeError('stop after ground'))))
    monkeypatch.setitem(sys.modules, 'bpy', NS(data=data))
    monkeypatch.setitem(sys.modules, 'mathutils', NS(Vector=Mock()))
    stub_module('wfrl_blender.scene_builder', _mesh_object=Mock(), _configure_render=Mock())
    stub_module('wfrl_blender.landscape', height=lambda x, y: 0., terrain_shader=lambda *a, **k: None)
    with pytest.raises(RuntimeError, match='stop after ground'):
        manual_scene._environment(scene, object(), prefix)
    assert original in meshes


@pytest.fixture
def view_context(monkeypatch):
    monkeypatch.setattr(manual_scene, '_VIEWS', {})
    monkeypatch.setattr(manual_scene, '_RESUME', {})
    region = NS(view_location=[1., 2., 3.], view_rotation=[1., 0., 0., 0.],
                view_distance=99., view_perspective='PERSP', view_camera_zoom=12.,
                view_camera_offset=(.1, .2))
    camera = Block('Source camera')
    class Objects(dict):
        def __iter__(self):
            return iter(self.values())
    space = NS(region_3d=region, camera=camera, use_local_camera=True, clip_start=.1, clip_end=500.,
               shading=NS(type='SOLID', color_type='MATERIAL', use_scene_world=False, use_scene_lights=False),
               overlay=NS(show_overlays=True), show_region_ui=False)
    area = NS(type='VIEW_3D', spaces=NS(active=space), tag_redraw=Mock())
    return NS(window=NS(as_pointer=lambda: 77), screen=NS(areas=[area]),
              scene=NS(objects=Objects({camera.name: camera}))), camera


def test_manual_view_snapshot_keeps_camera_identity_without_an_rna_reference(view_context):
    context, camera = view_context
    manual_scene._remember_view(context)
    assert manual_scene._VIEWS[77][0][2]['camera'] == camera.name
    saved = {}
    manual_scene._save_return_view(context, saved)
    assert json.loads(saved['wfrl_manual_return_views'])[0]['camera'] == camera.name
    replacement = Block(camera.name)
    context.scene.objects[camera.name] = replacement
    context.screen.areas[0].spaces.active.overlay.show_overlays = False
    manual_scene._restore_view(context, saved)
    assert context.screen.areas[0].spaces.active.camera is replacement
    assert context.screen.areas[0].spaces.active.overlay.show_overlays


def test_manual_view_restore_skips_malformed_records_and_restores_valid_ones(view_context):
    context, _camera = view_context
    manual_scene._remember_view(context)
    saved = {}
    manual_scene._save_return_view(context, saved)
    records = json.loads(saved['wfrl_manual_return_views'])
    saved['wfrl_manual_return_views'] = json.dumps([{'area_index': 0}, *records])
    manual_scene.clear_views()
    space = context.screen.areas[0].spaces.active
    space.overlay.show_overlays = False
    space.region_3d.view_distance = 1.
    manual_scene._restore_view(context, saved)
    assert space.overlay.show_overlays
    assert space.region_3d.view_distance == 99.


def test_manual_view_restore_retains_a_renamed_camera(view_context):
    context, camera = view_context
    manual_scene._remember_view(context)
    del context.scene.objects[camera.name]
    camera.name = 'Renamed camera'
    context.scene.objects[camera.name] = camera
    context.screen.areas[0].spaces.active.camera = None
    manual_scene._restore_view(context)
    assert context.screen.areas[0].spaces.active.camera is camera


@pytest.mark.parametrize('case', ['normal', 'undo', 'focus_failure'])
def test_manual_return_does_not_dereference_a_scene_removed_by_undo(monkeypatch, stub_module, case):
    import wfrl_blender

    class Source(Block):
        deleted = False

        def __eq__(self, other):
            if self.deleted:
                raise ReferenceError('Source RNA was removed by undo')
            return self is other

    source = Source('Source')
    manual = Block('Manual', wfrl_manual_return_scene=source)
    manual['wfrl_scene_kind'] = 'manual'
    source.wfrl_manual_scene = manual
    window = NS(scene=source, as_pointer=lambda: 33)

    class Context:
        @property
        def scene(self):
            return window.scene

    context = Context()
    context.window = window
    play = Mock()
    monkeypatch.setitem(sys.modules, 'bpy', NS(ops=NS(screen=NS(animation_play=play))))
    monkeypatch.setattr(manual_scene, '_VIEWS', {})
    monkeypatch.setattr(manual_scene, '_RESUME', {})
    monkeypatch.setattr(manual_scene, 'entry_error', lambda context: '')
    for name in ('_remember_view', '_save_return_view', '_restore_view', '_ensure_environment'):
        monkeypatch.setattr(manual_scene, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(manual_scene, 'focus', Mock(side_effect=RuntimeError('focus failed')
                                                   if case == 'focus_failure' else None))
    monkeypatch.setattr(wfrl_blender, '_cancel_playback', Mock(), raising=False)
    stub_module('wfrl_blender.manual_control', validate_scene=lambda scene: (True, ''), pause_scene=Mock())
    stub_module('wfrl_blender.playback', is_playing=lambda scene: True, any_playing=lambda: False)
    if case == 'focus_failure':
        with pytest.raises(RuntimeError, match='focus failed'):
            manual_scene.enter(context)
        assert context.scene is source
        assert not manual_scene._RESUME
        play.assert_called_once()
        return
    assert manual_scene.enter(context) is manual
    if case == 'undo':
        source.deleted = True
        manual.wfrl_manual_return_scene = Block('Restored source')
    assert manual_scene.leave(context) is manual.wfrl_manual_return_scene
    assert play.call_count == (1 if case == 'normal' else 0)
    assert not manual_scene._RESUME
