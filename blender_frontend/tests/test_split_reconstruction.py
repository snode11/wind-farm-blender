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


def test_explicit_geometry_entry_deactivates_retained_same_source_and_keeps_its_choices(monkeypatch):
    from wfrl_blender import split_mappo_texture as same
    from wfrl_blender import split_texture_settings as settings
    scene = Scene(split_texture_current_source='MAPPO', split_mappo_texture_review=True)
    context, layouts = _entry_context(monkeypatch, scene)
    scene.objects[same.CAMERA] = NS(name=same.CAMERA)
    scene.wfrl_recon_texture_mode = 'CONTRAST'
    choices = settings.capture(scene)

    def unexpected_texture(_scene):
        raise AssertionError('explicit geometry entry restored texture')

    monkeypatch.setattr(same, 'ensure', unexpected_texture)
    assert split.enter(context, use_texture=False)
    assert scene[settings.CURRENT] == 'GEOMETRY'
    assert not same.active(scene)
    assert scene[same.MARKER]
    assert scene[settings._key('MAPPO')] == settings._encode(choices)
    assert split.reconstruction_camera(scene) is scene.objects['SplitRecon.Camera']
    assert layouts == [('DUAL', ('WFRL.Camera.T1.FrontQuarter', 'SplitRecon.Camera'))]
    assert (scene.frame_current, scene.frame_subframe) == (67, .5)


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


def _mode_context(monkeypatch, perspective='PERSP'):
    """Use distinct display offsets and real navigation/camera copy helpers."""
    from wfrl_blender import cameras, split_mappo_texture as same
    from wfrl_blender import split_texture_settings as settings

    class Camera:
        def __init__(self, name, location):
            self.name = name
            self.matrix_world = np.eye(4)
            self.location = location
            self.data = NS(type='ORTHO', lens=47., ortho_scale=186., sensor_width=36.,
                           sensor_height=24., sensor_fit='AUTO', shift_x=.12, shift_y=-.08,
                           clip_start=.1, clip_end=4000.)

        @property
        def location(self):
            return self.matrix_world[:3, 3]

        @location.setter
        def location(self, value):
            self.matrix_world[:3, 3] = value

    def navigation(location):
        return NS(view_perspective=perspective, view_location=np.array(location, dtype=float),
                  view_rotation=np.array([.5, .5, .5, .5]), view_distance=23.,
                  view_camera_zoom=104., view_camera_offset=(.23, -.11))

    scene = Scene(split_texture_current_source='GEOMETRY', split_mappo_texture_review=True)
    scene.frame_current, scene.frame_subframe = 67, .5
    source = Camera('WFRL.Camera.T1.FrontQuarter', (200., 300., 90.))
    legacy = Camera('SplitRecon.Camera', (280., 10015., 125.))
    texture = Camera(same.CAMERA, (10001., 0., 0.))
    scene.camera = source
    scene.objects['SplitRecon.Camera'] = legacy
    scene.objects[same.CAMERA] = texture
    left, right = area(1, source), area(2, legacy, 404)
    left.spaces.active.region_3d = navigation((7., 9., 90.))
    right.spaces.active.region_3d = navigation((3., 10004., 88.))
    right.spaces.active.lens = 61.
    screen = NS(areas=[left, right], is_animation_playing=True, as_pointer=lambda: 20)
    window = NS(scene=scene, screen=screen, as_pointer=lambda: 10)
    context = NS(scene=scene, window=window)
    monkeypatch.setitem(sys.modules, 'bpy', NS(context=NS(
        temp_override=lambda **_kw: nullcontext(), window_manager=NS(windows=[window]))))
    monkeypatch.setattr(cameras, '_LAYOUT_JOB', None)
    monkeypatch.setattr(split, '_JOBS', {})
    monkeypatch.setattr(split, '_SESSIONS', {})
    monkeypatch.setattr(split, '_display_origin', lambda target: np.array(
        (10000., 0., 0.) if same.active(target) else (0., 10000., 0.)))
    monkeypatch.setattr(split, '_configure', lambda _window, areas, **_kw:
                        setattr(areas[1].spaces.active, 'camera', split.reconstruction_camera(scene)))
    monkeypatch.setattr(split, '_remember', lambda *_a: None)
    monkeypatch.setattr(split, '_redraw_scene', lambda *_a: None)
    monkeypatch.setattr(same, 'ensure', lambda target: settings.activate(
        target, 'MAPPO', same._display_defaults({}), lambda: True))
    return context, left, right, legacy, texture


def _assert_navigation_equal(actual, expected):
    for name in ('view_rotation', 'view_location', 'view_camera_offset'):
        np.testing.assert_allclose(actual[name], expected[name], atol=1e-10)
    for name in ('view_perspective', 'view_distance', 'view_camera_zoom'):
        assert actual[name] == expected[name]


def test_mode_roundtrip_translates_private_camera_and_pivot_without_moving_clock(monkeypatch):
    from wfrl_blender import split_mappo_texture as same
    for perspective in ('PERSP', 'ORTHO', 'CAMERA'):
        context, left, right, legacy, texture = _mode_context(monkeypatch, perspective)
        left_before = split._navigation(left.spaces.active)
        right_before = split._navigation(right.spaces.active)
        camera_before = legacy.matrix_world.copy()
        optics_before = vars(legacy.data).copy()
        assert split.switch_mode(context)
        expected = dict(right_before, view_location=right_before['view_location'] + np.array((10000., -10000., 0.)))
        _assert_navigation_equal(split._navigation(right.spaces.active), expected)
        _assert_navigation_equal(split._navigation(left.spaces.active), left_before)
        np.testing.assert_allclose(texture.location, camera_before[:3, 3] + (10000., -10000., 0.))
        assert vars(texture.data) == optics_before
        assert same.active(context.scene) and right.spaces.active.camera is texture
        assert split.switch_mode(context)
        _assert_navigation_equal(split._navigation(right.spaces.active), right_before)
        _assert_navigation_equal(split._navigation(left.spaces.active), left_before)
        np.testing.assert_allclose(legacy.matrix_world, camera_before)
        assert vars(legacy.data) == optics_before
        assert right.spaces.active.camera is legacy and right.spaces.active.lens == 61.
        assert (context.scene.frame_current, context.scene.frame_subframe) == (67, .5)
        assert context.window.screen.is_animation_playing


def test_geometry_to_texture_restores_its_record_without_claiming_retained_markers(monkeypatch):
    from wfrl_blender import split_texture_settings as settings
    for retained_synthetic in (False, True):
        context, _left, _right, _legacy, _texture = _mode_context(monkeypatch)
        scene = context.scene
        scene.wfrl_recon_texture_mode = 'CONTRAST'
        expected = settings.capture(scene)
        scene[settings._key('MAPPO')] = settings._encode(expected)
        synthetic_record = settings._encode(dict(expected, wfrl_recon_texture_gain=13.))
        scene[settings._key('SYNTHETIC')] = synthetic_record
        scene['split_texture_review'] = retained_synthetic
        # Geometry is an explicit source state; its RNA values do not own an
        # inactive texture source's latest display record.
        scene.wfrl_recon_texture_mode = 'ORIGINAL'
        assert split.switch_mode(context)
        assert settings.capture(scene) == expected
        assert scene[settings._key('SYNTHETIC')] == synthetic_record


def test_failed_mode_configuration_restores_right_camera_role_and_navigation(monkeypatch):
    from wfrl_blender import split_texture_settings as settings
    context, left, right, legacy, texture = _mode_context(monkeypatch)
    left_before = split._navigation(left.spaces.active)
    right_before = split._navigation(right.spaces.active)
    target_before = texture.matrix_world.copy()
    texture.data.lens, texture.data.ortho_scale = 19., 321.
    optics_before = vars(texture.data).copy()
    def fail(_window, areas, **_kw):
        areas[1].spaces.active.camera = texture
        areas[1].spaces.active.region_3d.view_distance = 987.
        raise ValueError('native configure failed')
    monkeypatch.setattr(split, '_configure', fail)
    try:
        split.switch_mode(context)
    except ValueError as exc:
        assert 'native configure failed' in str(exc)
    else:
        raise AssertionError('failed configuration unexpectedly succeeded')
    assert context.scene[settings.CURRENT] == 'GEOMETRY'
    assert right.spaces.active.camera is legacy
    _assert_navigation_equal(split._navigation(left.spaces.active), left_before)
    _assert_navigation_equal(split._navigation(right.spaces.active), right_before)
    np.testing.assert_allclose(texture.matrix_world, target_before)
    assert vars(texture.data) == optics_before


def test_saved_navigation_reopens_without_scheduling_an_implicit_fit(monkeypatch):
    context, left, right, _legacy, _texture = _mode_context(monkeypatch)
    bpy = sys.modules['bpy']
    bpy.data = NS(scenes=[context.scene])
    split._save_pre(None)
    assert context.scene['split_review_navigation_saved']
    left_before = split._navigation(left.spaces.active)
    right_before = split._navigation(right.spaces.active)
    configured = []
    monkeypatch.setattr(split, '_REGISTERED', True)
    monkeypatch.setattr(split, '_advance_jobs', lambda _now: None)
    monkeypatch.setattr(split, '_configure', lambda *_a, **kw: configured.append(kw))
    monkeypatch.setattr(split, '_timeline_all', lambda *_a: None)
    assert split._tick() == split._INTERVAL
    assert configured == [{}] and split._JOBS == {}
    assert context.scene['split_review_status'] == 'READY'
    _assert_navigation_equal(split._navigation(left.spaces.active), left_before)
    _assert_navigation_equal(split._navigation(right.spaces.active), right_before)


def test_return_after_exit_queues_saved_right_navigation_and_current_clock(monkeypatch):
    import json
    from wfrl_blender import split_mappo_texture as same
    scene = Scene(split_texture_current_source='MAPPO', split_mappo_texture_review=True,
                  split_review_enabled=False)
    context, _layouts = _entry_context(monkeypatch, scene)
    scene.objects[same.CAMERA] = NS(name=same.CAMERA)
    retained = dict(view_perspective='CAMERA', view_rotation=[.5, .5, .5, .5],
                    view_location=[10003., 4., 88.], view_distance=23.,
                    view_camera_zoom=104., view_camera_offset=[.23, -.11])
    scene['split_review_return_navigation'] = json.dumps(retained)
    monkeypatch.setattr(same, 'ensure', lambda _scene: None)
    assert split.enter(context)
    job = split._JOBS[split._key(context.window)]
    assert job['right_navigation'] == retained
    assert (job['frame'], job['subframe']) == (67, .5)
    assert scene['split_texture_current_source'] == 'MAPPO'


def test_stable_same_source_timer_refreshes_without_reactivating_settings(monkeypatch):
    scene = Scene(split_texture_current_source='MAPPO', split_mappo_texture_review=True)
    window = NS(scene=scene, screen=NS(areas=[], as_pointer=lambda: 20), as_pointer=lambda: 10)
    monkeypatch.setitem(sys.modules, 'bpy', NS(context=NS(window_manager=NS(windows=[window]))))
    monkeypatch.setattr(split, '_REGISTERED', True)
    monkeypatch.setattr(split, '_advance_jobs', lambda _now: None)
    monkeypatch.setattr(split, '_JOBS', {})
    calls = []
    def forbidden(_scene):
        raise AssertionError('poll reactivated display settings')
    adapter = NS(active=lambda _scene: True, refresh=lambda target: calls.append(target), ensure=forbidden)
    monkeypatch.setattr(split, 'texture_adapter', lambda _scene: adapter)
    monkeypatch.setattr(split, '_pair', lambda *_a: None)
    for _ in range(20):
        assert split._tick() == split._INTERVAL
    assert calls == [scene]*20


def test_history_boundary_clears_data_bindings_and_jobs_without_losing_view_session(monkeypatch):
    from wfrl_blender import split_mappo_texture as same, split_turbine_context as context
    state = {1: dict(signature='user-sized layout', changed=None, areas=(10, 11))}
    monkeypatch.setattr(split, '_SESSIONS', state.copy())
    monkeypatch.setattr(split, '_JOBS', {1: object()})
    monkeypatch.setattr(split, '_BOUNDS', {1: object()})
    monkeypatch.setattr(same, '_SESSIONS', {1: object()})
    monkeypatch.setattr(context, '_SESSIONS', {1: object()})
    monkeypatch.setattr(split, '_ensure_timer', lambda: None)
    split._history_post(None)
    assert split._SESSIONS == state and not split._JOBS and not split._BOUNDS
    assert not same._SESSIONS and not context._SESSIONS


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
