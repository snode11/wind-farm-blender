"""Saved split ownership and lifecycle, without opening a Blender window."""
import sys
from contextlib import nullcontext
from types import SimpleNamespace as NS

import numpy as np

from wfrl_blender import split_reconstruction as split


class Scene(dict):
    def __init__(self, **values):
        super().__init__(split_reconstruction_review=True, **values)
        self.objects = {name: object() for name in
                        ('SplitRecon.Camera', 'SplitRecon.B1', 'SplitRecon.B2', 'SplitRecon.B3')}

    def as_pointer(self):
        return 30


def area(pointer, camera, x=0):
    return NS(type='VIEW_3D', x=x, y=0, width=400, height=600,
              spaces=NS(active=NS(type='VIEW_3D', camera=camera)), as_pointer=lambda: pointer)


def test_saved_marker_supports_legacy_scene_and_explicit_exit():
    scene = Scene()
    right = NS(type='VIEW_3D', camera=scene.objects['SplitRecon.Camera'])
    assert split.available(scene) and split.enabled(scene)
    assert split.is_reconstruction_view(scene, right)
    # Camera ownership survives a manually orbited right view.
    right.region_3d = NS(view_perspective='PERSP')
    assert split.is_reconstruction_view(scene, right)
    scene['split_review_enabled'] = False
    assert split.available(scene) and not split.enabled(scene)
    assert not split.is_reconstruction_view(scene, right)


def test_incomplete_scene_does_not_claim_split_or_unrelated_camera():
    scene = Scene()
    assert not split.is_reconstruction_view(scene, NS(type='VIEW_3D', camera=object()))
    del scene.objects['SplitRecon.B3']
    assert not split.available(scene)


def test_loaded_mappo_offers_texture_entry_without_legacy_split_geometry():
    scene = Scene(wfrl_farm_flex_path='__WFRL_BUNDLED_MAPPO__')
    scene.pop('split_reconstruction_review')
    scene.objects = {'WFRL.Turbine.T1.Rotor': object()}
    assert split.available(scene)
    assert not split.enabled(scene)
    scene['split_review_enabled'] = True
    assert split.enabled(scene)


def test_texture_camera_owns_right_view_and_legacy_camera_is_retained():
    from wfrl_blender import split_surface_texture
    scene = Scene(split_texture_review=True)
    legacy = scene.objects['SplitRecon.Camera']
    texture = object()
    scene.objects[split_surface_texture.CAMERA] = texture
    assert split.reconstruction_camera(scene) is texture
    assert split.is_reconstruction_view(scene, NS(type='VIEW_3D', camera=texture))
    assert not split.is_reconstruction_view(scene, NS(type='VIEW_3D', camera=legacy))
    assert scene.objects['SplitRecon.Camera'] is legacy


def _entry_context(monkeypatch, scene):
    """Exercise entry routing while replacing native layout and timer work."""
    from wfrl_blender import cameras
    source = NS(name='WFRL.Camera.T1.FrontQuarter')
    scene.camera = source
    scene.frame_current, scene.frame_subframe = 67, .5
    if 'SplitRecon.Camera' in scene.objects:
        scene.objects['SplitRecon.Camera'] = NS(name='SplitRecon.Camera')
    source_area = area(1, source)
    screen = NS(areas=[source_area], as_pointer=lambda: 20)
    window = NS(scene=scene, screen=screen, as_pointer=lambda: 10)
    context = NS(scene=scene, window=window)
    bpy = NS(app=NS(background=False),
             context=NS(temp_override=lambda **_kw: nullcontext()))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setattr(cameras, '_LAYOUT_JOB', None)
    monkeypatch.setattr(split, '_JOBS', {})
    monkeypatch.setattr(split, '_SESSIONS', {})
    monkeypatch.setattr(split, '_navigation', lambda _space: {})
    monkeypatch.setattr(split, '_layout_factor', lambda _areas: (.5, False))
    monkeypatch.setattr(split, '_ensure_timer', lambda: None)
    layouts = []
    monkeypatch.setattr(cameras, 'set_view_layout',
                        lambda _context, mode, **kw: layouts.append((mode, kw['camera_names'])))
    return context, layouts


def test_returning_to_saved_geometry_does_not_replace_it_with_texture(monkeypatch):
    from wfrl_blender import split_surface_texture as surface
    scene = Scene(split_review_enabled=False)
    context, layouts = _entry_context(monkeypatch, scene)
    legacy_camera = scene.objects['SplitRecon.Camera']
    legacy_blades = [scene.objects[f'SplitRecon.B{b}'] for b in (1, 2, 3)]

    def unexpected_texture(_scene):
        raise AssertionError('returning to saved geometry created synthetic texture')

    monkeypatch.setattr(surface, 'ensure', unexpected_texture)
    assert split.enter(context)
    assert scene['split_review_enabled']
    assert not surface.active(scene)
    assert split.reconstruction_camera(scene) is legacy_camera
    assert [scene.objects[f'SplitRecon.B{b}'] for b in (1, 2, 3)] == legacy_blades
    assert layouts == [('DUAL', ('WFRL.Camera.T1.FrontQuarter', 'SplitRecon.Camera'))]


def test_explicit_texture_entry_upgrades_geometry_and_keeps_legacy_objects(monkeypatch):
    from wfrl_blender import split_surface_texture as surface
    scene = Scene()
    context, layouts = _entry_context(monkeypatch, scene)
    legacy_camera = scene.objects['SplitRecon.Camera']
    restored = []

    def ensure_texture(target):
        restored.append(target)
        target[surface.MARKER] = True
        target.objects[surface.CAMERA] = NS(name=surface.CAMERA)

    monkeypatch.setattr(surface, 'ensure', ensure_texture)
    assert split.enter(context, use_texture=True)
    assert restored == [scene]
    assert split.reconstruction_camera(scene) is scene.objects[surface.CAMERA]
    assert scene.objects['SplitRecon.Camera'] is legacy_camera
    assert layouts == [('DUAL', ('WFRL.Camera.T1.FrontQuarter', surface.CAMERA))]


def test_returning_to_retained_texture_restores_its_saved_sample(monkeypatch):
    from wfrl_blender import split_surface_texture as surface
    scene = Scene(split_texture_review=True, split_texture_index=47,
                  split_review_enabled=False)
    context, layouts = _entry_context(monkeypatch, scene)
    scene.objects[surface.CAMERA] = NS(name=surface.CAMERA)
    restored = []
    monkeypatch.setattr(surface, 'ensure', lambda target: restored.append(target))
    assert split.enter(context)
    assert restored == [scene]
    assert scene[surface.INDEX] == 47
    assert split.reconstruction_camera(scene) is scene.objects[surface.CAMERA]
    assert layouts == [('DUAL', ('WFRL.Camera.T1.FrontQuarter', surface.CAMERA))]


def test_loaded_mappo_enters_texture_when_no_saved_geometry_exists(monkeypatch):
    from wfrl_blender import split_surface_texture as surface
    scene = Scene(wfrl_farm_flex_path='__WFRL_BUNDLED_MAPPO__')
    scene.pop('split_reconstruction_review')
    scene.objects = {'WFRL.Turbine.T1.Rotor': object()}
    context, layouts = _entry_context(monkeypatch, scene)
    restored = []

    def ensure_texture(target):
        restored.append(target)
        target[surface.MARKER] = True
        target.objects[surface.CAMERA] = NS(name=surface.CAMERA)

    monkeypatch.setattr(surface, 'ensure', ensure_texture)
    assert split.enter(context)
    assert restored == [scene]
    assert layouts == [('DUAL', ('WFRL.Camera.T1.FrontQuarter', surface.CAMERA))]


def test_texture_draw_information_never_restores_or_writes_ids(monkeypatch):
    from wfrl_blender import split_surface_texture as surface
    scene = NS(as_pointer=lambda: 30)
    def forbidden(_scene):
        raise AssertionError('draw information attempted restoration or an ID write')
    monkeypatch.setattr(surface, 'ensure', forbidden)
    monkeypatch.setattr(surface, 'apply', forbidden)
    monkeypatch.setattr(surface, '_SESSIONS', {})
    assert surface.information(scene) is None
    sequence = NS(states=[None]*120, times=np.arange(120)/10,
                  source_frames=np.arange(120), fps=10., source_label='SYNTHETIC')
    surface._SESSIONS[30] = dict(sequence=sequence, index=16)
    info = surface.information(scene)
    assert info['index'] == 16 and info['time_s'] == 1.6 and info['samples'] == 120
    assert info['source_frame'] == 16


def test_pair_requires_existing_correctly_positioned_roles():
    scene = Scene()
    left, right = area(1, object()), area(2, scene.objects['SplitRecon.Camera'], 404)
    screen = NS(areas=[right, left])
    assert split._pair(scene, screen) == (left, right)
    right.x = 0
    left.x = 404
    assert split._pair(scene, screen) is None
    screen.areas = [left]
    assert split._pair(scene, screen) is None


def test_all_saved_poses_bound_once_and_cache_invalidates_on_key_count_change():
    split._BOUNDS.clear()
    calls = []

    def pose(values):
        def read(_property, array):
            calls.append(1)
            array[:] = np.asarray(values).ravel()
        return NS(data=NS(foreach_get=read))

    keys = NS(as_pointer=lambda: 9, key_blocks=[
        pose([[-2, 0, 1], [1, 3, 4]]), pose([[5, -7, 2], [2, 1, 8]])])
    obj = NS(data=NS(as_pointer=lambda: 8, shape_keys=keys, vertices=[1, 2]))
    corners = np.array(split._saved_corners(obj))
    np.testing.assert_equal(corners.min(axis=0), [-2, -7, 1])
    np.testing.assert_equal(corners.max(axis=0), [5, 3, 8])
    split._saved_corners(obj)
    assert len(calls) == 2
    keys.key_blocks.append(pose([[-4, 0, 0], [1, 1, 12]]))
    corners = np.array(split._saved_corners(obj))
    np.testing.assert_equal(corners.min(axis=0), [-4, -7, 0])
    np.testing.assert_equal(corners.max(axis=0), [5, 3, 12])
    assert len(calls) == 5


def test_workspace_without_pair_is_not_mutated_or_rebuilt(monkeypatch):
    scene = Scene()
    window = NS(scene=scene, screen=NS(areas=[], as_pointer=lambda: 20), as_pointer=lambda: 10)
    bpy = NS(context=NS(window_manager=NS(windows=[window])))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setattr(split, '_REGISTERED', True)
    monkeypatch.setattr(split, '_advance_jobs', lambda _now: None)
    monkeypatch.setattr(split, '_SESSIONS', {})
    monkeypatch.setattr(split, '_JOBS', {})
    mutations = []
    monkeypatch.setattr(split, '_configure', lambda *_a, **_kw: mutations.append('configure'))
    monkeypatch.setattr(split, 'enter', lambda *_a, **_kw: mutations.append('enter'))
    assert split._tick() == split._INTERVAL
    assert mutations == [] and split._SESSIONS == {}


def test_resize_fit_is_debounced_and_camera_changes_do_not_trigger_repair(monkeypatch):
    scene = Scene()
    window = NS(scene=scene, screen=NS(as_pointer=lambda: 20), as_pointer=lambda: 10)
    bpy = NS(context=NS(window_manager=NS(windows=[window])))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setattr(split, '_REGISTERED', True)
    monkeypatch.setattr(split, '_advance_jobs', lambda _now: None)
    monkeypatch.setattr(split, '_JOBS', {})
    key = split._key(window)
    state = dict(signature='old', changed=None)
    monkeypatch.setattr(split, '_SESSIONS', {key: state})
    pair = (object(), object())
    monkeypatch.setattr(split, '_pair', lambda *_: pair)
    monkeypatch.setattr(split, '_signature', lambda *_: 'resized')
    monkeypatch.setattr(split, '_sidebar_category', lambda *_: True)
    now = [1.]
    monkeypatch.setattr(split.time, 'monotonic', lambda: now[0])
    fitted = []
    monkeypatch.setattr(split, '_fit_pair', lambda *_a, **_kw: fitted.append(1))
    split._tick()
    assert fitted == [] and state['changed'] == 1.
    now[0] = 1.2
    split._tick()
    assert fitted == []
    now[0] = 1.5
    split._tick()
    assert fitted == []
    assert split._JOBS[key]['explicit'] is False
    assert scene['split_review_status'] == 'PREPARING'
    now[0] = 2.
    split._tick()
    assert fitted == []
    # Losing the role does not overwrite camera state; explicit enter recovers it.
    monkeypatch.setattr(split, '_pair', lambda *_: None)
    split._tick()
    assert fitted == []


def test_automatic_fit_waits_for_two_redraws_and_never_resets_left(monkeypatch):
    from wfrl_blender import cameras
    scene = Scene()
    screen = object()
    window = NS(scene=scene, screen=screen)
    job = dict(window=window, scene=scene, screen=screen, stage='fit', settle=1., deadline=10., explicit=False)
    monkeypatch.setattr(split, '_JOBS', {1: job})
    monkeypatch.setattr(cameras, '_LAYOUT_JOB', None)
    monkeypatch.setitem(sys.modules, 'bpy', NS())
    monkeypatch.setattr(split, '_views', lambda *_: (object(), object()))
    monkeypatch.setattr(split, '_sidebar_category', lambda *_: True)
    monkeypatch.setattr(split, '_signature', lambda *_: 'stable size')
    monkeypatch.setattr(split, '_remember', lambda *_: None)
    fits, timelines = [], []
    monkeypatch.setattr(split, '_fit_pair', lambda *_a, **kw: fits.append(kw['explicit']))
    monkeypatch.setattr(split, '_timeline_all', lambda *_: timelines.append(1))
    scene['split_review_status'] = 'PREPARING'
    split._advance_jobs(1.)
    assert fits == [False] and scene['split_review_status'] == 'PREPARING'
    split._advance_jobs(1.5)
    assert fits == [False, False] and scene['split_review_status'] == 'PREPARING'
    split._advance_jobs(2.)
    assert scene['split_review_status'] == 'READY' and not split._JOBS
    assert not timelines


def test_load_boundary_drops_rna_references_and_cached_geometry(monkeypatch):
    from wfrl_blender import cameras
    canceled = []
    monkeypatch.setattr(cameras, 'cancel_view_layout', lambda: canceled.append(1))
    monkeypatch.setattr(split, '_SESSIONS', {1: object()})
    monkeypatch.setattr(split, '_JOBS', {2: object()})
    monkeypatch.setattr(split, '_BOUNDS', {3: object()})
    split._load_pre(None)
    assert not split._SESSIONS and not split._JOBS and not split._BOUNDS
    assert canceled == [1]


def test_initial_readonly_sidebar_preserves_current_tab_without_killing_lifecycle():
    class Region:
        type = 'UI'
        @property
        def active_panel_category(self):
            return 'Item'
    redraws = []
    target = NS(regions=[Region()], tag_redraw=lambda: redraws.append(1))
    assert split._sidebar_category(target) is False
    assert target.regions[0].active_panel_category == 'Item'
    assert redraws == [1]


def test_analytic_source_fit_contains_all_depths_and_sidebar_without_projection_feedback():
    points = split._corners((-74., -70., -75.), (74., 90., 75.))
    for bounds in [(-.95, -.9, .95, .9), (-.95, -.9, .2, .9), (-.8, -.6, -.2, .85)]:
        distance, offset = split._perspective_distance(points, 1.8, 2.2, bounds)
        left, bottom, right, top = bounds
        assert distance > max(point[2] for point in points)
        for x, y, z in points:
            screen_x = 1.8 * (x - offset[0]) / (distance - z)
            screen_y = 2.2 * (y - offset[1]) / (distance - z)
            assert left < screen_x < right
            assert bottom < screen_y < top


def test_exit_presentation_restores_source_when_join_retains_right_view():
    source = NS(shading=NS(type='MATERIAL', use_scene_world=True, use_scene_lights=True),
                show_region_ui=True, show_region_toolbar=True, show_region_tool_header=True,
                show_gizmo=True, overlay=NS(show_overlays=True))
    retained_right = NS(shading=NS(type='SOLID', use_scene_world=False, use_scene_lights=False),
                show_region_ui=False, show_region_toolbar=False, show_region_tool_header=False,
                show_gizmo=False, overlay=NS(show_overlays=False))
    split._restore_presentation(retained_right, split._presentation(source))
    assert split._presentation(retained_right) == split._presentation(source)


if __name__ == '__main__':
    # The local scientific runtime has NumPy but no pytest. Keep ordinary test
    # discovery and also provide a dependency-free focused runner.
    import inspect
    from contextlib import ExitStack
    from unittest.mock import patch

    class Patches:
        def __init__(self, stack):
            self.stack = stack

        def setattr(self, target, name, value):
            self.stack.enter_context(patch.object(target, name, value))

        def setitem(self, target, name, value):
            self.stack.enter_context(patch.dict(target, {name: value}))

    tests = [value for name, value in list(globals().items()) if name.startswith('test_')]
    for test in tests:
        with ExitStack() as stack:
            test(**{'monkeypatch': Patches(stack)} if 'monkeypatch' in inspect.signature(test).parameters else {})
    print(f'{len(tests)} split lifecycle/ownership tests passed')
