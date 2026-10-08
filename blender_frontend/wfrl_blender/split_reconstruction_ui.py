"""Compact controls and truthful timestamps for saved reconstruction comparison."""
from pathlib import Path

import bpy
import blf
from bpy.app.handlers import persistent
from bpy.props import IntProperty, EnumProperty

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
    bl_label = '打开几何重建示例…'
    bl_description = '打开扩展自带的原601样本几何分屏文件；当前工作未保存时由 Blender 提示保存'

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
            _pause_all_playback(context)
            texture = _core().texture_adapter(context.scene)
            if texture.active(context.scene):
                texture.step(context.scene, self.direction)
                pair = _core()._pair(context.scene, context.window.screen)
                if pair and context.scene.get(texture.DETAIL):
                    texture.focus(context, pair[1])
            else:
                spec = spec_from_scene(context.scene)
                destination = step_sample_frame(_frame(context.scene), self.direction, spec)
                context.scene.frame_set(destination, subframe=0.0)
            _redraw(context)
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_SplitReconEnter(bpy.types.Operator):
    bl_idname = 'wfrl.split_recon_enter'
    bl_label = '独立合成纹理样例'
    bl_description = '左侧保留 MAPPO，右侧直接检查指定合成样例的表面纹理；两者来源与时钟独立'
    bl_options = {'REGISTER'}

    mode: EnumProperty(items=(('TEXTURE', '纹理对照', '在当前场景载入独立合成纹理样例'),
                              ('CURRENT', '当前对照', '保留当前几何或纹理数据，仅恢复对照布局')),
                       default='TEXTURE', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.scene is not None and _core().available(context.scene)

    def execute(self, context):
        try:
            _core().enter(context, use_texture=True if self.mode == 'TEXTURE' else None)
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_SplitReconSameSource(bpy.types.Operator):
    bl_idname = 'wfrl.split_recon_same_source'
    bl_label = '同源纹理同步对照'
    bl_description = '左侧 MAPPO 与右侧同源保存重建按117–177秒共享播放、暂停和跳转；纹理由两路原图离线融合'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and _core().available(context.scene)

    def execute(self, context):
        try:
            _core().enter(context, use_same_source=True)
        except (ValueError, RuntimeError, OSError) as exc:
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


class WFRL_OT_SplitReconTextureToggle(bpy.types.Operator):
    bl_idname = 'wfrl.split_recon_texture_toggle'
    bl_label = '原始 / 增强纹理'
    bl_description = '一键对照原始纹理和亮暗增强；保留原始图像、UV、几何和观测证据'

    @classmethod
    def poll(cls, context):
        return _core().enabled(context.scene) and _core().texture_adapter(context.scene).active(context.scene)

    def execute(self, context):
        try:
            _core().texture_adapter(context.scene).toggle(context.scene)
            _redraw(context)
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_SplitReconInspect(bpy.types.Operator):
    bl_idname = 'wfrl.split_recon_inspect'
    bl_label = '局部纹理检查'
    bl_description = '放大右侧手动选取的表面纹理位置；同源模式随旋转追踪，此操作不识别缺陷'
    point: EnumProperty(items=(('CURRENT', '当前位置', ''), ('BRIGHT', '亮斑', ''),
                               ('HORIZONTAL', '横细线', ''), ('VERTICAL', '纵细线', ''),
                               ('SUPPORTED', '中段观察点', ''), ('TIP', '叶尖观察点', '')),
                        default='CURRENT', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return _core().enabled(context.scene) and _core().texture_adapter(context.scene).active(context.scene)

    def execute(self, context):
        try:
            pair = _core()._pair(context.scene, context.window.screen)
            if pair is None or _core()._key(context.window) in _core()._JOBS:
                raise ValueError('请等待布局就绪，或点击恢复布局')
            _pause_all_playback(context)
            _core().texture_adapter(context.scene).focus(context, pair[1], None if self.point == 'CURRENT' else self.point)
            _redraw(context)
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


_CLASSES = (WFRL_OT_SplitReconOpenExample, WFRL_OT_SplitReconStep, WFRL_OT_SplitReconEnter, WFRL_OT_SplitReconSameSource,
            WFRL_OT_SplitReconExit, WFRL_OT_SplitReconFit, WFRL_OT_SplitReconTextureToggle,
            WFRL_OT_SplitReconInspect)


def draw_controls(layout, context):
    """Embed the comparison controls in the existing MAPPO sidebar."""
    scene = context.scene
    if not _core().available(scene):
        return
    box = layout.box()
    texture = _core().texture_adapter(scene)
    from . import split_mappo_texture
    synchronized = split_mappo_texture.active(scene)
    if not _core().enabled(scene):
        if synchronized:
            box.label(text='同源纹理 · 共享时间轴')
            box.operator('wfrl.split_recon_enter', text='返回同源同步对照', icon='MOD_MIRROR').mode = 'CURRENT'
        elif _core()._legacy_available(scene) and not texture.active(scene):
            box.label(text='原几何示例 · 共享时间轴')
            box.operator('wfrl.split_recon_enter', text='返回几何对照', icon='MOD_MIRROR').mode = 'CURRENT'
        else:
            box.operator('wfrl.split_recon_enter', text='返回独立合成纹理样例', icon='SHADING_TEXTURE').mode = 'CURRENT'
        box.operator('wfrl.split_recon_same_source', text='同源纹理同步对照', icon='SHADING_TEXTURE')
        box.operator('wfrl.split_recon_enter', text='独立合成纹理样例', icon='SHADING_TEXTURE')
        return
    textured = texture.active(scene)
    if synchronized:
        box.label(text='同源纹理同步对照')
        try:
            value = texture.information(scene)
            if value is None:
                box.label(text='正在恢复同源纹理…', icon='TIME')
            elif value['in_range']:
                box.label(text=f"播放 {value['timeline_time_s']:.3f} s")
                box.label(text=f"重建 {value['sample_time_s']:.3f} s · 保持 {_age_text(value['age_ms'])} ms")
                box.label(text=f"样本 {value['index']+1}/601 · 10 Hz · 精度未验收")
            else:
                box.label(text='时间轴超出保存片段', icon='ERROR')
        except ValueError:
            box.label(text='同源纹理数据恢复失败', icon='ERROR')
        box.label(text='两路原图固定灰度图集 · 无观测处保留约束色')
        box.operator('wfrl.split_recon_inspect', text='跟随选中表面近景', icon='VIEW_ZOOM').point = 'CURRENT'
        row = box.row(align=True)
        for point, (label, _blade, _radius, _tau, _size) in split_mappo_texture.POINTS.items():
            row.operator('wfrl.split_recon_inspect', text=label).point = point
        row = box.row(align=True)
        row.prop(scene, 'wfrl_recon_inspect_blade', text='叶片')
        row.prop(scene, 'wfrl_recon_inspect_radius', text='半径')
        row = box.row(align=True)
        row.prop(scene, 'wfrl_recon_inspect_tau', text='周向 UV')
        row.prop(scene, 'wfrl_recon_inspect_size', text='范围')
        original = scene.wfrl_recon_texture_mode == 'ORIGINAL'
        box.operator('wfrl.split_recon_texture_toggle',
            text='原始纹理 → 对比增强' if original else '对比增强 → 原始纹理', icon='SHADING_TEXTURE')
        box.operator('wfrl.split_recon_enter', text='独立合成纹理样例', icon='SHADING_TEXTURE')
    elif textured:
        from . import split_surface_texture
        box.label(text='MAPPO＋细节表面纹理')
        try:
            value = texture.information(scene)
            if value is None:
                box.label(text='正在恢复表面纹理…', icon='TIME')
            else:
                box.label(text=f"合成纹理 {value['time_s']:.3f} s · 样本 {value['index']+1}/{value['samples']}")
                box.label(text=f"右侧 {value['fps']:g} Hz · 独立时钟 / 精度未验收")
        except ValueError:
            box.label(text='表面纹理数据恢复失败', icon='ERROR')
        box.label(text='右侧为合成样例，非左侧 MAPPO 同源重建')
        original = scene.wfrl_recon_texture_mode == 'ORIGINAL'
        box.operator('wfrl.split_recon_texture_toggle',
                     text='原始纹理 → 增强显示' if original else '增强显示 → 原始纹理', icon='SHADING_TEXTURE')
        row = box.row(align=True)
        for point, (label, _radius, _tau) in split_surface_texture.POINTS.items():
            row.operator('wfrl.split_recon_inspect', text=label).point = point
    else:
        box.label(text='原几何示例 · 共享时间轴')
        box.operator('wfrl.split_recon_same_source', text='同源纹理同步对照', icon='SHADING_TEXTURE')
        box.operator('wfrl.split_recon_enter', text='独立合成纹理样例', icon='SHADING_TEXTURE')
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
    row.operator('wfrl.split_recon_enter', text='恢复布局', icon='MOD_MIRROR').mode = 'CURRENT'
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
    from . import split_surface_texture, split_mappo_texture
    title = ('同源纹理 · 同步回放' if split_mappo_texture.active(scene) else
             '细节表面纹理 · 合成样例' if split_surface_texture.active(scene) else '三维重建回放')
    self.layout.label(text=title
                           if right else 'MAPPO Demo')
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

    from . import split_surface_texture, split_mappo_texture
    if split_mappo_texture.active(scene):
        value = split_mappo_texture.information(scene)
        if value is None:
            line('正在恢复同源纹理…', top)
        elif value['in_range']:
            line(f"播放 {value['timeline_time_s']:.3f} s · 同源 MAPPO", top)
            line(f"重建 {value['sample_time_s']:.3f} s · 样本 {value['index']+1}/601", top-17*scale)
            line(f"10 Hz 保存 / 保持 {_age_text(value['age_ms'])} ms · 固定灰度图集", top-34*scale)
        else:
            line('时间轴超出同源保存片段', top, (1.,.68,.3,1.))
        line('绿：截面受图像约束   橙：模型推断 · 均为估计', 35*scale, (.85,.88,.92,1.))
        line('无观测处保留约束色 · 重建精度未验收', 18*scale, (.85,.88,.92,1.))
        return
    if split_surface_texture.active(scene):
        try:
            value = split_surface_texture.information(scene)
            if value is None:
                line('正在恢复表面纹理…', top)
            else:
                line(f"合成纹理 {value['time_s']:.3f} s · 样本 {value['index']+1}/{value['samples']}", top)
            line('独立样例与时钟 · 非 MAPPO 同源重建', top - 17 * scale)
            label = '原始纹理' if scene.wfrl_recon_texture_mode == 'ORIGINAL' else '亮暗增强：亮青 / 暗洋红'
            line(label, top - 34 * scale)
        except ValueError:
            line('表面纹理数据恢复失败', top, (1., .68, .3, 1.))
        line('手动纹理位置 · 外观显示 / 精度未验收', 18 * scale, (.85, .88, .92, 1.))
        return
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
    line('绿：两端截面受约束   橙：模型推断 · 均为估计', 18 * scale, (.85, .88, .92, 1.))


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
