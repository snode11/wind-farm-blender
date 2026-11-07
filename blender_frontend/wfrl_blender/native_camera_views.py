"""Native camera viewing in an independent window; no offscreen preview loop.

Shared scene aspect is expanded to contain each calibrated rectangle. Proxy
cameras follow originals, with no exterior mask over the surrounding viewport.
"""
import math
import uuid
from contextlib import contextmanager
import bpy
from bpy.app.handlers import persistent
from . import custom_cameras as core
from . import installation_schematic as schematic
from . import native_camera_layout as _layout
from . import native_camera_projection as _projection
from . import native_camera_lifecycle as _lifecycle
from . import native_camera_drawing as _drawing

_ACTIVE = None
_KEYMAPS = []
_ERROR_PROPERTY = 'wfrl_native_camera_error'
_PROXY_OWNER = 'wfrl_native_camera_owner'
_HISTORY_QUALITY = None


def owns_view(window, area):
    """Reserve every area of the observation window, including layout setup."""
    session = _ACTIVE
    return bool(session is not None and session.owns_window
                and window.as_pointer() == session.window_id)


_view_state = _projection._view_state


_restore_view = _projection._restore_view


def last_error(scene):
    """Keep asynchronous failures visible after their observing window closes."""
    try:
        return scene.get(_ERROR_PROPERTY, '')
    except ReferenceError:
        return ''


def _remember_error(scene, message, *, append=False):
    try:
        previous = last_error(scene)
        if append and previous:
            message = previous if message in previous else previous + '；' + message
        scene[_ERROR_PROPERTY] = message
        from . import viewport
        viewport.tag_redraw((scene,), area_types=None)
    except ReferenceError:
        # The owning scene may itself have been removed or replaced.
        pass


def _cleanup_step(errors, label, action):
    """A failed release must not prevent the remaining resources restoring."""
    try:
        result = action()
        if isinstance(result, list):
            errors.extend(result)
    except Exception as exc:
        errors.append(f'{label}：{exc}')


def fit_gate(hfov, vfov, scene_aspect):
    return _projection.fit_gate(hfov, vfov, scene_aspect, math=math)


gate_bounds = _projection.gate_bounds


def frame_gate(scene, entry, region, space):
    """Fit the calibrated gate under its title without symmetrical wasted space.

    Use Blender's current projected gate to calibrate pan/zoom, which also
    handles UI scaling, wide/tall windows and non-square render pixels.
    """
    return _projection.frame_gate(scene, entry, region, space,
                                  bpy=bpy, math=math, gate_bounds=gate_bounds)


class NativeSession:
    # Session dependencies share this public module's live state and helpers.
    _native = globals()

    def __init__(self, context, slots, layout='GRID'):
        self.window, self.scene, self.original = context.window, context.scene, context.workspace
        self.source_window_id = context.window.as_pointer()
        self.window_id = self.source_window_id
        self.closing = False
        self.closed = False
        self.resume_when_closed = False
        self.resume_scheduled = False
        self.resume_generation = None
        self.lifecycle = []
        self.workspace = None
        self.owns_window = False
        self.entries = []
        self.handle = None
        self.diagram_handle = None
        self.diagram_area = None
        self.diagram_key = None
        self.diagram = None
        self.diagram_stale = True
        self.diagram_error = ''
        self.diagram_batches = None
        self.layout = layout
        self.preparing = False
        self.preparation_steps = 0
        self.merge_first = False
        self.slots = slots
        self.source_shading = []
        self.preview_quality = None
        self.proxy_owner = uuid.uuid4().hex
        self.save_state = None
        self.fast = True
        shader=context.space_data.shading
        self.material_settings={key:getattr(shader,key) for key in (
            'use_scene_lights','use_scene_world','studio_light','studiolight_rotate_z',
            'studiolight_intensity','studiolight_background_alpha',
            'studiolight_background_blur','use_studiolight_view_rotation')}
        if shader.type not in {'MATERIAL','RENDERED'}:
            self.material_settings.update(use_scene_lights=True,use_scene_world=True,studio_light='forest.exr')

    start = _layout.start
    quality_settings = _layout.quality_settings
    set_quality = _layout.set_quality
    apply_quality = _layout.apply_quality
    prepare = _layout.prepare
    _prepare = _layout._prepare
    change_view = _layout.change_view
    first_paint = _layout.first_paint

    release_proxy = _projection.release_proxy
    sync = _projection.sync

    alive_window = _lifecycle.alive_window
    clear_views = _lifecycle.clear_views
    suspend_for_save = _lifecycle.suspend_for_save
    resume_after_save = _lifecycle.resume_after_save
    playback_context = _lifecycle.playback_context
    lifecycle_event = _lifecycle.lifecycle_event
    close = _lifecycle.close
    restore_quality = _lifecycle.restore_quality
    restore_source_shading = _lifecycle.restore_source_shading
    close_window = _lifecycle.close_window
    resume_after_close = _lifecycle.resume_after_close

    draw = _drawing.draw
    draw_diagram = _drawing.draw_diagram


@contextmanager
def capture_quality(scene):
    """Keep PNG sampling independent of the temporary live preview budget."""
    session = _ACTIVE
    if session is None or session.scene != scene or session.preview_quality is None:
        yield
        return
    current = session.quality_settings()
    try:
        session.set_quality(session.preview_quality)
        yield
    finally:
        session.set_quality(current)


def shutdown(preserve_playback=True):
    global _ACTIVE
    if _ACTIVE:
        session = _ACTIVE
        # Keep ownership until every retained resource is released. A failed
        # restoration can then be retried without resuming a half-closed view.
        if session.close(preserve_playback=preserve_playback) and _ACTIVE is session:
            _ACTIVE = None


def watch():
    if _ACTIVE:
        try:
            if _ACTIVE.closing or _ACTIVE.alive_window() is None:
                shutdown()
            elif _ACTIVE.window.scene!=_ACTIVE.scene:
                shutdown(preserve_playback=False)
            else:_ACTIVE.sync()
        except Exception as exc:
            print('Native camera view closed:',str(exc))
            _remember_error(_ACTIVE.scene, f'相机观察中断：{exc}')
            shutdown()
    return .25 if _ACTIVE is not None else None


def _playback_control_state(scene):
    """Protect another or unknown owner even when global screen flags disagree."""
    from . import playback
    owner = playback.owner_scene()
    if owner is not None and owner != scene:
        return False, f'播放器由场景“{owner.name}”占用，请先在对应窗口暂停'
    playing = playback.any_playing()
    if playing and owner is None:
        return False, '无法确认当前播放器归属，请先在播放窗口暂停'
    return playing, ''


def header(self,context):
    if _ACTIVE and not _ACTIVE.closing and context.window==_ACTIVE.window:
        row=self.layout.row(align=True)
        entry=next((e for e in _ACTIVE.entries if e['area']==context.area),None)
        playing, blocked = _playback_control_state(_ACTIVE.scene)
        controls_enabled = WFRL_OT_NativePlay.poll(context) and not blocked
        play = row.row(align=True)
        play.enabled = controls_enabled
        play.operator('wfrl.native_camera_play',text='播放器占用中' if blocked else '暂停' if playing else '播放',icon='PAUSE' if playing else 'PLAY')
        step=row.row(align=True)
        step.enabled=controls_enabled
        step.operator('wfrl.farm_transport',text='单步',icon='NEXT_KEYFRAME').action='STEP'
        if entry and len(_ACTIVE.slots)>1:
            op=row.operator('wfrl.native_camera_focus',text=f"放大 C{entry['slot']}",icon='FULLSCREEN_ENTER')
            op.slot=entry['slot']
        elif len(_ACTIVE.slots)==1:
            op=row.operator('wfrl.native_camera_view',text='返回三路',icon='FULLSCREEN_EXIT')
            op.mode='TRIPLE'
            op.layout=_ACTIVE.layout
        op=row.operator('wfrl.native_camera_view',text='2×2' if _ACTIVE.layout=='STRIP' else '三列')
        op.mode='TRIPLE'
        op.layout='GRID' if _ACTIVE.layout=='STRIP' else 'STRIP'
        if len(_ACTIVE.slots)>1:
            row.operator('wfrl.native_camera_quality',text='流畅' if _ACTIVE.fast else '高清')
        row.operator('wfrl.native_camera_exit',text='',icon='X')


class WFRL_OT_NativeCamera(bpy.types.Operator):
    bl_idname='wfrl.native_camera_view'
    bl_label='原生相机观察'
    mode:bpy.props.EnumProperty(items=[('WATCH','单路',''),('TRIPLE','三路','')])
    layout:bpy.props.EnumProperty(items=[('GRID','2×2 展示','三路相机与静态安装示意'),
                                         ('STRIP','三列对照','从叶根到叶尖横向比较')],default='GRID')
    @classmethod
    def poll(cls,context):
        return bool(context.window and context.area
                    and (context.area.type=='VIEW_3D' or (_ACTIVE and context.window==_ACTIVE.window))
                    and core.is_available(context.scene))

    def execute(self,context):
        global _ACTIVE
        from .panels import custom_cameras as panel
        from . import custom_camera_capture
        if custom_camera_capture.active() or (panel._ACTIVE and panel._ACTIVE.stage not in panel._VIEWING):
            self.report({'WARNING'},'请先完成采集或确认相机草稿')
            return {'CANCELLED'}
        if not core.is_available(context.scene):return {'CANCELLED'}
        slot=context.window_manager.wfrl_custom_slot
        if self.mode=='WATCH' and not core.get_camera(context.scene,slot):return {'CANCELLED'}
        if panel._ACTIVE:panel._ACTIVE.finish()
        slots = list(core.SLOTS) if self.mode == 'TRIPLE' else [slot]
        if _ACTIVE and _ACTIVE.closing:
            shutdown()
        if _ACTIVE and _ACTIVE.scene == context.scene:
            try:
                _ACTIVE.change_view(slots, self.layout if self.mode=='TRIPLE' else None)
                return {'FINISHED'}
            except (ValueError, RuntimeError) as exc:
                _remember_error(context.scene, f'切换相机观察失败：{exc}')
                self.report({'ERROR'},str(exc))
                return {'CANCELLED'}
        shutdown()
        if _ACTIVE:
            self.report({'ERROR'}, last_error(context.scene) or '相机窗口正在恢复，请稍后重开')
            return {'CANCELLED'}
        session = None
        try:
            session=NativeSession(context, slots, self.layout)
            _ACTIVE=session
            session.start()
            if not bpy.app.timers.is_registered(watch):bpy.app.timers.register(watch,first_interval=.25)
            return {'FINISHED'}
        except Exception as exc:
            _remember_error(context.scene, f'打开相机观察失败：{exc}')
            if session is not None:
                shutdown()
            self.report({'ERROR'},str(exc))
            return {'CANCELLED'}


class WFRL_OT_NativeFocus(bpy.types.Operator):
    bl_idname='wfrl.native_camera_focus'
    bl_label='放大此路相机'
    bl_description='保留相机、共同时间与播放状态；返回后仍显示当前时刻'
    slot:bpy.props.IntProperty(min=1,max=3,default=1)
    @classmethod
    def poll(cls,context):
        from . import custom_camera_capture
        return bool(_ACTIVE and context.scene == _ACTIVE.scene and not _ACTIVE.closing and not _ACTIVE.preparing and not custom_camera_capture.active())
    def execute(self,context):
        if not self.poll(context):return {'CANCELLED'}
        try:
            _ACTIVE.change_view([self.slot])
            return {'FINISHED'}
        except (ValueError,RuntimeError) as exc:
            _remember_error(context.scene, f'切换相机观察失败：{exc}')
            self.report({'ERROR'},str(exc))
            return {'CANCELLED'}


class WFRL_OT_NativePlay(bpy.types.Operator):
    bl_idname='wfrl.native_camera_play'
    bl_label='播放 / 暂停共同场景'
    bl_description='由主窗口维护共同回放时钟，关闭观察窗口后继续保持播放状态'
    @classmethod
    def poll(cls,context):
        from . import custom_camera_capture
        # Keep Space dispatch here so an occupied player cannot fall through
        # to Blender's global playback shortcut. The header disables its button.
        return bool(_ACTIVE and not _ACTIVE.closing and context.window==_ACTIVE.window and not custom_camera_capture.active())
    def execute(self,context):
        if not WFRL_OT_NativePlay.poll(context):return {'CANCELLED'}
        session = _ACTIVE
        override=session.playback_context()
        if override is None:return {'CANCELLED'}
        with context.temp_override(**override):
            playing, blocked = _playback_control_state(session.scene)
            if blocked:
                self.report({'WARNING'}, blocked)
                return {'CANCELLED'}
            if playing:
                bpy.ops.screen.animation_cancel(restore_frame=False)
            else:
                from . import clearance_replay
                if clearance_replay.reader_for(session.scene) is not None:
                    return bpy.ops.wfrl.clearance_playback()
                bpy.ops.screen.animation_play()
        return {'FINISHED'}


class WFRL_OT_NativeQuality(bpy.types.Operator):
    bl_idname='wfrl.native_camera_quality'
    bl_label='切换预览画质'
    bl_description='三路切换流畅与高清；原图采集保持原始画质'
    @classmethod
    def poll(cls,context):
        return bool(_ACTIVE and context.scene == _ACTIVE.scene and not _ACTIVE.closing and len(_ACTIVE.slots)>1 and not _ACTIVE.preparing)
    def execute(self,context):
        if not self.poll(context):return {'CANCELLED'}
        _ACTIVE.fast = not _ACTIVE.fast
        _ACTIVE.apply_quality()
        return {'FINISHED'}


class WFRL_OT_NativeExit(bpy.types.Operator):
    bl_idname='wfrl.native_camera_exit'
    bl_label='退出原生相机观察'
    def execute(self,context):
        shutdown()
        return {'FINISHED'}


def register():
    bpy.types.VIEW3D_HT_header.prepend(header)
    bpy.types.IMAGE_HT_header.prepend(header)
    bpy.app.handlers.load_pre.append(on_load)
    bpy.app.handlers.load_post.append(on_loaded)
    bpy.app.handlers.save_pre.append(on_save)
    bpy.app.handlers.save_post.append(on_saved)
    bpy.app.handlers.save_post_fail.append(on_saved)
    config=bpy.context.window_manager.keyconfigs.addon
    if config:
        keymap=config.keymaps.new(name='Frames',space_type='EMPTY')
        item=keymap.keymap_items.new('wfrl.native_camera_play','SPACE','PRESS',head=True)
        _KEYMAPS.append((keymap,item))


@persistent
def on_load(_):shutdown(preserve_playback=False)


@persistent
def on_loaded(_):
    """Discard only explicitly owned observation resources from loaded files."""
    for obj in list(bpy.data.objects):
        owner = obj.get(_PROXY_OWNER)
        if owner and obj.type == 'CAMERA' and obj.data.get(_PROXY_OWNER) == owner:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data.users == 0:
                bpy.data.cameras.remove(data)
    for data in list(bpy.data.cameras):
        if data.get(_PROXY_OWNER) and data.users == 0:
            bpy.data.cameras.remove(data)


def on_history_pre(_):
    """End transient observation before Blender replaces scene RNA."""
    global _HISTORY_QUALITY
    _HISTORY_QUALITY = None
    if _ACTIVE is not None:
        try:
            _HISTORY_QUALITY = (_ACTIVE.scene.session_uid, _ACTIVE.preview_quality)
        except ReferenceError:
            pass
        shutdown(preserve_playback=False)


def on_history_post(_):
    """Restore quality only to the same scene identity in the undo result."""
    global _HISTORY_QUALITY
    snapshot, _HISTORY_QUALITY = _HISTORY_QUALITY, None
    on_loaded(None)
    if snapshot is not None and snapshot[1] is not None:
        scene = next((s for s in bpy.data.scenes if s.session_uid == snapshot[0]), None)
        if scene is not None:
            pixel_size, samples, shadows, gi = snapshot[1]
            scene.render.preview_pixel_size = pixel_size
            scene.eevee.taa_samples = samples
            scene.eevee.use_shadows = shadows
            scene.eevee.use_fast_gi = gi


@persistent
def on_save(_):
    if _ACTIVE:
        try:
            _ACTIVE.suspend_for_save()
        except Exception as exc:
            _remember_error(_ACTIVE.scene, f'相机观察保存准备失败：{exc}')
            # Blender continues writing after handler exceptions. End the
            # failed observation so ordinary cleanup can retry every resource.
            shutdown()


@persistent
def on_saved(_):
    if _ACTIVE:
        try:
            _ACTIVE.resume_after_save()
        except Exception as exc:
            _remember_error(_ACTIVE.scene, f'相机观察保存恢复失败：{exc}')
            shutdown()


def unregister():
    shutdown(preserve_playback=False)
    if bpy.app.timers.is_registered(watch):bpy.app.timers.unregister(watch)
    bpy.types.VIEW3D_HT_header.remove(header)
    bpy.types.IMAGE_HT_header.remove(header)
    if on_load in bpy.app.handlers.load_pre:bpy.app.handlers.load_pre.remove(on_load)
    for handlers, callback in ((bpy.app.handlers.load_post, on_loaded),
                               (bpy.app.handlers.save_pre, on_save),
                               (bpy.app.handlers.save_post, on_saved),
                               (bpy.app.handlers.save_post_fail, on_saved)):
        if callback in handlers:
            handlers.remove(callback)
    for keymap,item in _KEYMAPS:keymap.keymap_items.remove(item)
    _KEYMAPS.clear()


CLASSES=(WFRL_OT_NativeCamera,WFRL_OT_NativeFocus,WFRL_OT_NativePlay,WFRL_OT_NativeQuality,WFRL_OT_NativeExit)
