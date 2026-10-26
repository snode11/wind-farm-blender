"""WFRL task navigation reusing the existing operators and scene clocks."""
from types import SimpleNamespace
import unicodedata

import bpy

from .. import clearance_replay, runtime
from ..home_state import backend_status, scene_status


def snapshot(context):
    from .. import custom_camera_capture, manual_control, native_camera_views, playback
    from . import custom_cameras, stacked_camera_rig, presentation
    scene = context.scene
    ready = clearance_replay.reader_for(scene) is not None
    kind = scene.get('wfrl_scene_kind', '')
    load_error = scene.get('wfrl_home_load_error', '') if kind != 'manual' else ''
    saved_status = scene.get('wfrl_clearance_status', '')
    if not ready and kind != 'manual' and saved_status not in {'', 'READY', 'REVIEW_ONLY'}:
        load_error = load_error or saved_status
    busy = ''
    if custom_camera_capture.active():
        busy = ('相机采集中' if custom_camera_capture._ACTIVE.scene == scene
                else '其他场景正在采集相机图像')
    elif stacked_camera_rig._ACTIVE:
        busy = ('相机盒体安装中' if stacked_camera_rig._ACTIVE.scene == scene
                else '其他场景正在安装相机盒体')
    elif custom_cameras._ACTIVE and custom_cameras._ACTIVE.stage not in custom_cameras._VIEWING:
        busy = ('相机草稿编辑中' if custom_cameras._ACTIVE.scene == scene
                else '其他场景正在编辑相机草稿')
    elif presentation._ACTIVE is not None:
        busy = ('窗口录制中 · Esc 停止' if presentation._ACTIVE._scene == scene
                else '其他场景正在录制窗口')
    manual_error = getattr(scene, 'wfrl_manual_error', '') if kind == 'manual' else ''
    running = kind == 'manual' and any(
        (entry.running and entry.rpm > 0)
        or (manual_control.pitch_ramping(entry) and not entry.pitch_paused)
        for entry in scene.wfrl_manual_turbines)
    status = scene_status(kind=kind, ready=ready, playing=playback.is_playing(scene),
                          load_error=load_error, manual_error=manual_error,
                          manual_running=running, busy=busy)
    issues = [('加载结果', load_error), ('后端', runtime.get_state().error),
              ('手动控制', manual_error), ('相机观察', native_camera_views.last_error(scene))]
    for key, title in (('wfrl_blade_model_error', '叶片外形'),
                       ('wfrl_manual_geometry_error', '手动外形'),
                       ('wfrl_view_layout_error', '视图布局')):
        issues.append((title, scene.get(key, '')))
    op = custom_cameras._ACTIVE
    if op is not None and op.scene == scene:
        issues.append(('相机编辑', getattr(op, 'error', '')))
    placement = stacked_camera_rig._ACTIVE
    if placement is not None and placement.scene == scene:
        issues.append(('相机盒体', placement.transaction.error))
    capture = custom_camera_capture._ACTIVE
    if capture is not None and capture.scene == scene:
        issues.append(('相机采集', getattr(capture, 'error', '')))
    for key, title in (('wfrl_history_export_status', '历史导出'),
                       ('wfrl_capture_status', '窗口录制')):
        detail = scene.get(key, '')
        if detail.startswith('FAILED'):
            issues.append((title, detail))
    return status, busy, [(title, str(detail)) for title, detail in issues if detail]


def message(layout, text, icon='INFO'):
    region = getattr(bpy.context, 'region', None)
    scale = bpy.context.preferences.system.ui_scale
    width = max(18, int((getattr(region, 'width', 320) / max(scale, .5) - 50) / 7))
    line, used, first = '', 0, True
    for char in text:
        size = 2 if unicodedata.east_asian_width(char) in {'W', 'F'} else 1
        if char == '\n' or (line and used + size > width):
            layout.label(text=line, icon=icon if first else 'NONE')
            line, used, first = '', 0, False
        if char != '\n':
            line += char
            used += size
    if line:
        layout.label(text=line, icon=icon if first else 'NONE')


def draw_status(layout, context):
    status, busy, issues = snapshot(context)
    box = layout.box()
    box.label(text=status.title, icon=status.icon)
    message(box, status.next_step)
    message(box, backend_status(runtime.get_state()), 'LINKED' if runtime.get_state().connection == 'CONNECTED' else 'UNLINKED')
    for title, detail in issues:
        error = layout.box()
        error.alert = True
        error.label(text=title, icon='ERROR')
        message(error, detail, 'ERROR')
        error.operator('wfrl.copy_diagnostic', text='复制错误', icon='COPYDOWN').report_text = title + '\n' + detail
    if busy:
        from .. import custom_camera_capture
        if custom_camera_capture.active():
            message(layout, custom_camera_capture.progress())
            layout.operator('wfrl.custom_capture_cancel', text='取消相机采集', icon='CANCEL')
        elif '相机' in busy:
            layout.label(text='在“相机”页确认或取消编辑。', icon='INFO')
    from .. import animation_render_ui
    animation_render_ui.draw(layout, context)
    return busy


def draw_load(layout, context, *, busy=''):
    if context.scene.get('wfrl_scene_kind') == 'manual':
        layout.label(text='先返回原场景，再加载离线结果。', icon='INFO')
        layout.operator('wfrl.manual_leave', text='返回原场景', icon='BACK')
        return
    controls = layout.column(align=True)
    controls.enabled = not busy
    controls.operator('wfrl.load_demo', text='加载 MAPPO 离线回放', icon='FILE_REFRESH')
    controls.operator('wfrl.load_dual_beam', text='加载双束净空 / S1', icon='IMPORT')
    controls.operator('wfrl.load_dual_beam', text='加载 TLS 候选 / S1', icon='IMPORT').builtin_method = 'TLS'
    controls.operator('wfrl.load_dual_beam', text='打开外部双束数据包…', icon='FILE_FOLDER').choose_file = True
    controls.operator('wfrl.split_recon_open_example', text='打开几何重建示例…', icon='FILE_BLEND')
    layout.label(text='保存的仿真结果 · 无需连接后端', icon='INFO')


def draw_export(layout, context, *, busy=''):
    from .. import custom_cameras as core
    scene = context.scene
    if scene.get('wfrl_scene_kind') == 'manual':
        layout.label(text='保存刚性姿态；不包含仿真结果', icon='INFO')
        layout.operator('wfrl.manual_pose_export', text='保存三机姿态 JSON…', icon='EXPORT')
        layout.label(text='Down 视频与冻结试验导出见“手动”页。')
        return
    if clearance_replay.reader_for(scene) is None and scene.get('wfrl_scene_kind') != 'live':
        layout.label(text='请先在“回放”页加载数据。', icon='INFO')
        return
    data = layout.column()
    data.enabled = not busy
    data.operator('wfrl.export_history', text='导出已采样历史…', icon='EXPORT')
    data.label(text='仅导出已采样数据，缺失通道不补值。')
    if scene.get('wfrl_history_export_status'):
        message(layout, scene['wfrl_history_export_status'])
    if core.is_available(scene):
        from .custom_camera_output import WFRL_PT_CustomCapture
        WFRL_PT_CustomCapture.draw(SimpleNamespace(layout=layout), context)
    window = layout.box()
    window.label(text='窗口截图与后台动画')
    window.prop(scene, 'wfrl_capture_directory', text='截图目录')
    window.operator('wfrl.capture_screenshot', text='保存窗口截图', icon='RENDER_STILL')
    animation = window.column()
    animation.enabled = not busy
    animation.operator('wfrl.render_animation', text='后台导出动画 PNG…', icon='RENDER_ANIMATION')
    window.label(text='动画使用当前场景相机和帧范围。')


class WFRL_PT_Home(bpy.types.Panel):
    bl_label = 'WFRL 工作台'
    bl_idname = 'WFRL_PT_home'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'WFRL'
    bl_order = -400

    def draw(self, context):
        from .. import farm_flex
        layout, scene = self.layout, context.scene
        layout.use_property_decorate = False
        busy = draw_status(layout, context)
        layout.prop(context.window_manager, 'wfrl_home_page', expand=True)
        page = context.window_manager.wfrl_home_page
        if page == 'REPLAY':
            if farm_flex.is_active(scene):
                from .farm_replay import draw
                controls = layout.column()
                controls.enabled = not busy
                draw(controls, context)
            elif clearance_replay.reader_for(scene) is not None:
                from .clearance import draw, playback_control
                controls = layout.column()
                controls.enabled = not busy
                text, icon, _state = playback_control(scene, context.screen)
                controls.operator('wfrl.clearance_playback', text=text, icon=icon)
                draw(controls, scene)
            else:
                draw_load(layout, context, busy=busy)
        elif page == 'CAMERA':
            if scene.get('wfrl_scene_kind') == 'manual':
                layout.label(text='手动场景使用“手动”页的 Down 相机。', icon='INFO')
            else:
                from .custom_cameras import WFRL_PT_CustomCameras
                WFRL_PT_CustomCameras.draw(self, context)
        elif page == 'MANUAL':
            from .manual_control import WFRL_PT_ManualControl
            controls = layout.column()
            controls.enabled = not busy
            WFRL_PT_ManualControl.draw(SimpleNamespace(layout=controls), context)
        elif page == 'EXPORT':
            draw_export(layout, context, busy=busy)
        else:
            from .status import WFRL_PT_connection
            from .scene import WFRL_PT_WorkflowScene
            from .run import WFRL_PT_WorkflowRun
            if scene.get('wfrl_scene_kind') == 'manual':
                layout.label(text='先返回原场景，再连接后端。', icon='INFO')
                layout.operator('wfrl.manual_leave', text='返回原场景', icon='BACK')
            else:
                controls = layout.column()
                controls.enabled = not busy
                WFRL_PT_connection.draw(SimpleNamespace(layout=controls), context)
                if WFRL_PT_WorkflowRun.poll(context):
                    WFRL_PT_WorkflowScene.draw(self, context)
                    WFRL_PT_WorkflowRun.draw(self, context)


CLASSES = (WFRL_PT_Home,)


def register_properties():
    bpy.types.WindowManager.wfrl_home_page = bpy.props.EnumProperty(
        name='工作台', items=(('REPLAY', '回放', '加载并查看保存的仿真结果'),
                              ('CAMERA', '相机', '观察、安装与编辑相机'),
                              ('MANUAL', '手动', '独立的刚性运动与姿态控制'),
                              ('EXPORT', '导出', '保存已采样数据、相机图像与布局'),
                              ('BACKEND', '后端', '连接、配置与运行真实后端')),
        default='REPLAY', options={'SKIP_SAVE'})


def unregister_properties():
    if hasattr(bpy.types.WindowManager, 'wfrl_home_page'):
        del bpy.types.WindowManager.wfrl_home_page
