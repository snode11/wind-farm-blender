"""Compact controls and truthful timestamps for saved reconstruction comparison."""
from pathlib import Path

import bpy
import blf
from bpy.app.handlers import persistent
from bpy.props import IntProperty

from .split_reconstruction_timing import spec_from_scene, step_sample_frame, timing_for_frame


_RUNTIME_KEY = 'wfrl_split_reconstruction_ui'
_HUD_HANDLE = None


def packaged_example_path(package_root=None):
    """Return the example inside this installed extension; allow isolated tests."""
    root = Path(package_root) if package_root is not None else Path(__file__).resolve().parent
    return root / 'assets' / 'examples' / 'mappo_reconstruction_split.blend'


def _core():
    # The layout module and the package registration may both import this UI.
    from . import split_reconstruction
    return split_reconstruction


def _frame(scene):
    return scene.frame_current + scene.frame_subframe


def _timing(scene):
    spec = spec_from_scene(scene)
    return spec, timing_for_frame(_frame(scene), spec)


def _age_text(value):
    return f'{value:.1f}'.rstrip('0').rstrip('.')


def _pause_all_playback(context):
    for window in context.window_manager.windows:
        if window.scene != context.scene:
            continue
        screen = window.screen
        if screen is not None and screen.is_animation_playing:
            with context.temp_override(window=window, screen=screen):
                bpy.ops.screen.animation_cancel(restore_frame=False)


def _redraw(context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {'VIEW_3D', 'DOPESHEET_EDITOR'}:
                area.tag_redraw()


class WFRL_OT_SplitReconOpenExample(bpy.types.Operator):
    bl_idname = 'wfrl.split_recon_open_example'
    bl_label = 'MAPPO＋重建示例'
    bl_description = '打开扩展自带的同步分屏示例；当前工作未保存时由 Blender 提示保存'

    def execute(self, context):
        if bpy.app.version < (5, 2, 0):
            self.report({'ERROR'}, 'MAPPO＋重建示例需要 Blender 5.2 或更新版本')
            return {'CANCELLED'}
        if bpy.app.background or context.window is None:
            self.report({'ERROR'}, '请在 Blender 窗口中打开示例，以显示当前文件的保存确认')
            return {'CANCELLED'}
        path = packaged_example_path()
        if not path.is_file():
            self.report({'ERROR'}, '安装包缺少 assets/examples/mappo_reconstruction_split.blend；请重新安装含示例的最新 ZIP')
            return {'CANCELLED'}
        try:
            # INVOKE preserves Blender's standard unsaved-work dialog. Never
            # fall back to EXEC_DEFAULT, which would bypass that confirmation.
            result = bpy.ops.wm.open_mainfile(
                'INVOKE_DEFAULT', filepath=str(path), display_file_selector=False,
                load_ui=True, use_scripts=False,
            )
        except (OSError, RuntimeError, TypeError) as exc:
            self.report({'ERROR'}, '无法打开打包示例：' + str(exc))
            return {'CANCELLED'}
        # The native open-file operator owns any confirmation/modal handler.
        return {'CANCELLED'} if 'CANCELLED' in result else {'FINISHED'}


class WFRL_OT_SplitReconStep(bpy.types.Operator):
    bl_idname = 'wfrl.split_recon_step'
    bl_label = '切换重建采样'
    bl_description = '暂停播放并跳到前一个或后一个实际保存的重建样本；不生成插值观测'
    bl_options = {'REGISTER'}

    direction: IntProperty(default=1, min=-1, max=1, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.scene is not None and _core().enabled(context.scene)

    def execute(self, context):
        try:
            spec = spec_from_scene(context.scene)
            _pause_all_playback(context)
            destination = step_sample_frame(_frame(context.scene), self.direction, spec)
            context.scene.frame_set(destination, subframe=0.0)
            _redraw(context)
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_SplitReconEnter(bpy.types.Operator):
    bl_idname = 'wfrl.split_recon_enter'
    bl_label = '进入重建对照'
    bl_description = '恢复原 MAPPO 场景与保存重建的共享时间轴分屏'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and _core().available(context.scene)

    def execute(self, context):
        try:
            _core().enter(context)
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_SplitReconExit(bpy.types.Operator):
    bl_idname = 'wfrl.split_recon_exit'
    bl_label = '退出重建对照'
    bl_description = '返回普通 MAPPO 视图，保留重建数据和当前时间，之后可重新进入'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and _core().enabled(context.scene)

    def execute(self, context):
        try:
            _core().exit_mode(context)
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_SplitReconFit(bpy.types.Operator):
    bl_idname = 'wfrl.split_recon_fit'
    bl_label = '完整取景'
    bl_description = '重新装下两侧对照对象，不修改重建几何或观测数据'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and _core().enabled(context.scene)

    def execute(self, context):
        try:
            _core().fit(context)
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


_CLASSES = (WFRL_OT_SplitReconOpenExample, WFRL_OT_SplitReconStep, WFRL_OT_SplitReconEnter,
            WFRL_OT_SplitReconExit, WFRL_OT_SplitReconFit)


def draw_controls(layout, context):
    """Embed the comparison controls in the existing MAPPO sidebar."""
    scene = context.scene
    if not _core().available(scene):
        return
    box = layout.box()
    if not _core().enabled(scene):
        box.operator('wfrl.split_recon_enter', text='进入重建对照', icon='MOD_MIRROR')
        return
    box.label(text='重建对照 · 共享时间轴')
    try:
        spec, value = _timing(scene)
        if value.in_range:
            box.label(text=f'播放 {value.timeline_time_s:.3f} s')
            box.label(text=f'重建 {value.sample_time_s:.3f} s · 保持 {_age_text(value.age_ms)} ms')
            box.label(text=f'样本 {value.index + 1}/{spec.samples} · {spec.sampling_hz:g} Hz · 精度未验收')
        else:
            box.label(text='时间轴超出保存片段', icon='ERROR')
    except ValueError:
        box.label(text='重建时间元数据无效', icon='ERROR')
    row = box.row(align=True)
    row.operator('wfrl.split_recon_step', text='上一样本', icon='PREV_KEYFRAME').direction = -1
    row.operator('wfrl.split_recon_step', text='下一样本', icon='NEXT_KEYFRAME').direction = 1
    row = box.row(align=True)
    row.operator('wfrl.split_recon_fit', text='完整取景', icon='VIEW_ZOOM')
    row.operator('wfrl.split_recon_enter', text='恢复布局', icon='MOD_MIRROR')
    box.operator('wfrl.split_recon_exit', text='退出对照', icon='LOOP_BACK')
    status = str(scene.get('split_review_status', ''))
    if status == 'PREPARING':
        box.label(text='正在恢复对照布局…', icon='TIME')
    elif status.startswith('ERROR'):
        box.label(text='对照布局未就绪', icon='ERROR')


def _draw_header(self, context):
    scene, space = context.scene, context.space_data
    if scene is None or space is None or space.type != 'VIEW_3D' or not _core().enabled(scene):
        return
    right = _core().is_reconstruction_view(scene, space)
    self.layout.separator()
    self.layout.label(text='三维重建回放' if right else 'MAPPO Demo')
    if right:
        self.layout.operator('wfrl.split_recon_fit', text='', icon='VIEW_ZOOM')


def _draw_hud():
    context = bpy.context
    scene, space, region = context.scene, context.space_data, context.region
    if (scene is None or space is None or space.type != 'VIEW_3D' or region is None
            or not _core().enabled(scene) or not _core().is_reconstruction_view(scene, space)):
        return
    scale = context.preferences.system.ui_scale
    x, top = 14 * scale, region.height - 58 * scale

    def line(text, y, color=(.94, .96, 1., 1.)):
        blf.size(0, 11 * scale)
        # Keep the HUD compact even if the user narrows the right viewport.
        width = max(0, region.width - 28 * scale)
        if blf.dimensions(0, text)[0] > width:
            while text and blf.dimensions(0, text + '…')[0] > width:
                text = text[:-1]
            text += '…'
        blf.position(0, x, y, 0)
        blf.color(0, *color)
        blf.enable(0, blf.SHADOW)
        try:
            blf.shadow(0, 5, 0., 0., 0., .8)
            blf.draw(0, text)
        finally:
            blf.disable(0, blf.SHADOW)

    try:
        spec, value = _timing(scene)
        if value.in_range:
            line(f'播放 {value.timeline_time_s:.3f} s', top)
            line(f'重建样本 {value.sample_time_s:.3f} s · 保持 {_age_text(value.age_ms)} ms', top - 17 * scale)
        else:
            line('时间轴超出保存片段', top, (1., .68, .3, 1.))
        line(f'{spec.sampling_hz:g} Hz 保存结果 · 精度未验收', top - 34 * scale)
    except ValueError:
        line('重建时间元数据无效', top, (1., .68, .3, 1.))
    line('绿：两端截面受约束   橙：模型推断', 18 * scale, (.85, .88, .92, 1.))


def _no_legacy_hud():
    """Inert replacement for the untracked HUD in the original one-off script."""


def _remove_headers():
    for function in tuple(getattr(bpy.types.VIEW3D_HT_header.draw, '_draw_funcs', ())):
        ours = getattr(function, '_wfrl_split_reconstruction_ui', False)
        code = getattr(function, '__code__', None)
        legacy = (getattr(function, '__name__', '') == 'split_header' and code is not None
                  and code.co_filename.endswith('restore_split_headers.py'))
        if legacy:
            # That script discarded its draw-handler handle. Disable only its
            # identified callback; new managed handlers always retain a handle.
            old_hud = function.__globals__.get('split_hud')
            if callable(old_hud) and not getattr(old_hud, '__closure__', None):
                old_hud.__code__ = _no_legacy_hud.__code__
        if ours or legacy:
            try:
                bpy.types.VIEW3D_HT_header.remove(function)
            except (ValueError, RuntimeError):
                pass


_draw_header._wfrl_split_reconstruction_ui = True


def _remove_draw_callbacks():
    global _HUD_HANDLE
    stored = bpy.app.driver_namespace.pop(_RUNTIME_KEY, None) or {}
    handles = [_HUD_HANDLE]
    old_handle = stored.get('handle')
    if old_handle is not _HUD_HANDLE:
        handles.append(old_handle)
    for handle in handles:
        if handle is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
            except (ValueError, RuntimeError, ReferenceError):
                pass
    _HUD_HANDLE = None
    _remove_headers()


def _install_draw_callbacks():
    global _HUD_HANDLE
    _remove_draw_callbacks()
    bpy.types.VIEW3D_HT_header.append(_draw_header)
    if not bpy.app.background:
        _HUD_HANDLE = bpy.types.SpaceView3D.draw_handler_add(_draw_hud, (), 'WINDOW', 'POST_PIXEL')
    bpy.app.driver_namespace[_RUNTIME_KEY] = {'handle': _HUD_HANDLE}


@persistent
def _on_load(_unused):
    _install_draw_callbacks()


_on_load._wfrl_split_reconstruction_ui = True


def _registered_operator(cls):
    # Blender exposes operators by their normalized RNA identifier, not their
    # Python class name. Resolve the registry so module reloads find old classes.
    namespace, name = cls.bl_idname.split('.', 1)
    identifier = f'{namespace.upper()}_OT_{name}'
    registered = bpy.types.Operator.bl_rna_get_subclass_py(identifier, None)
    return registered if registered is not None else (cls if cls.is_registered else None)


def register():
    for handler in tuple(bpy.app.handlers.load_post):
        if getattr(handler, '_wfrl_split_reconstruction_ui', False):
            bpy.app.handlers.load_post.remove(handler)
    for cls in _CLASSES:
        old = _registered_operator(cls)
        if old is not None:
            bpy.utils.unregister_class(old)
        bpy.utils.register_class(cls)
    _install_draw_callbacks()
    bpy.app.handlers.load_post.append(_on_load)


def unregister():
    for handler in tuple(bpy.app.handlers.load_post):
        if getattr(handler, '_wfrl_split_reconstruction_ui', False):
            bpy.app.handlers.load_post.remove(handler)
    _remove_draw_callbacks()
    for cls in reversed(_CLASSES):
        old = _registered_operator(cls)
        if old is not None:
            bpy.utils.unregister_class(old)
