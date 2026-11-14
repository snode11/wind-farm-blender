"""Reconstruction callback ownership and RNA invalidation on the host."""
import importlib.util
from contextlib import nullcontext
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS

import pytest

from wfrl_blender import split_reconstruction as split


class Timers:
    def __init__(self):
        self.callbacks = set()
        self.fail_register = self.fail_unregister = False

    def is_registered(self, callback):
        return callback in self.callbacks

    def register(self, callback, **kwargs):
        if self.fail_register:
            raise RuntimeError('timer registration failed')
        self.callbacks.add(callback)

    def unregister(self, callback):
        if self.fail_unregister:
            raise RuntimeError('timer removal failed')
        self.callbacks.remove(callback)


@pytest.fixture
def runtime(monkeypatch):
    handlers = ModuleType('bpy.app.handlers')
    handlers.persistent = lambda callback: callback
    for name in ('frame_change_post', 'load_pre', 'load_post', 'save_pre',
                 'undo_pre', 'undo_post', 'redo_pre', 'redo_post', 'depsgraph_update_post'):
        setattr(handlers, name, [])
    draws, headers, classes = [], [], []
    failures = set()

    def add_draw(*args):
        if 'draw_add' in failures:
            raise RuntimeError('draw registration failed')
        handle = object()
        draws.append(handle)
        return handle

    def remove_draw(handle, *args):
        if 'draw_remove' in failures:
            raise RuntimeError('draw removal temporarily unavailable')
        draws.remove(handle)

    class Operator:
        is_registered = False

        @staticmethod
        def bl_rna_get_subclass_py(identifier, default):
            return default

    def register_class(cls):
        if 'class_add' in failures and classes:
            raise RuntimeError('class registration failed')
        classes.append(cls)
        cls.is_registered = True

    def unregister_class(cls):
        classes.remove(cls)
        cls.is_registered = False

    props = ModuleType('bpy.props')
    for name in ('StringProperty', 'BoolProperty', 'FloatProperty', 'EnumProperty', 'IntProperty'):
        setattr(props, name, lambda **kwargs: object())
    bpy = ModuleType('bpy')
    bpy.props = props
    bpy.types = NS(Scene=type('Scene', (), {}), Operator=Operator,
        SpaceView3D=NS(draw_handler_add=add_draw, draw_handler_remove=remove_draw),
        VIEW3D_HT_header=NS(draw=NS(_draw_funcs=headers), append=headers.append, remove=headers.remove))
    bpy.utils = NS(register_class=register_class, unregister_class=unregister_class)
    bpy.app = NS(handlers=handlers, timers=Timers(), background=False, driver_namespace={})
    bpy.data = NS(scenes=[])
    bpy.context = NS(temp_override=lambda **kwargs: nullcontext(), window_manager=NS(windows=[]))
    for name, module in {'bpy': bpy, 'bpy.props': props, 'bpy.app.handlers': handlers,
                         'blf': ModuleType('blf'), 'mathutils': NS(Matrix=object, Vector=object)}.items():
        monkeypatch.setitem(sys.modules, name, module)

    def load(name):
        path = Path(split.__file__).with_name(name + '.py')
        spec = importlib.util.spec_from_file_location('wfrl_blender._cc_' + name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    monkeypatch.setattr(split, '_REGISTERED', False)
    monkeypatch.setattr(split, '_JOBS', {})
    monkeypatch.setattr(split, '_SESSIONS', {})
    monkeypatch.setattr(split, '_BOUNDS', {})
    return NS(bpy=bpy, load=load, handlers=handlers, timers=bpy.app.timers,
              draws=draws, headers=headers, classes=classes, failures=failures)


def callbacks(runtime):
    return [callback for values in vars(runtime.handlers).values()
            if isinstance(values, list) for callback in values]


@pytest.mark.parametrize('name', ['split_reconstruction', 'blade_recon_review', 'blade_recon_surface'])
def test_registration_failure_releases_new_callbacks(runtime, name):
    module = split if name == 'split_reconstruction' else runtime.load(name)
    runtime.timers.fail_register = True
    kwargs = {'standalone': True} if name == 'blade_recon_review' else {}
    with pytest.raises(RuntimeError, match='timer registration failed'):
        module.register(**kwargs)
    assert callbacks(runtime) == []
    assert not runtime.timers.callbacks and not runtime.draws
    assert not any(key.startswith('wfrl_recon_') for key in vars(runtime.bpy.types.Scene))
    if module is split:
        assert not split._REGISTERED


def test_review_draw_failure_preserves_preexisting_shared_properties(runtime):
    review = runtime.load('blade_recon_review')
    review.register()
    properties = vars(runtime.bpy.types.Scene).copy()
    runtime.failures.add('draw_add')
    with pytest.raises(RuntimeError, match='draw registration failed'):
        review.register(standalone=True)
    assert callbacks(runtime) == [] and not runtime.timers.callbacks
    assert vars(runtime.bpy.types.Scene) == properties


@pytest.mark.parametrize('name', ['split_reconstruction', 'blade_recon_review', 'blade_recon_surface'])
def test_unregister_continues_after_timer_failure_and_can_retry(runtime, monkeypatch, name):
    module = split if name == 'split_reconstruction' else runtime.load(name)
    if module is split:
        monkeypatch.setattr(split, '_load_pre', lambda _: split._JOBS.clear())
    module.register(**({'standalone': True} if name == 'blade_recon_review' else {}))
    runtime.timers.fail_unregister = True
    with pytest.raises(RuntimeError, match='timer removal failed'):
        module.unregister()
    assert not callbacks(runtime) and not runtime.draws
    assert module._tick in runtime.timers.callbacks
    runtime.timers.fail_unregister = False
    module.unregister()
    assert not runtime.timers.callbacks


def test_ui_failed_draw_removal_keeps_handle_and_prevents_duplicate(runtime):
    ui = runtime.load('split_reconstruction_ui')
    ui._install_draw_callbacks()
    original = ui._HUD_HANDLE
    runtime.failures.add('draw_remove')
    with pytest.raises(RuntimeError, match='draw removal'):
        ui._install_draw_callbacks()
    assert ui._HUD_HANDLE is original
    assert runtime.bpy.app.driver_namespace[ui._RUNTIME_KEY]['handle'] is original
    assert runtime.draws == [original]
    runtime.failures.clear()
    ui._remove_draw_callbacks()
    assert not runtime.draws and not runtime.headers and ui._HUD_HANDLE is None


def test_ui_draw_registration_failure_rolls_back_header(runtime):
    ui = runtime.load('split_reconstruction_ui')
    runtime.failures.add('draw_add')
    with pytest.raises(RuntimeError, match='draw registration failed'):
        ui._install_draw_callbacks()
    assert not runtime.headers and not runtime.draws


def test_ui_class_registration_failure_rolls_back_new_classes(runtime):
    ui = runtime.load('split_reconstruction_ui')
    runtime.failures.add('class_add')
    with pytest.raises(RuntimeError, match='class registration failed'):
        ui.register()
    assert not runtime.classes and not callbacks(runtime)


def test_repeated_registration_is_idempotent(runtime):
    review = runtime.load('blade_recon_review')
    review.register(standalone=True)
    before = (callbacks(runtime), set(runtime.timers.callbacks), list(runtime.draws),
              vars(runtime.bpy.types.Scene).copy())
    review.register(standalone=True)
    assert (callbacks(runtime), runtime.timers.callbacks, runtime.draws,
            vars(runtime.bpy.types.Scene)) == before


def test_review_failed_draw_removal_keeps_retry_handle_and_inert_callback(runtime):
    review = runtime.load('blade_recon_review')
    review.register(standalone=True)
    original = review.HUD_HANDLE
    runtime.failures.add('draw_remove')
    with pytest.raises(RuntimeError, match='draw removal'):
        review.unregister()
    assert review.HUD_HANDLE is original and runtime.draws == [original]
    review._hud()  # An unregistered callback must not access the removed Scene properties.
    runtime.failures.clear()
    review.unregister()
    assert not runtime.draws and review.HUD_HANDLE is None


@pytest.mark.parametrize('reload_module', [False, True])
def test_ui_failed_draw_removal_makes_callback_inert(runtime, reload_module):
    ui = runtime.load('split_reconstruction_ui')
    ui._install_draw_callbacks()
    current = runtime.load('split_reconstruction_ui') if reload_module else ui
    runtime.failures.add('draw_remove')
    with pytest.raises(RuntimeError, match='draw removal'):
        current.unregister()
    ui._draw_hud()


def test_texture_toggle_failure_preserves_mode(runtime):
    ui = runtime.load('split_reconstruction_ui')
    scene = Scene('comparison')
    scene.wfrl_recon_texture_mode = 'ORIGINAL'

    def toggle(scene):
        scene.wfrl_recon_texture_mode = 'CONTRAST'

    def fit(context):
        raise ValueError('layout is busy')

    ui._core = lambda: NS(texture_adapter=lambda scene: NS(toggle=toggle), fit=fit)
    operator = ui.WFRL_OT_SplitReconTextureToggle()
    operator.report = lambda *args: None
    assert operator.execute(NS(scene=scene)) == {'CANCELLED'}
    assert scene.wfrl_recon_texture_mode == 'ORIGINAL'


class Scene(dict):
    def __init__(self, kind):
        super().__init__(wfrl_scene_kind=kind)
        self.objects = []
        self.frame_current = 1

    def as_pointer(self):
        return id(self)


@pytest.mark.parametrize('name,kind,prefix', [
    ('blade_recon_review', 'blade_recon', 'blade_recon'),
    ('blade_recon_surface', 'blade_recon_surface', 'surface')])
def test_history_invalidates_rna_and_allows_restore_retry(runtime, name, kind, prefix):
    module = runtime.load(name)
    scene = Scene(kind)
    runtime.bpy.data.scenes.append(scene)
    module.register(**({'standalone': True} if name == 'blade_recon_review' else {}))
    for event in ('undo', 'redo', 'load'):
        module.SESSIONS[scene.as_pointer()] = {'blades': [object()]}
        for callback in getattr(runtime.handlers, event + '_pre'):
            callback(None)
        assert not module.SESSIONS, event
        scene[prefix + '_restore_failed'] = True
        for callback in getattr(runtime.handlers, event + '_post'):
            callback(None)
        assert not scene.get(prefix + '_restore_failed'), event


@pytest.mark.parametrize('name', ['blade_recon_review', 'blade_recon_surface'])
def test_timer_drops_deleted_scene_sessions(runtime, name):
    module = runtime.load(name)
    module.SESSIONS[4242] = {'blades': [object()]}
    module._tick()
    assert not module.SESSIONS


@pytest.mark.parametrize('name,kind', [('blade_recon_review', 'blade_recon'),
                                     ('blade_recon_surface', 'blade_recon_surface')])
def test_timer_restores_new_scene_reusing_pointer(runtime, monkeypatch, name, kind):
    module = runtime.load(name)
    scene = Scene(kind)
    scene.session_uid = 202
    runtime.bpy.data.scenes.append(scene)
    module.SESSIONS[scene.as_pointer()] = dict(scene_uid=101, index=0, pending=False,
                                              sequence=NS(states=[None]))
    module.register(**({'standalone': True} if name == 'blade_recon_review' else {}))
    restored = []

    def restore(scene):
        restored.append(scene)
        module.SESSIONS[scene.as_pointer()] = dict(scene_uid=202, index=0, pending=False,
                                                  sequence=NS(states=[None]))

    monkeypatch.setattr(module, '_restore' if name == 'blade_recon_review' else 'restore', restore)
    module._tick()
    assert restored == [scene]


@pytest.mark.parametrize('raises', [False, True])
def test_old_split_job_cannot_publish_over_reentrant_exit(runtime, monkeypatch, raises):
    from wfrl_blender import cameras
    scene = Scene('comparison')
    screen = object()
    window = NS(scene=scene, screen=screen)
    areas = [NS(spaces=NS(active=object())), NS(spaces=NS(active=object()))]
    old = dict(scene=scene, screen=screen, window=window, stage='ready',
               settle=0., deadline=10., signature='same', detail=True)
    replacement = dict(stage='exit')
    split._JOBS[1] = old
    monkeypatch.setattr(cameras, '_LAYOUT_JOB', None)
    monkeypatch.setattr(split, '_views', lambda screen: areas)
    monkeypatch.setattr(split, '_signature', lambda areas: 'same')
    monkeypatch.setattr(split, '_region', lambda area: None)
    monkeypatch.setattr(split, '_remember', lambda *args: split._SESSIONS.update({1: 'old'}))
    monkeypatch.setattr(split, '_redraw_scene', lambda scene: None)

    def focus(*args):
        split._JOBS[1] = replacement
        scene['split_review_status'] = 'EXITING'
        if raises:
            raise RuntimeError('old focus interrupted')

    monkeypatch.setattr(split, 'texture_adapter', lambda scene: NS(focus=focus))
    split._advance_jobs(1.)
    assert split._JOBS.get(1) is replacement
    assert scene['split_review_status'] == 'EXITING'
    assert not split._SESSIONS


def test_entry_timer_failure_cancels_its_pending_layout(monkeypatch):
    from blender_frontend.tests.test_split_reconstruction import Scene as SplitScene, _entry_context
    from wfrl_blender import cameras
    context, _ = _entry_context(monkeypatch, SplitScene(split_review_enabled=False))
    owned_layout = {'scene': context.scene, 'window': context.window}
    canceled = []

    def begin(*args, **kwargs):
        cameras._LAYOUT_JOB = owned_layout

    def cancel():
        canceled.append(cameras._LAYOUT_JOB)
        cameras._LAYOUT_JOB = None

    def fail():
        raise RuntimeError('timer registration failed')

    monkeypatch.setattr(cameras, 'set_view_layout', begin)
    monkeypatch.setattr(cameras, 'cancel_view_layout', cancel)
    monkeypatch.setattr(split, '_ensure_timer', fail)
    with pytest.raises(RuntimeError, match='timer registration failed'):
        split.enter(context)
    assert canceled == [owned_layout] and cameras._LAYOUT_JOB is None
    assert not split._JOBS and not context.scene['split_review_enabled']


def test_entry_failure_removes_original_window_key(monkeypatch):
    from blender_frontend.tests.test_split_reconstruction import Scene as SplitScene, _entry_context
    context, _ = _entry_context(monkeypatch, SplitScene(split_review_enabled=False))

    def fail():
        context.window.scene = NS(as_pointer=lambda: 999)
        raise RuntimeError('window changed during entry')

    monkeypatch.setattr(split, '_ensure_timer', fail)
    with pytest.raises(RuntimeError, match='window changed'):
        split.enter(context)
    assert not split._JOBS


def test_fit_timer_failure_restores_navigation_and_frees_job(monkeypatch):
    from blender_frontend.tests.test_split_reconstruction import _mode_context, _assert_navigation_equal
    context, left, right, _, _ = _mode_context(monkeypatch, 'ORTHO')
    before = [split._navigation(area.spaces.active) for area in (left, right)]
    context.scene['split_review_status'] = 'READY'

    def fail():
        raise RuntimeError('fit timer unavailable')

    monkeypatch.setattr(split, '_ensure_timer', fail)
    with pytest.raises(RuntimeError, match='fit timer unavailable'):
        split.fit(context)
    assert not split._JOBS and context.scene['split_review_status'] == 'READY'
    for area, saved in zip((left, right), before):
        _assert_navigation_equal(split._navigation(area.spaces.active), saved)


def test_exit_layout_failure_frees_job_and_reports_error(monkeypatch):
    from blender_frontend.tests.test_split_reconstruction import _mode_context
    from wfrl_blender import cameras
    context, *_ = _mode_context(monkeypatch)
    monkeypatch.setattr(split, '_presentation', lambda space: {})
    monkeypatch.setattr(split, '_restore_presentation', lambda *args: None)
    monkeypatch.setattr(split, '_leave_local', lambda *args: None)

    def fail(*args, **kwargs):
        raise RuntimeError('exit layout unavailable')

    monkeypatch.setattr(cameras, 'set_view_layout', fail)
    with pytest.raises(RuntimeError, match='exit layout unavailable'):
        split.exit_mode(context)
    assert not split._JOBS and not context.scene['split_review_enabled']
    assert 'exit layout unavailable' in context.scene['split_review_status']


def test_entry_rejects_partial_timing_instead_of_guessing_missing_fields(monkeypatch):
    from blender_frontend.tests.test_split_reconstruction import Scene as SplitScene, _entry_context
    scene = SplitScene(split_review_enabled=False, split_reconstruction_samples=42)
    context, layouts = _entry_context(monkeypatch, scene)
    with pytest.raises(ValueError, match='Incomplete.*timing'):
        split.enter(context)
    assert not layouts and not split._JOBS
    assert 'split_reconstruction_start_s' not in scene
