"""Split comparison jobs, resize debounce, timers and Blender data callbacks."""
from __future__ import annotations

from contextlib import contextmanager


def _remove_callbacks(callbacks, timer=None):
    """Attempt every release; failed native registrations remain retryable."""
    import bpy
    errors = []
    if timer is not None:
        try:
            if bpy.app.timers.is_registered(timer):
                bpy.app.timers.unregister(timer)
        except Exception as exc:
            errors.append(exc)
    for collection, callback in reversed(tuple(callbacks)):
        try:
            if callback in collection:
                collection.remove(callback)
        except Exception as exc:
            errors.append(exc)
    return errors


@contextmanager
def _register_callbacks(callbacks, timer=None, *, first_interval=.02):
    """Roll back only registrations acquired by this attempt."""
    import bpy
    added = []
    new_timer = None
    try:
        for collection, callback in callbacks:
            bpy.app.handlers.persistent(callback)
            if callback not in collection:
                collection.append(callback)
                added.append((collection, callback))
        if timer is not None and not bpy.app.timers.is_registered(timer):
            new_timer = timer
            bpy.app.timers.register(timer, first_interval=first_interval, persistent=True)
        yield
    except Exception as exc:
        for error in _remove_callbacks(added, new_timer):
            exc.add_note('Callback cleanup requires retry: ' + str(error))
        raise


def _remember(window, areas):
    _core._SESSIONS[_core._key(window)] = dict(signature=_core._signature(areas), changed=None,
                                  areas=tuple(a.as_pointer() for a in areas))


def _defer_resize_fit(scene, state):
    # A pending resize leaves the active job slot available to view operators.
    state['fit_deferred'] = True
    if (scene.get('split_review_status') == 'PREPARING' and
            not any(job['scene'] == scene for job in _core._JOBS.values())):
        scene['split_review_status'] = 'READY'
        _core._redraw_scene(scene)


def _set_error(scene, exc):
    scene['split_review_status'] = 'ERROR: ' + str(exc)
    _core._redraw_scene(scene)
    print('WFRL reconstruction split:', exc)


def _redraw_scene(scene):
    """Publish lifecycle status changes without waiting for user interaction."""
    from . import viewport
    viewport.tag_redraw((scene,))


def _advance_jobs(now):
    import bpy
    from . import cameras, playback
    for key, job in list(_core._JOBS.items()):
        if _core._JOBS.get(key) is not job:
            continue
        scene = job['scene']
        try:
            window = job['window']
            if window.screen != job['screen'] or window.scene != scene:
                del _core._JOBS[key]
                continue
            if job.get('explicit') is False and playback.is_playing(scene):
                # Playback can start between the timer callbacks of an active fit.
                pair = _core._pair(scene, window.screen)
                if (job['stage'] != 'ready' or pair is None or
                        job['signature'] != _core._signature(pair)):
                    del _core._JOBS[key]
                    state = _core._SESSIONS[key]
                    state['changed'] = now
                    _core._defer_resize_fit(scene, state)
                    continue
            if now > job['deadline']:
                raise ValueError('分屏布局超时，请再次点击恢复布局')
            if cameras._LAYOUT_JOB is not None:
                continue
            if scene.get('wfrl_view_layout_error'):
                raise ValueError(scene['wfrl_view_layout_error'])
            areas = _core._views(window.screen)
            if job['stage'] == 'exit':
                if len(areas) != 1:
                    continue
                space = areas[0].spaces.active
                space.camera = job['source']
                space.use_local_camera = False
                _core._restore_navigation(space, job['navigation'])
                _core._restore_presentation(space, job['presentation'])
                scene['split_review_status'] = 'OFF'
                _core._redraw_scene(scene)
                del _core._JOBS[key]
                continue
            if len(areas) != 2:
                continue
            if job['stage'] == 'layout':
                with bpy.context.temp_override(window=window, area=areas[0]):
                    _core._configure(window, areas, navigation=job['navigation'], reset_view=True)
                job['stage'], job['settle'] = 'balance', now + _core._DEBOUNCE
                continue
            if now < job['settle']:
                continue
            if job['stage'] == 'balance':
                _core._sidebar_category(areas[0])
                job['stage'], job['settle'] = 'fit', now + _core._DEBOUNCE
                continue
            if job['stage'] in {'fit', 'refit'}:
                if 'deferred_signature' in job:
                    signature = _core._signature(areas)
                    if signature != job['deferred_signature']:
                        job['deferred_signature'], job['settle'] = signature, now + _core._DEBOUNCE
                        continue
                    del job['deferred_signature']
                _core._sidebar_category(areas[0])
                _core._fit_pair(window, areas, explicit=job.get('explicit', True))
                if job.get('explicit', True):
                    _core._timeline_all(window)
                job['signature'] = _core._signature(areas)
                # A viewport camera update needs a redraw before its matrices
                # reflect the new pane/optics. READY is only published afterwards.
                job['stage'] = 'refit' if job['stage'] == 'fit' else 'ready'
                job['settle'] = now + _core._DEBOUNCE
                continue
            if job.get('signature') != _core._signature(areas):
                # A panel may still be animating or the user may be dragging a
                # divider. Keep PREPARING until a complete fit survives redraw.
                job['stage'], job['settle'] = 'fit', now + _core._DEBOUNCE
                continue
            if job.get('detail'):
                with bpy.context.temp_override(window=window, area=areas[1], region=_core._region(areas[1])):
                    _core.texture_adapter(scene).focus(bpy.context, areas[1])
            if _core._JOBS.get(key) is not job:
                continue
            if job.get('right_navigation') is not None:
                _core._restore_navigation(areas[1].spaces.active, job['right_navigation'])
            _core._remember(window, areas)
            scene['split_review_status'] = 'READY'
            _core._redraw_scene(scene)
            del _core._JOBS[key]
        except (ReferenceError, RuntimeError, ValueError, TypeError, AttributeError, StopIteration) as exc:
            if _core._JOBS.get(key) is not job:
                continue
            try:
                _core._set_error(scene, exc)
            except ReferenceError:
                pass
            _core._JOBS.pop(key, None)
            _core._SESSIONS[key] = {'failed': True}


def _tick():
    import bpy
    from . import playback
    if not _core._REGISTERED:
        return None
    now = _core.time.monotonic()
    _core._advance_jobs(now)
    for window in bpy.context.window_manager.windows:
        try:
            scene, key = window.scene, _core._key(window)
            if not _core.enabled(scene) or key in _core._JOBS:
                continue
            texture = _core.texture_adapter(scene)
            if texture.active(scene):
                texture.refresh(scene)
            areas = _core._pair(scene, window.screen)
            if areas is None:
                # An unrelated workspace remains untouched. Enter is the explicit
                # recovery action when that workspace does not contain a pair.
                continue
            state = _core._SESSIONS.get(key)
            if state is not None and state.get('failed'):
                continue
            if state is None:
                from .split_reconstruction_timing import spec_from_scene
                spec_from_scene(scene)
                for name, value in _core._METADATA.items():
                    if name not in scene:
                        scene[name] = value
                with bpy.context.temp_override(window=window, area=areas[0]):
                    _core._configure(window, areas)
                _core._timeline_all(window)
                _core._remember(window, areas)
                if scene.get('split_review_navigation_saved'):
                    # Blender saved the viewport itself. Restoring data/settings
                    # must not replay a user's explicit original/enhanced fit.
                    scene['split_review_status'] = 'READY'
                else:
                    areas[0].spaces.active.region_3d.view_perspective = 'PERSP'
                    areas[1].spaces.active.region_3d.view_perspective = 'CAMERA'
                    _core._JOBS[key] = dict(window=window, scene=scene, screen=window.screen,
                        stage='fit', settle=now + _core._DEBOUNCE, deadline=now + 15, explicit=True)
                    scene['split_review_status'] = 'PREPARING'
                continue
            signature = _core._signature(areas)
            if signature != state['signature']:
                state['signature'], state['changed'] = signature, now
            if state['changed'] is None:
                continue
            if playback.is_playing(scene):
                _core._defer_resize_fit(scene, state)
                continue
            if now - state['changed'] >= _core._DEBOUNCE:
                job = dict(window=window, scene=scene, screen=window.screen,
                           stage='fit', settle=now, deadline=now + 15, explicit=False)
                if state.pop('fit_deferred', False):
                    # The existing view and latest layout have already settled.
                    job.update(stage='refit', deferred_signature=signature)
                _core._JOBS[key] = job
                state['changed'] = None
            if scene.get('split_review_status') != 'PREPARING':
                scene['split_review_status'] = 'PREPARING'
        except (ReferenceError, RuntimeError, ValueError, TypeError, AttributeError, StopIteration) as exc:
            try:
                _core._set_error(window.scene, exc)
            except ReferenceError:
                pass
    return _core._INTERVAL


def _ensure_timer():
    import bpy
    if not bpy.app.background and _core._REGISTERED and not bpy.app.timers.is_registered(_core._tick):
        bpy.app.timers.register(_core._tick, first_interval=.1, persistent=True)


def _clear_research_texture_runtime():
    """The research-only synthetic texture module is absent from installed packages.

    Workspace sessions that imported it hold Blender ID references, which file
    loads and undo/redo invalidate exactly like the same-source texture runtime.
    """
    import sys
    module = sys.modules.get(__package__ + '.split_surface_texture')
    if module is not None:
        module.clear_runtime()


def _load_pre(_unused):
    from . import cameras, split_mappo_texture, split_turbine_context
    cameras.cancel_view_layout()
    _core._SESSIONS.clear()
    _core._JOBS.clear()
    _core._BOUNDS.clear()
    split_mappo_texture.clear_runtime()
    _core._clear_research_texture_runtime()
    split_turbine_context.clear_runtime()


def _load_post(_unused):
    import bpy
    for scene in bpy.data.scenes:
        if _core._legacy_available(scene):
            try:
                _core.ensure_geometry_structure(scene)
            except (ReferenceError, RuntimeError, ValueError, KeyError) as exc:
                _core._set_error(scene, exc)
    _core._ensure_timer()


def _save_pre(_unused):
    import bpy
    for scene in bpy.data.scenes:
        if _core.available(scene):
            scene['split_review_navigation_saved'] = True


def _history_post(_unused):
    """Undo/redo replaces IDs and saved metadata without a file-load callback."""
    from . import split_mappo_texture, split_turbine_context
    # Window sessions contain only scalar area/layout signatures. Retain them
    # so invalidating data cannot schedule a fit that moves the user's view.
    _core._JOBS.clear()
    _core._BOUNDS.clear()
    split_mappo_texture.clear_runtime()
    _core._clear_research_texture_runtime()
    split_turbine_context.clear_runtime()
    _core._ensure_timer()


def _depsgraph_post(scene, depsgraph):
    from . import split_turbine_context
    split_turbine_context.notice_updates(scene, depsgraph)


# The facade imports these functions, so bind its shared namespace last.
from . import split_reconstruction as _core
