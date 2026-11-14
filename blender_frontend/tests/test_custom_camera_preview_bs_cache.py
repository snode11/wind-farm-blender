"""Geometry update delivery, unchanged drawing output and allocation budgets."""
from array import array
from collections import Counter
from contextlib import nullcontext
from copy import deepcopy
import sys
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import custom_camera_preview as preview


@pytest.fixture
def geometry(monkeypatch):
    handlers = NS(depsgraph_update_post=[], frame_change_post=[])
    monkeypatch.setitem(sys.modules, 'bpy', NS(app=NS(handlers=handlers)))
    counts = Counter()

    class Mesh:
        bl_rna = NS(identifier='Mesh')

        def __init__(self, pointer):
            self.pointer = pointer
            self.values = [0., 0., 0., 1., 0., 0.]
            self.vertices = self

        def as_pointer(self):
            return self.pointer

        def __len__(self):
            return len(self.values) // 3

        def foreach_get(self, attribute, output):
            assert attribute == 'co'
            counts['copies'] += 1
            output[:] = array('f', self.values)

    class Object:
        type = 'MESH'
        bl_rna = NS(identifier='Object')

        def __init__(self, name, pointer, mesh):
            self.name, self.pointer, self.data = name, pointer, mesh
            self.matrix_world = [[float(i == j) for j in range(4)] for i in range(4)]

        def get(self, _key):
            return None

        def as_pointer(self):
            return self.pointer

        def evaluated_get(self, _graph):
            return self

        def to_mesh(self):
            counts[self.name] += 1
            return self.data

        def to_mesh_clear(self):
            counts['clears'] += 1

    obj = Object('WFRL.Turbine.T1.Blade', 10, Mesh(20))
    scene = NS(as_pointer=lambda: 1, frame_current=1, frame_subframe=0., objects=[obj])
    graph = NS(as_pointer=lambda: 2, updates=[])
    cache = preview.GeometryCache()

    def notify(item, *, geometry=True, frame=False, owner=scene):
        graph.updates = [NS(id=NS(original=item), is_updated_geometry=geometry)]
        callbacks = handlers.frame_change_post if frame else handlers.depsgraph_update_post
        for callback in tuple(callbacks):
            callback(owner, graph)
        graph.updates = []

    yield NS(scene=scene, graph=graph, obj=obj, cache=cache, counts=counts,
             handlers=handlers, notify=notify, Object=Object, Mesh=Mesh)
    cache.free()


def test_static_mesh_five_frames_reads_once_with_two_checks_per_frame(geometry):
    g = geometry
    hashes = []
    for frame in range(1, 6):
        g.scene.frame_current = frame
        g.scene.frame_subframe = frame / 10
        g.notify(g.obj, geometry=False, frame=True)
        hashes.append(g.cache.hash(g.scene, g.graph, 'source'))
        assert g.cache.hash(g.scene, g.graph, 'source') == hashes[-1]
    print('static five-frame to_mesh:', g.counts[g.obj.name])
    assert len(set(hashes)) == 1
    assert g.counts[g.obj.name] == g.counts['copies'] == g.counts['clears'] == 1


@pytest.mark.parametrize('frame_handler', [False, True])
def test_flexible_blade_updates_every_frame_without_rereading_static_mesh(geometry, frame_handler):
    g = geometry
    tower = g.Object('WFRL.Turbine.T1.Tower', 30, g.Mesh(40))
    g.scene.objects.append(tower)
    hashes = []
    for frame in range(1, 6):
        g.scene.frame_current = frame
        g.obj.data.values[-1] = frame / 10
        g.notify(g.obj, frame=frame_handler)
        hashes.append(g.cache.hash(g.scene, g.graph, 'source'))
        assert g.cache.hash(g.scene, g.graph, 'source') == hashes[-1]
    print('flexible/static five-frame to_mesh:', g.counts[g.obj.name], g.counts[tower.name])
    assert len(set(hashes)) == 5
    assert g.counts[g.obj.name] == 5
    assert g.counts[tower.name] == 1


@pytest.mark.parametrize('update_kind', ['Object', 'Mesh', 'Key', 'modifier_parameter'])
def test_same_frame_geometry_and_parameter_updates_change_fingerprint(geometry, update_kind):
    g = geometry
    first = g.cache.hash(g.scene, g.graph, 'source')
    g.obj.data.values[0] = .25
    item = g.obj.data if update_kind == 'Mesh' else g.obj
    if update_kind == 'Key':
        item = NS(bl_rna=NS(identifier='Key'))
    g.notify(item)
    second = g.cache.hash(g.scene, g.graph, 'source')
    assert second != first and g.counts[g.obj.name] == 2
    assert second == preview.simulation_state_hash(g.scene, g.graph)


def test_transform_only_update_rehashes_without_copying_coordinates(geometry):
    g = geometry
    first = g.cache.hash(g.scene, g.graph, 'source')
    g.obj.matrix_world[0][3] = 2.
    g.notify(g.obj, geometry=False)
    assert g.cache.hash(g.scene, g.graph, 'source') != first
    assert g.counts[g.obj.name] == 1


@pytest.mark.parametrize('replacement', ['object', 'mesh', 'membership', 'reused_pointer'])
def test_replacement_or_membership_change_does_not_reuse_old_coordinates(geometry, replacement):
    g = geometry
    first = g.cache.hash(g.scene, g.graph, 'source')
    if replacement == 'object':
        g.obj = g.Object(g.obj.name, 99, g.obj.data)
        g.scene.objects[:] = [g.obj]
    elif replacement == 'mesh':
        g.obj.data = g.Mesh(99)
    elif replacement == 'reused_pointer':
        g.obj = g.Object(g.obj.name, g.obj.pointer, g.obj.data)
        g.obj.session_uid = 99
        g.scene.objects[:] = [g.obj]
    else:
        g.scene.objects.clear()
        assert g.cache.hash(g.scene, g.graph, 'source') != first
        assert not g.cache.meshes
        g.scene.objects.append(g.obj)
    g.obj.data.values[0] = .5
    assert g.cache.hash(g.scene, g.graph, 'source') != first
    assert g.counts[g.obj.name] == 2 and len(g.cache.meshes) == 1


@pytest.mark.parametrize('scope_change', ['source', 'scene', 'graph', 'load', 'depsgraph_handler', 'frame_handler'])
def test_scope_and_missing_update_handlers_force_fresh_geometry(geometry, monkeypatch, scope_change):
    g = geometry
    g.cache.hash(g.scene, g.graph, 'source')
    identity = 'source'
    if scope_change == 'source':
        identity = 'new source'
    elif scope_change == 'scene':
        g.scene.as_pointer = lambda: 11
    elif scope_change == 'graph':
        g.graph.as_pointer = lambda: 12
    elif scope_change == 'load':
        monkeypatch.setattr(preview, '_ALL_SCENES_REVISION', preview._ALL_SCENES_REVISION + 1)
    elif scope_change == 'depsgraph_handler':
        g.handlers.depsgraph_update_post.clear()
    else:
        g.handlers.frame_change_post.clear()
    g.cache.hash(g.scene, g.graph, identity)
    assert g.counts[g.obj.name] == 2
    assert len(g.handlers.depsgraph_update_post) == len(g.handlers.frame_change_post) == 1
    g.cache.free()
    g.cache.free()
    assert not g.handlers.depsgraph_update_post and not g.handlers.frame_change_post


def test_shared_mesh_invalidates_all_owners_but_other_scene_does_not(geometry):
    g = geometry
    other = g.Object('WFRL.Turbine.T2.Blade', 30, g.obj.data)
    g.scene.objects.append(other)
    first = g.cache.hash(g.scene, g.graph, 'source')
    g.notify(g.obj.data, owner=NS(as_pointer=lambda: 100))
    assert g.cache.hash(g.scene, g.graph, 'source') == first
    assert g.counts['copies'] == 2
    g.obj.data.values[0] = .75
    g.notify(g.obj.data)
    assert g.cache.hash(g.scene, g.graph, 'source') != first
    assert g.counts['copies'] == 4


def test_shape_key_update_invalidates_only_its_mesh(geometry):
    g = geometry
    shape_keys = NS(as_pointer=lambda: 50, bl_rna=NS(identifier='Key'))
    g.obj.data.shape_keys = shape_keys
    tower = g.Object('WFRL.Turbine.T1.Tower', 30, g.Mesh(40))
    g.scene.objects.append(tower)
    first = g.cache.hash(g.scene, g.graph, 'source')
    g.obj.data.values[0] = 1.
    g.notify(shape_keys, frame=True)
    assert g.cache.hash(g.scene, g.graph, 'source') != first
    assert g.counts[g.obj.name] == 2 and g.counts[tower.name] == 1


@pytest.mark.parametrize('leased', [False, True])
@pytest.mark.parametrize('mutation', [None, 'geometry', 'clock', 'source'])
def test_render_group_keeps_before_after_checks(geometry, monkeypatch, leased, mutation):
    g = geometry
    identity, images = ['source'], []
    g.scene.display_settings = NS(display_device='sRGB')
    context = NS(scene=g.scene, area=NS(type='VIEW_3D', regions=[NS(type='WINDOW')]),
                 view_layer=NS(update=lambda: None), space_data=NS(),
                 evaluated_depsgraph_get=lambda: g.graph)
    monkeypatch.setitem(sys.modules, preview.__package__ + '.panels.custom_cameras',
                        NS(session_identity=lambda _: identity[0]))
    monkeypatch.delitem(sys.modules, preview.__package__ + '.native_camera_views', raising=False)
    monkeypatch.setattr(preview, 'drawing_settings', lambda *_: nullcontext())
    monkeypatch.setattr(preview, 'without_annotations', lambda *_: nullcontext())
    monkeypatch.setattr(preview.core, 'parameters', lambda _: NS(output_long_edge_px=640))
    monkeypatch.setattr(preview, '_DRAWING', False)

    class Image:
        freed = False
        view = projection = None

        def __init__(self, *_args, **_kwargs):
            images.append(self)

        def snapshot(self, *_args, **_kwargs):
            assert preview._DRAWING
            if mutation == 'geometry':
                g.obj.data.values[0] = 1.
                g.notify(g.obj.data)
            elif mutation == 'clock':
                g.scene.frame_subframe += .25
            elif mutation == 'source':
                identity[0] = 'replacement'

        def free(self):
            self.freed = True

    monkeypatch.setattr(preview, 'CameraImage', Image)
    offscreen = NS(draw_view3d=lambda *_args, **_kwargs: None)
    pool = preview.PreviewTargets()
    pool.state_cache = g.cache
    pool.acquire = lambda *_: offscreen
    target = pool if leased else NS(get=lambda *_: offscreen)
    Image.width = Image.height = 640
    if mutation is None:
        result = preview.render_group(context, [{'wfrl_custom_slot': 1}], target=target,
                                      create_texture=leased)
        assert result[1] is images[0] and not images[0].freed
        assert g.counts[g.obj.name] == (1 if leased else 2)
    else:
        with pytest.raises(RuntimeError, match='本组无效'):
            preview.render_group(context, [{'wfrl_custom_slot': 1}], target=target,
                                 create_texture=leased)
        assert images[0].freed
        if mutation == 'geometry':
            assert g.counts[g.obj.name] == 2
    assert not preview._DRAWING


@pytest.fixture
def drawing(monkeypatch):
    counts, output = Counter(), []

    class Shader:
        def __init__(self, name):
            self.name, self.uniforms = name, {}
            counts['shader:' + name] += 1

        def bind(self):
            pass

        def uniform_float(self, name, value):
            self.uniforms[name] = value

        def uniform_sampler(self, name, value):
            self.uniforms[name] = value

    def batch_for_shader(shader, kind, attributes):
        counts['batch:' + shader.name] += 1
        attributes = deepcopy(attributes)
        return NS(draw=lambda shader: output.append((shader.name, kind, attributes, dict(shader.uniforms))))

    system = NS(ui_scale=1., use_region_overlap=False)
    region = NS(as_pointer=lambda: 3, type='WINDOW', x=0, y=0, width=1000, height=800)
    context = NS(window=NS(as_pointer=lambda: 1),
                 area=NS(as_pointer=lambda: 2, regions=[region]), region=region,
                 preferences=NS(system=system), scene=NS(),
                 window_manager=NS(wfrl_custom_engineering=False))
    monkeypatch.setitem(sys.modules, 'bpy', NS(context=context))
    monkeypatch.setitem(sys.modules, 'gpu', NS(shader=NS(from_builtin=Shader)))
    monkeypatch.setitem(sys.modules, 'gpu_extras.batch', NS(batch_for_shader=batch_for_shader))
    monkeypatch.setitem(sys.modules, 'blf', NS(size=lambda *_: None, color=lambda *_: None,
        position=lambda *_: None, draw=lambda *_: None, dimensions=lambda *_: (10., 10.)))
    monkeypatch.setattr(preview, 'playback_preview', lambda *_: False)
    monkeypatch.setattr(preview, '_DRAWING', False)
    renderer = preview.Preview()
    state = dict(time_s=0., frame=1, subframe=0., profile='MATERIAL')
    renderer.observe = lambda *_: None
    renderer.request = renderer.key = state
    renderer.metadata = state
    renderer.displayed_version = renderer.version
    image = NS(width=640, height=320, texture=object(), free=lambda: None)
    renderer.images[1] = image
    cameras = [{'wfrl_custom_slot': 1}]
    yield NS(context=context, region=region, system=system, renderer=renderer,
             image=image, cameras=cameras, counts=counts, output=output)
    renderer.free()
    if hasattr(preview, '_CONTROL_DRAW_CACHE'):
        preview._CONTROL_DRAW_CACHE.free()


def test_twenty_redraws_reuse_batches_and_preserve_vertices_colors_and_texture(drawing):
    d = drawing
    for _ in range(20):
        d.renderer.draw(d.context, d.cameras)
    print('20 redraw batch/shader counts:', dict(d.counts))
    assert d.counts['batch:IMAGE'] == d.counts['shader:IMAGE'] == 1
    assert d.counts['batch:UNIFORM_COLOR'] == 5
    assert d.counts['shader:UNIFORM_COLOR'] == 1
    assert len(d.output) == 120
    assert all(d.output[start:start + 6] == d.output[:6] for start in range(0, 120, 6))
    assert d.output[0] == ('UNIFORM_COLOR', 'TRI_FAN',
        {'pos': [(0, 0), (1000, 0), (1000, 800), (0, 800)]}, {'color': (.018, .022, .03, 1)})
    assert d.output[5] == ('IMAGE', 'TRI_FAN',
        {'pos': [(18., 114.), (982., 114.), (982., 596.), (18., 596.)],
         'texCoord': [(0, 0), (1, 0), (1, 1), (0, 1)]}, {'image': d.image.texture})


@pytest.mark.parametrize('change', ['width', 'height', 'ui_scale', 'layout', 'texture', 'aspect', 'window', 'region'])
def test_draw_cache_rebuilds_for_region_layout_and_texture_changes(drawing, change):
    d = drawing
    d.renderer.draw(d.context, d.cameras)
    first = deepcopy(d.output[-1][2])
    closable = True
    if change in {'width', 'height'}:
        setattr(d.region, change, getattr(d.region, change) + 100)
    elif change == 'ui_scale':
        d.system.ui_scale = 1.5
    elif change == 'layout':
        closable = False
    elif change == 'texture':
        d.image.texture = object()
    elif change == 'aspect':
        d.image.height = 640
    elif change == 'window':
        d.context.window.as_pointer = lambda: 99
    else:
        d.region.as_pointer = lambda: 99
    d.renderer.draw(d.context, d.cameras, closable=closable)
    assert d.counts['batch:IMAGE'] == 2
    if change in {'width', 'height', 'ui_scale', 'layout', 'aspect'}:
        assert d.output[-1][2] != first
    assert d.output[-1][3]['image'] is d.image.texture
    counts = dict(d.counts)
    d.renderer.draw(d.context, d.cameras, closable=closable)
    assert dict(d.counts) == counts


def test_control_hud_reuses_batches_but_updates_uniforms_and_layout(drawing):
    d = drawing
    for _ in range(20):
        preview.draw_controls(d.context, '采集中', editing=True, confirmable=True, external=True)
    print('20 control redraw batch/shader counts:', dict(d.counts))
    assert d.counts['batch:UNIFORM_COLOR'] == 3
    assert d.counts['shader:UNIFORM_COLOR'] == 1
    counts = dict(d.counts)
    preview.draw_controls(d.context, '等待', editing=True, confirmable=False, external=True)
    assert dict(d.counts) == counts
    assert d.output[-2][3]['color'] == (.1, .12, .15, 1)
    d.region.width += 100
    preview.draw_controls(d.context, '等待', editing=True, external=True)
    assert d.counts['batch:UNIFORM_COLOR'] == 6


def test_preview_free_releases_batches_and_shaders(drawing):
    d = drawing
    d.renderer.draw(d.context, d.cameras)
    before = dict(d.counts)
    d.renderer.free()
    d.renderer.images[1] = d.image
    d.renderer.request = dict(time_s=0., frame=1, subframe=0.)
    d.renderer.draw(d.context, d.cameras)
    assert dict(d.counts) == {key: count * 2 for key, count in before.items()}
