"""Custom-camera edit transactions, viewport snapshots and lifecycle callbacks."""
from bpy.app.handlers import persistent


def dismiss_for_view_switch(scene):
    """Release the overlay before a legacy camera writes its target view.

    Cancel any pending draft first (including its playback/snapshot restore),
    then let the requested camera take ownership. Never restore our old view
    after that target has been assigned.
    """
    if _panel._ACTIVE and _panel._ACTIVE.scene == scene:
        _panel._ACTIVE.finish(restore=False)


def session_identity(scene):
    """Identity rather than timeline position: ordinary playback is not a reload."""
    from .. import clearance_replay, farm_flex, runtime
    parent = scene.objects.get('WFRL.Turbine.T1.YawRoot')
    state = getattr(runtime, '_state', None)
    return (scene.as_pointer(), parent.as_pointer() if parent else None,
            id(clearance_replay.reader_for(scene)), id(farm_flex.active_for(scene)),
            getattr(state, 'session_id', None), scene.get('wfrl_clearance_demo'),
            scene.get('wfrl_farm_demo'))


def installation_block_reason(scene):
    """Only the Blender timeline is paused here; never race a live producer."""
    from .. import runtime
    from . import stacked_camera_rig as box_panel
    if box_panel._ACTIVE:
        return '请先确认或取消整个盒体的安装'
    state = runtime.get_state()
    if state.connection != 'LOCAL DEMO':
        if (getattr(runtime, '_pending_command', None) or not state.confirmed
                or state.run_status in {'STARTING', 'RUNNING', 'DRAINING'}):
            return '请先暂停后端并等待暂停确认，再调整自定义相机'
    elif scene.get('wfrl_scene_kind') == 'live' and state.run_status in {'STARTING', 'RUNNING', 'DRAINING'}:
        return '请先暂停实时运行，再调整自定义相机'
    return ''


class ViewportSnapshot:
    def __init__(self, area):
        self.area = area
        space = area.spaces.active
        rv = space.region_3d
        self.local, self.camera, self.lock = space.use_local_camera, space.camera, space.lock_camera
        self.values = {name: getattr(rv, name).copy() if hasattr(getattr(rv, name), 'copy')
                       else getattr(rv, name) for name in
                       ('view_distance', 'view_location', 'view_rotation', 'view_perspective',
                        'view_camera_zoom', 'view_camera_offset')}

    def restore(self):
        try:
            if self.area.type != 'VIEW_3D':
                return
            space = self.area.spaces.active
            space.use_local_camera, space.lock_camera = self.local, self.lock
            try:
                space.camera = self.camera
            except ReferenceError:
                space.camera = None
            for name, value in self.values.items():
                setattr(space.region_3d, name, value)
            self.area.tag_redraw()
        except (ReferenceError, RuntimeError):
            pass


class EditSession:
    """Transaction usable in background tests without a window or UI context."""
    def __init__(self, scene, slot, area=None, window=None, wm=None):
        self.scene, self.slot = scene, slot
        self.area, self.window, self.wm = area, window, wm
        self.identity = _panel.session_identity(scene)
        self.frame, self.subframe = scene.frame_current, scene.frame_subframe
        self.playing = bool(window and _panel.playback.is_playing(scene))
        self.snapshot = _panel.ViewportSnapshot(area) if area else None
        self.original_camera = _panel.core.get_camera(scene, slot)
        self.draft, self.draft_data, self.closed = None, None, False
        self.error = ''

    def valid(self):
        try:
            if self.closed or _panel.session_identity(self.scene) != self.identity or _panel.installation_block_reason(self.scene):
                return False
            if self.window and (self.window.scene != self.scene or
                                self.window not in list(self.wm.windows)):
                return False
            if self.area and (self.area.type != 'VIEW_3D' or
                              self.area not in list(self.window.screen.areas)):
                return False
            return True
        except (ReferenceError, RuntimeError):
            return False

    def _play(self, playing):
        if not self.window:
            return
        with _panel.bpy.context.temp_override(window=self.window, screen=self.window.screen):
            owner = _panel.playback.owner_scene()
            live = _panel.playback.any_playing()
            if (owner is not None and owner != self.scene) or (live and owner is None):
                return
            if playing and not live:
                _panel.bpy.ops.screen.animation_play()
            elif not playing and live:
                _panel.bpy.ops.screen.animation_cancel(restore_frame=False)

    def begin(self):
        reason = _panel.installation_block_reason(self.scene)
        if reason:
            raise ValueError(reason)
        self._play(False)
        try:
            self.draft = _panel.core.begin_draft(self.scene, self.slot)
            self.draft_data = self.draft.data
        except Exception:
            self._play(self.playing)
            raise
        return self.draft

    def confirm(self):
        if not self.valid():
            self.cancel(restore=False)
            raise ValueError('场景或回放会话已变化，请重新安装')
        camera = _panel.core.commit_draft(self.scene, self.slot, self.draft)
        self.draft = self.draft_data = None
        self._play(self.playing)
        self.closed = True
        return camera

    def cancel(self, restore=True):
        if self.closed:
            return
        same = self.valid()
        if self.draft is not None:
            try:
                _panel.core.cancel_draft(self.draft, data=self.draft_data)
            except (RuntimeError, ValueError) as exc:
                self.error = '相机草稿清理失败，请重试取消：' + str(exc)
                raise RuntimeError(self.error) from exc
            self.draft = self.draft_data = None
        if restore and same:
            if self.snapshot:
                self.snapshot.restore()
            self._play(self.playing)
        self.closed = True
        self.error = ''


@persistent
def cancel_on_load(_unused=None):
    from . import stacked_camera_rig as box_panel
    box_panel.shutdown(restore=False)
    if _panel._ACTIVE:
        _panel._ACTIVE.finish(restore=False,resume=False)
    from .. import custom_camera_capture
    custom_camera_capture.shutdown(restore=False)
    _panel.preview.shutdown()
    _panel.history.HISTORY.clear('加载文件，相机撤销历史已清理')


def lifecycle_watchdog():
    # Window-close/area replacement can remove modal event delivery entirely.
    # Keep cleanup independent of the viewport event stream.
    from .. import custom_camera_capture
    from . import stacked_camera_rig as box_panel
    if box_panel._ACTIVE:
        op=box_panel._ACTIVE
        try:
            valid=(op.window in list(op.wm.windows) and op.area in list(op.window.screen.areas)
                   and op.area.type=='VIEW_3D' and op.window.scene==op.scene and op.transaction.same_session())
        except (ReferenceError,RuntimeError):valid=False
        if not valid:box_panel.shutdown()
    if custom_camera_capture.active() and not custom_camera_capture._ACTIVE.same_session():
        custom_camera_capture.shutdown(restore=False)
    if _panel._ACTIVE:
        try:
            valid = (_panel._ACTIVE.window in list(_panel._ACTIVE.wm.windows)
                and _panel._ACTIVE.area in list(_panel._ACTIVE.window.screen.areas)
                and _panel._ACTIVE.area.type == 'VIEW_3D'
                and _panel._ACTIVE.window.scene == _panel._ACTIVE.scene
                and _panel.session_identity(_panel._ACTIVE.scene) == _panel._ACTIVE.identity)
        except (ReferenceError, RuntimeError):
            valid = False
        if not valid:
            try:
                _panel._ACTIVE.finish(restore=False,resume=False)
            except (RuntimeError, ValueError):
                pass  # finish reports the error and retains resources for retry.
    if (not box_panel._ACTIVE and _panel.history.HISTORY.entries and getattr(_panel.bpy.context, 'scene', None) is not None
            and not _panel.playback.any_playing()):
        _panel.history.watchdog_status(_panel.bpy.context.scene)
    return .25


def discard_saved_drafts(scene):
    """Remove drafts that no edit session owns, such as one saved mid-edit.

    A saved draft is an unconfirmed change. Left in place, it would block every
    later edit, clear, undo and capture of this scene.
    """
    session = getattr(_panel._ACTIVE, 'session', None)
    owned = session.draft if session is not None and not session.closed else None
    for obj in list(scene.objects):
        if obj.get('wfrl_custom_draft') and obj != owned:
            _panel.core.cancel_draft(obj)


@persistent
def migrate_loaded(_unused=None):
    _panel.preview.register_change_handler()
    if not _panel.bpy.app.timers.is_registered(_panel.lifecycle_watchdog):
        _panel.bpy.app.timers.register(_panel.lifecycle_watchdog, first_interval=.25)
    for scene in _panel.bpy.data.scenes:
        _panel.discard_saved_drafts(scene)
        if _panel.core.is_available(scene):
            _panel.core.migrate_scene(scene)


def status_for(scene):
    from ..home_state import TaskStatus
    job = _panel._ACTIVE
    if job is None:
        return TaskStatus('camera')
    return TaskStatus('camera', True, job.scene == scene, job.stage,
                      job.scene.name, getattr(job, 'error', ''), '')


# Runtime callbacks share the public namespace; definitions are available first
# so importing either this module or the public entry point is supported.
from . import custom_cameras as _panel
