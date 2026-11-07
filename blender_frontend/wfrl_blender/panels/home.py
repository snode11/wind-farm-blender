"""WFRL task navigation reusing the existing operators and scene clocks."""
from types import SimpleNamespace
import unicodedata

import bpy

from .. import clearance_replay, runtime
from ..home_state import backend_status, scene_status, foreground_tasks, can_start, stage_label


def busy_reason(scene):
    """Name the camera or recording task that locks workbench controls, or return ''."""
    return can_start('replace_scene', scene)[1]


def snapshot(context):
    from .. import manual_control, native_camera_views, playback
    scene = context.scene
    ready = clearance_replay.reader_for(scene) is not None
    kind = scene.get('wfrl_scene_kind', '')
    load_error = scene.get('wfrl_home_load_error', '') if kind != 'manual' else ''
    saved_status = scene.get('wfrl_clearance_status', '')
    if not ready and kind != 'manual' and saved_status not in {'', 'READY', 'REVIEW_ONLY'}:
        load_error = load_error or saved_status
    busy = busy_reason(scene)
    manual_error = getattr(scene, 'wfrl_manual_error', '') if kind == 'manual' else ''
    running = kind == 'manual' and any(
        (entry.running and entry.rpm > 0)
        or manual_control.pitch_ramping(entry)
        for entry in scene.wfrl_manual_turbines)
    status = scene_status(kind=kind, ready=ready, playing=playback.is_playing(scene),
                          load_error=load_error, manual_error=manual_error,
                          manual_running=running, busy=busy)
    issues = [('加载结果', load_error), ('后端', runtime.get_state().error),
              ('手动控制', manual_error), ('相机观察', native_camera_views.last_error(scene))]
    for key, title in (('wfrl_blade_model_error', '叶片外形'),
                       ('wfrl_view_layout_error', '视图布局')):
        issues.append((title, scene.get(key, '')))
    for task in foreground_tasks(scene):
        if task.owned and task.error:
            issues.append(({'camera': '相机编辑', 'mount': '相机盒体',
                            'capture': '相机采集', 'recording': '窗口录制'}[task.kind], task.error))
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
    offline = (clearance_replay.reader_for(context.scene) is not None
               and context.scene.get('wfrl_scene_kind') != 'manual'
               and context.window_manager.wfrl_home_page not in {'MANUAL', 'BACKEND'})
    if not offline or status.next_step != '主入口：N → WFRL':
        message(box, status.next_step)
    if offline:
        header, body = box.panel('wfrl_home_backend_details', default_closed=True)
        header.label(text='后端连接状态')
        if body is not None:
            message(body, backend_status(runtime.get_state()))
    else:
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
            if custom_camera_capture.status_for(context.scene).owned:
                layout.operator('wfrl.custom_capture_cancel', text='取消相机采集', icon='CANCEL')
        elif '相机' in busy:
            layout.label(text='在“相机”页确认或取消编辑。', icon='INFO')
    draw_tasks(layout, context)
    from .. import animation_render_ui
    animation_render_ui.draw(layout, context)
    return busy


def draw_tasks(layout, context):
    from pathlib import Path
    from .. import farm_flex, playback, video_output
    from ..radar_x100 import runtime as x100
    scene = context.scene
    box = layout.box()
    box.label(text='当前任务 · ' + scene.name)
    owner = playback.owner_scene()
    box.label(text='播放器：' + (owner.name if owner is not None else '未占用'))
    active = farm_flex.active_for(scene)
    if active is not None and farm_flex.is_active(scene):
        method = active.manifest.get('dual_beam_calibration', {}).get('reconstruction_method', 'B2')
        value = clearance_replay.sample(scene)
        box.label(text=Path(scene.get('wfrl_farm_flex_path', '')).name + ' · ' + method)
        box.label(text=f"仿真时刻 {value['time_s']:.3f} s · REVIEW_ONLY")
    from .. import custom_cameras
    count = sum(custom_cameras.get_camera(scene, slot) is not None for slot in (1, 2, 3))
    if count:
        box.label(text=f'已应用相机：{count} / 3')
    for task in foreground_tasks(scene):
        if task.active or (task.owned and task.stage):
            label = {'camera': '相机', 'mount': '盒体', 'capture': '采集', 'recording': '录制'}[task.kind]
            message(box, label + ' · ' + stage_label(task.stage) + (' · 本场景' if task.owned else ' · ' + task.scene_name))
            if task.progress:
                message(box, task.progress)
            if task.output:
                message(box, '输出：' + task.output)
    radar = x100.status_for(scene)
    if radar.active or radar.error:
        message(box, 'X100 · ' + stage_label(radar.stage) + (' · ' + radar.progress if radar.progress else ''))
        if radar.error:
            message(box, radar.error, 'ERROR')
    box.operator('wm.call_panel', text='查看 X100 任务 · N → X100', icon='PREFERENCES').name = 'WFRL_PT_x100'
    research = scene.get('split_texture_current_source', '')
    if scene.get('wfrl_nrel_defect_enabled') or scene.get('split_texture_review') or research:
        message(box, '研究显示 · 未验收；重建对照使用同源保存样本')
        box.operator('wm.call_panel', text='查看重建 / 纹理任务').name = 'WFRL_PT_blade_recon'
        if scene.get('wfrl_nrel_defect_enabled'):
            box.operator('wm.call_panel', text='返回缺陷任务').name = 'NREL_PT_defects'
    copy = video_output.status_for(scene)
    if copy[0] != 'IDLE':
        message(box, 'MP4 复制 · ' + stage_label(copy[0]) + ' · ' + copy[6])
    if copy[8] or copy[7] == 'ERROR':
        message(box, 'RTSP · ' + copy[9])


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
        layout.label(text='当前刚性姿态 → JSON；暂停后的原生冲突须先恢复。')
        layout.operator('wfrl.manual_down_view', text='观察所选风机 Down', icon='CAMERA_DATA')
        layout.operator('wfrl.manual_down_video', text='Down 姿态序列 → MP4 + JSON…', icon='RENDER_ANIMATION')
        message(layout, '使用所选风机的姿态与转速；取消仅影响本次输出。视口渲染期间界面暂停响应。')
        layout.operator('wfrl.manual_triple_down_video', text='三路 Down 视频 → MP4…', icon='RENDER_ANIMATION')
        return
    if clearance_replay.reader_for(scene) is None and scene.get('wfrl_scene_kind') != 'live':
        layout.label(text='请先在“回放”页加载数据。', icon='INFO')
        return
    data = layout.column()
    data.enabled = not busy
    data.operator('wfrl.export_history', text='导出已采样历史…', icon='EXPORT')
    data.label(text='仅导出已采样数据，缺失通道不补值。')
    if scene.get('wfrl_history_export_status'):
        from .ui_helpers import history_status
        history_status(layout, scene['wfrl_history_export_status'], context=context)
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
    window.label(text='场景相机与帧范围 → 后台 PNG；取消保留已完成帧。')
    from . import video_output
    video_output.draw(layout, context)


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
        page = context.window_manager.wfrl_home_page
        reader = clearance_replay.reader_for(scene)
        shared_playback = reader is not None and page != 'MANUAL'
        if shared_playback:
            controls = layout.column(align=True)
            controls.enabled = not busy
            value = clearance_replay.sample(scene)
            if farm_flex.is_active(scene):
                from .farm_replay import draw_playback
                draw_playback(controls, context, value, reader, show_time=True)
            else:
                from .clearance import draw_playback
                draw_playback(controls, scene, value, context.screen)
        layout.prop(context.window_manager, 'wfrl_home_page', expand=True)
        if page == 'REPLAY':
            if farm_flex.is_active(scene):
                from .farm_replay import draw
                controls = layout.column()
                controls.enabled = not busy
                draw(controls, context, show_playback=not shared_playback)
            elif clearance_replay.reader_for(scene) is not None:
                from .clearance import draw
                controls = layout.column()
                controls.enabled = not busy
                draw(controls, scene, show_playback=not shared_playback)
            else:
                draw_load(layout, context, busy=busy)
        elif page == 'CAMERA':
            if scene.get('wfrl_scene_kind') == 'manual':
                layout.label(text='在“手动”页调整机组姿态。', icon='INFO')
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
