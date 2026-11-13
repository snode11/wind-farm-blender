"""Split-view isolation, selection ownership and failure rollback."""
from contextlib import contextmanager
import sys
from types import SimpleNamespace as NS

import numpy as np
import pytest

from wfrl_blender import split_reconstruction as split


class Objects(list):
    def get(self, name):
        return next((obj for obj in self if obj.name == name), None)


class Object:
    def __init__(self, name, *, selected=False):
        self.name, self.type = name, 'MESH'
        self.selected, self.hide_select = selected, False
        self.members, self.selection_calls = {}, []
        self.failure = False

    def as_pointer(self):
        return id(self)

    def hide_get(self):
        return False

    def select_set(self, selected):
        self.selection_calls.append(selected)
        self.selected = selected

    def local_view_get(self, space):
        return self.members.get(id(space), False)

    def local_view_set(self, space, included):
        if self.failure:
            self.failure = False
            raise RuntimeError('local membership failed')
        self.members[id(space)] = included


def make_area(camera):
    rv = NS(view_perspective='ORTHO', view_rotation=np.array([1., 0., 0., 0.]),
            view_location=np.array([3., 4., 5.]), view_distance=71., view_camera_zoom=43.,
            view_camera_offset=(.2, -.3))
    space = NS(type='VIEW_3D', camera=camera, use_local_camera=False, local_view=None,
               lock_camera=True, lock_object=object(), lock_cursor=True, lens=61., region_3d=rv,
               show_region_ui=True, show_region_toolbar=True, show_region_tool_header=True,
               show_gizmo=True, overlay=NS(show_overlays=True),
               shading=NS(type='WIREFRAME', use_scene_world=True, use_scene_lights=True, color_type='RANDOM'))
    return NS(type='VIEW_3D', spaces=NS(active=space), width=400, height=600,
              regions=[NS(type='WINDOW', width=400, height=600)], tag_redraw=lambda: None)


@pytest.fixture
def view_context(monkeypatch):
    from wfrl_blender import cameras
    source = NS(name='Source.Camera')
    global_camera = NS(name='Global.Camera')
    display = NS(name='SplitRecon.Camera', data=NS(show_passepartout=False, passepartout_alpha=0.))
    selected, seed, other = Object('source', selected=True), Object('SplitRecon.B1'), Object('other')
    seed.hide_select = True
    scene = NS(objects=Objects([selected, seed, other]), camera=global_camera)
    left, right = make_area(source), make_area(display)
    layer = NS(objects=NS(active=selected))
    window = NS(scene=scene, screen=NS(areas=[left, right]), view_layer=layer)
    outsider = Object('foreign', selected=True)
    foreign_layer = NS(objects=NS(active=outsider))
    foreign_scene = NS(objects=Objects([outsider]), camera=global_camera)
    class Context:
        @property
        def selected_objects(self):
            return [obj for obj in self.scene.objects if obj.selected]

        @contextmanager
        def temp_override(self, **values):
            before = vars(self).copy()
            if 'window' in values:
                self.window = values['window']
                self.scene = self.window.scene
                self.view_layer = self.window.view_layer
            self.__dict__.update(values)
            try:
                yield self
            finally:
                self.__dict__.clear()
                self.__dict__.update(before)
    context = Context()
    context.scene, context.view_layer, context.area = foreign_scene, foreign_layer, make_area(global_camera)
    calls = []
    def localview(*, frame_selected):
        assert frame_selected is False
        space = context.area.spaces.active
        calls.append(tuple(obj.name for obj in context.selected_objects))
        if space.local_view:
            space.local_view = None
        else:
            space.local_view = object()
            for obj in context.scene.objects:
                obj.members[id(space)] = obj.selected
        return {'FINISHED'}
    bpy = NS(context=context, ops=NS(view3d=NS(localview=localview)))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setattr(split, 'texture_adapter', lambda _: NS(active=lambda _: False))
    monkeypatch.setattr(split, 'ensure_geometry_structure', lambda _: None)
    monkeypatch.setattr(split, 'reconstruction_camera', lambda _: display)
    monkeypatch.setattr(split, '_sidebar_category', lambda _: True)
    monkeypatch.setattr(cameras, 'clear_camera_mask', lambda _: None)
    return NS(scene=scene, window=window, left=left, right=right, layer=layer, seed=seed,
              selected=selected, other=other, outsider=outsider, bpy=bpy, local_calls=calls,
              source=source, global_camera=global_camera)


def state(area, objects):
    space = area.spaces.active
    navigation = split._navigation(space)
    for field in ('view_rotation', 'view_location'):
        navigation[field] = tuple(navigation[field])
    return dict(navigation=navigation, presentation=split._presentation(space),
                color_type=space.shading.color_type, camera=space.camera,
                use_local_camera=space.use_local_camera, lock_camera=space.lock_camera,
                lock_object=space.lock_object, lock_cursor=space.lock_cursor,
                local=bool(space.local_view),
                members=[obj.local_view_get(space) for obj in objects] if space.local_view else None)


def test_isolate_uses_target_window_selection_and_preserves_foreign_context(view_context):
    env = view_context
    split._isolate(env.window, env.right, env.scene)
    assert env.local_calls == [('SplitRecon.B1',)]
    assert env.selected.selected and not env.seed.selected and env.seed.hide_select
    assert env.layer.objects.active is env.selected
    assert env.outsider.selected and env.outsider.selection_calls == []
    assert env.bpy.context.view_layer.objects.active is env.outsider
    assert [obj.local_view_get(env.right.spaces.active) for obj in env.scene.objects] == [False, True, False]


@pytest.mark.parametrize('already_local', [False, True])
def test_isolate_failure_restores_local_view_and_membership(view_context, already_local):
    env = view_context
    if already_local:
        env.right.spaces.active.local_view = object()
        env.other.local_view_set(env.right.spaces.active, True)
    before = state(env.right, env.scene.objects)
    env.other.failure = True
    with pytest.raises(RuntimeError, match='local membership failed'):
        split._isolate(env.window, env.right, env.scene)
    assert state(env.right, env.scene.objects) == before
    assert env.selected.selected and not env.seed.selected and env.seed.hide_select
    assert env.layer.objects.active is env.selected


@pytest.mark.parametrize('leaving', [False, True])
def test_cancelled_local_view_operation_is_a_failure(view_context, leaving):
    env = view_context
    env.right.spaces.active.local_view = object() if leaving else None
    before = state(env.right, env.scene.objects)
    env.bpy.ops.view3d.localview = lambda **_: {'CANCELLED'}
    with pytest.raises(RuntimeError):
        if leaving:
            split._leave_local(env.window, env.right)
        else:
            split._isolate(env.window, env.right, env.scene)
    assert state(env.right, env.scene.objects) == before


def test_configure_changes_only_the_requested_viewports(view_context):
    env = view_context
    outside = state(env.bpy.context.area, env.bpy.context.scene.objects)
    split._configure(env.window, [env.left, env.right], reset_view=True)
    assert env.scene.camera is env.global_camera
    assert env.left.spaces.active.camera is env.source
    assert env.left.spaces.active.use_local_camera
    assert env.right.spaces.active.use_local_camera
    assert state(env.bpy.context.area, env.bpy.context.scene.objects) == outside


@pytest.mark.parametrize('failure', ['isolate', 'redraw'])
def test_configure_failure_restores_both_views_and_scene_camera(view_context, monkeypatch, failure):
    env = view_context
    env.left.spaces.active.local_view = object()
    env.selected.local_view_set(env.left.spaces.active, True)
    before = [state(area, env.scene.objects) for area in (env.left, env.right)]
    def fail(*_args, **_kwargs):
        raise RuntimeError('configure failed')
    if failure == 'isolate':
        monkeypatch.setattr(split, '_isolate', fail)
    else:
        env.right.tag_redraw = fail
    with pytest.raises(RuntimeError, match='configure failed'):
        split._configure(env.window, [env.left, env.right], reset_view=True)
    assert [state(area, env.scene.objects) for area in (env.left, env.right)] == before
    assert env.scene.camera is env.global_camera


def test_configure_rejects_a_view_from_another_window_before_mutation(view_context):
    env = view_context
    outside = env.bpy.context.area
    before = state(outside, env.bpy.context.scene.objects)
    with pytest.raises(ValueError):
        split._configure(env.window, [env.left, outside])
    assert state(outside, env.bpy.context.scene.objects) == before
    assert env.scene.camera is env.global_camera


@pytest.mark.parametrize('missing', ['source', 'reconstruction'])
def test_configure_checks_required_cameras_before_changing_views(view_context, monkeypatch, missing):
    env = view_context
    env.left.spaces.active.local_view = object()
    env.selected.local_view_set(env.left.spaces.active, True)
    before = [state(area, env.scene.objects) for area in (env.left, env.right)]
    if missing == 'source':
        monkeypatch.setattr(split, '_source_camera', lambda *_: None)
    else:
        monkeypatch.setattr(split, 'reconstruction_camera', lambda *_: None)
    with pytest.raises(ValueError):
        split._configure(env.window, [env.left, env.right])
    assert [state(area, env.scene.objects) for area in (env.left, env.right)] == before


def test_configure_failure_restores_active_sidebar_category(view_context, monkeypatch):
    env = view_context
    sidebar = NS(type='UI', active_panel_category='Item')
    env.left.regions.append(sidebar)
    monkeypatch.setattr(split, '_sidebar_category', lambda _: setattr(sidebar, 'active_panel_category', 'MAPPO'))
    def fail(*_args):
        raise RuntimeError('isolation failed')
    monkeypatch.setattr(split, '_isolate', fail)
    with pytest.raises(RuntimeError, match='isolation failed'):
        split._configure(env.window, [env.left, env.right])
    assert sidebar.active_panel_category == 'Item'
