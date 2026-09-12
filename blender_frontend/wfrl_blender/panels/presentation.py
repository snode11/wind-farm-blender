"""Camera configuration and wall-clock screen capture for all runtime modes."""
import json
import time
from pathlib import Path

import bpy

from ..presentation import recording_schedule, set_presentation_mode

_ACTIVE = None


class WFRL_OT_PresentationMode(bpy.types.Operator):
    bl_idname = 'wfrl.presentation_mode'
    bl_label = 'Toggle Presentation / Development'
    bl_description = '切换演示/开发界面：隐藏或显示工具栏、工具标题栏和操作手柄，不影响仿真或训练'

    def execute(self, context):
        set_presentation_mode(context, not context.workspace.get('presentation_mode', False))
        return {'FINISHED'}


def _directory(scene):
    directory = Path(bpy.path.abspath(scene.wfrl_capture_directory)).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


class WindowRequired:
    @classmethod
    def poll(cls, context):
        return not bpy.app.background and context.window is not None


class WFRL_OT_CaptureScreenshot(WindowRequired, bpy.types.Operator):
    bl_idname = 'wfrl.capture_screenshot'
    bl_label = 'Save Screenshot'
    bl_description = 'Save the current Blender window at its screen resolution, including source labels'

    def execute(self, context):
        try:
            path = _directory(context.scene) / f'wfrl-{time.time_ns()}.png'
            result = bpy.ops.screen.screenshot(filepath=str(path))
            if 'FINISHED' not in result:
                raise RuntimeError('Blender cancelled the screenshot')
            context.scene['wfrl_capture_status'] = str(path)
            self.report({'INFO'}, f'Saved {path.name}')
        except (OSError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_CaptureRecording(WindowRequired, bpy.types.Operator):
    bl_idname = 'wfrl.capture_recording'
    bl_label = 'Record PNG Sequence'
    bl_description = 'Record the visible window on wall-clock time; never advances backend steps. Esc stops'

    def execute(self, context):
        global _ACTIVE
        if _ACTIVE is not None:
            self.report({'WARNING'}, 'A recording is already active')
            return {'CANCELLED'}
        scene = context.scene
        try:
            interval, self._total = recording_schedule(scene.wfrl_capture_fps, scene.wfrl_capture_duration)
            self._directory = _directory(scene) / f'recording-{time.time_ns()}'
            self._directory.mkdir()
        except (ValueError, OSError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self._scene = scene
        self._index = 0
        self._frames = []
        self._started = time.monotonic()
        self._duration = scene.wfrl_capture_duration
        self._fps = scene.wfrl_capture_fps
        self._interval = interval
        self._next_capture = self._started + interval
        self._manager = context.window_manager
        self._timer = self._manager.event_timer_add(interval, window=context.window)
        _ACTIVE = self
        scene['wfrl_capture_status'] = 'RECORDING / Esc to stop'
        try:
            self._capture(0.0)
            self._manager.modal_handler_add(self)
        except (OSError, RuntimeError) as exc:
            self._finish('FAILED')
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC':
            self._finish('CANCELLED')
            return {'CANCELLED'}
        # Blender Event does not expose its originating timer. Gate any TIMER
        # event by our monotonic deadline so other operators cannot oversample.
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        now = time.monotonic()
        elapsed = now - self._started
        if elapsed >= self._duration or self._index >= self._total:
            self._finish('COMPLETE')
            return {'FINISHED'}
        if now < self._next_capture:
            return {'PASS_THROUGH'}
        try:
            self._capture(elapsed)
            self._next_capture = now + self._interval
        except (OSError, RuntimeError) as exc:
            self._finish('FAILED')
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'PASS_THROUGH'}

    def _capture(self, elapsed):
        path = self._directory / f'{self._index:06d}.png'
        if 'FINISHED' not in bpy.ops.screen.screenshot(filepath=str(path)):
            raise RuntimeError('Screenshot cancelled')
        self._frames.append({'file': path.name, 'elapsed_s': elapsed,
                             'display_frame': self._scene.frame_current})
        self._index += 1
        self._scene['wfrl_capture_status'] = f'RECORDING {elapsed:.1f}/{self._duration:.1f}s / {self._index} frames'

    def _finish(self, status):
        global _ACTIVE
        self._manager.event_timer_remove(self._timer)
        _ACTIVE = None
        self._scene['wfrl_capture_status'] = f'{status} / {self._index} PNG frames / {self._directory}'
        try:
            (self._directory / 'manifest.json').write_text(json.dumps({
                'status': status, 'requested_fps': self._fps, 'duration_s': self._duration,
                'format': 'PNG window sequence', 'clock': 'wall-clock',
                'note': 'Actual capture timestamps retained; slow captures drop samples, never control steps.',
                'frames': self._frames}, indent=2), encoding='utf-8')
        except OSError as exc:
            self._scene['wfrl_capture_status'] = f'Manifest write failed: {exc}'

    def cancel(self, context):
        if _ACTIVE is self:
            self._finish('CANCELLED')


class WFRL_PT_Presentation(bpy.types.Panel):
    bl_idname = 'WFRL_PT_presentation'
    bl_label = 'WFRL / Views & Capture'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Item'

    def draw(self, context):
        scene, layout = context.scene, self.layout
        row = layout.row(align=True)
        for name in ('World', 'Top'):
            row.operator('wfrl.select_camera', text=name).camera_name = 'WFRL.Camera.' + name
        layout.prop(scene, 'wfrl_show_wake', text='Wake visible')
        layout.prop(scene, 'wfrl_wake_display', text='Wake display')
        layout.label(text='Cinematic: wind-tunnel filaments (SYNTH)')
        if scene.wfrl_wake_display == 'CINEMATIC':
            box = layout.box()
            if scene.get('wfrl_scene_kind') == 'live':
                box.label(text='Backend inflow / 后端来流')
                box.label(text='SYNTH trails / 尾迹形状为示意')
                if not scene.get('wfrl_cinematic_inflow_valid', False):
                    box.label(text='Waiting for valid inflow / 等待来流数据')
            controls = box.column()
            controls.enabled = scene.get('wfrl_scene_kind') != 'live'
            controls.prop(scene, 'wfrl_cinematic_wind_mode')
            if scene.wfrl_cinematic_wind_mode == 'FRONT':
                controls.prop(scene, 'wfrl_cinematic_reference')
                controls.prop(scene, 'wfrl_cinematic_offset', slider=True)
            else:
                controls.prop(scene, 'wfrl_cinematic_seed')
                controls.prop(scene, 'wfrl_cinematic_interval')
            if scene.get('wfrl_scene_kind') != 'live':
                box.label(text='Visual wind only / 不修改仿真来风')
        layout.label(text='Green lines: illustration, not FAST.Farm wind')
        presenting = context.workspace.get('presentation_mode', False)
        layout.operator('wfrl.presentation_mode', text='显示编辑工具 / 开发模式' if presenting else '隐藏编辑工具 / 演示模式')
        layout.label(text='当前：演示模式' if presenting else '当前：开发模式')
        layout.prop(scene, 'wfrl_capture_directory')
        layout.operator('wfrl.capture_screenshot')
        layout.prop(scene, 'wfrl_capture_fps')
        layout.prop(scene, 'wfrl_capture_duration')
        row = layout.row()
        row.enabled = _ACTIVE is None
        row.operator('wfrl.capture_recording')
        layout.label(text='PNG window sequence / screen resolution')
        layout.label(text=scene.get('wfrl_capture_status', 'Capture ready'))


CLASSES = (WFRL_OT_PresentationMode,
           WFRL_OT_CaptureScreenshot, WFRL_OT_CaptureRecording, WFRL_PT_Presentation)


def register_properties():
    scene = bpy.types.Scene
    scene.wfrl_capture_directory = bpy.props.StringProperty(name='Output folder', subtype='DIR_PATH', default='//wfrl-captures/')
    scene.wfrl_capture_fps = bpy.props.IntProperty(name='Capture FPS', min=1, max=30, default=10)
    scene.wfrl_capture_duration = bpy.props.FloatProperty(name='Duration (seconds)', min=.1, max=3600, default=10)


def unregister_properties():
    if _ACTIVE is not None:
        _ACTIVE._finish('CANCELLED')
    for name in ('wfrl_capture_directory', 'wfrl_capture_fps', 'wfrl_capture_duration'):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
