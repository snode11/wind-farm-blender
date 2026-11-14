"""Shared scene, camera and capture operators for the MAPPO demonstration."""
import bpy
from pathlib import Path
from tempfile import TemporaryDirectory

class DemoLoaded:
    @classmethod
    def poll(cls, context):
        return context.scene.objects.get('WFRL.Turbine.T1.Rotor') is not None


class WFRL_OT_LoadDemo(bpy.types.Operator):
    bl_idname = 'wfrl.load_demo'
    package_path: bpy.props.StringProperty(default='', options={'SKIP_SAVE'})
    bl_label = '加载 MAPPO · 60 秒'
    bl_description = '加载随扩展提供的三机 MAPPO 完整离线结果'

    @classmethod
    def poll(cls, context):
        from .. import runtime
        from ..home_state import can_start
        return runtime.configuration_editable() and can_start('replace_scene', context.scene)[0]

    def execute(self, context):
        from .. import load_demo_scene
        try:
            load_demo_scene(self.package_path or None)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_SelectCamera(bpy.types.Operator):
    bl_idname = "wfrl.select_camera"
    bl_label = "Select WFRL Camera"
    camera_name: bpy.props.StringProperty()

    def execute(self, context):
        from .. import farm_flex
        from .farm_replay import _ViewSwitchState, _view_failed, _required_camera
        scene, state = context.scene, None
        try:
            overview = farm_flex.is_active(scene) and self.camera_name in {
                'WFRL.Camera.World', 'WFRL.Camera.Top', 'WFRL.Camera.Side'}
            if not self.camera_name.endswith('.Gimbal') and not (overview and self.camera_name == 'WFRL.Camera.World'):
                camera = _required_camera(scene, self.camera_name)
            state = _ViewSwitchState(scene)
            if not self.camera_name.endswith('.Gimbal') and not (overview and self.camera_name == 'WFRL.Camera.World'):
                state.remember_camera(camera)
            if overview:
                if bpy.ops.wfrl.farm_flex_view(turbine='all') != {'FINISHED'}:
                    raise RuntimeError('风场视角切换已取消')
                if self.camera_name == 'WFRL.Camera.World':
                    return {'FINISHED'}
            if self.camera_name.endswith('.Gimbal'):
                from ..cameras import ensure_gimbal
                turbine = self.camera_name.split('.')[-2]
                existing = scene.objects.get(self.camera_name)
                state.remember_camera(existing)
                camera = ensure_gimbal(scene, turbine)
                if existing is None:
                    state.created.append(camera)
                scene.wfrl_gimbal_turbine = turbine
            from .custom_cameras import dismiss_for_view_switch
            dismiss_for_view_switch(scene)
            scene.camera = camera
            scene['wfrl_camera'] = self.camera_name
            from ..nacelle_camera import select_output
            select_output(scene, camera)
            if context.screen:
                from .. import split_reconstruction
                for area in context.screen.areas:
                    if area.type == 'VIEW_3D':
                        if split_reconstruction.is_reconstruction_view(scene, area.spaces.active):
                            continue
                        # Toolbar cameras release a sensor view's local override.
                        space = area.spaces.active
                        space.use_local_camera = False
                        space.camera = camera
                        space.region_3d.view_perspective = 'CAMERA'
                        from ..cameras import fill_camera_view
                        fill_camera_view(area, scene)
        except Exception as exc:
            return _view_failed(self, state, exc)
        return {"FINISHED"}


class WFRL_OT_LoadDualBeam(bpy.types.Operator):
    bl_idname = 'wfrl.load_dual_beam'
    bl_label = '加载双束净空与 S1 报警'
    bl_description = '加载双束研究结果；保留精度、采样和实机验证限制'
    filepath: bpy.props.StringProperty(subtype='FILE_PATH', options={'SKIP_SAVE'})
    filter_glob: bpy.props.StringProperty(default='manifest.json', options={'HIDDEN'})
    choose_file: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})
    builtin_method: bpy.props.EnumProperty(
        items=(('AXIS', '原双束旧法', '轮毂轴线外推；保持原默认方法'),
               ('TLS', 'TLS 候选', '轮毂约束 TLS；研究候选，性能待验收')),
        default='AXIS', options={'SKIP_SAVE'})

    def builtin_package(self):
        from .. import farm_flex
        return (farm_flex.default_dual_tls_package() if self.builtin_method == 'TLS'
                else farm_flex.default_dual_package())

    @classmethod
    def poll(cls, context):
        from .. import runtime
        from ..home_state import can_start
        return runtime.configuration_editable() and can_start('replace_scene', context.scene)[0]

    def invoke(self, context, event):
        if self.choose_file or not (self.builtin_package() / 'manifest.json').is_file():
            context.window_manager.fileselect_add(self)
            return {'RUNNING_MODAL'}
        return self.execute(context)

    def execute(self, context):
        from .. import load_demo_scene, farm_flex
        try:
            if self.filepath:
                manifest = Path(bpy.path.abspath(self.filepath)).expanduser()
                if not manifest.is_file():
                    raise ValueError('所选数据包文件不存在或不可读：' + str(manifest))
                if manifest.name != 'manifest.json':
                    raise ValueError('请选择双束数据包的 manifest.json：' + str(manifest))
                path = manifest.parent
            else:
                path = self.builtin_package()
            package = farm_flex.preflight(path)
            overlay = package.overlay
            if overlay is None:
                raise ValueError('请选择双束数据包的 manifest.json')
            if not self.filepath:
                expected = 'hub-tls.v1' if self.builtin_method == 'TLS' else 'hub-axis.v1'
                if overlay['config'].get('reconstruction_method', 'hub-axis.v1') != expected:
                    raise ValueError('内置双束数据包的方法与入口不一致：' + expected)
            load_demo_scene(path, _package=package)
            context.scene.wfrl_farm_panel_page = 'RADAR'
        except Exception as exc:
            context.scene['wfrl_home_load_error'] = '双束候选包加载失败：' + str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_RenderStill(bpy.types.Operator):
    bl_idname = "wfrl.render_still"
    bl_label = "Render Still"
    bl_description = "Render the active WFRL camera to a PNG"
    filepath: bpy.props.StringProperty(subtype="FILE_PATH", default="//wfrl-capture.png")

    def execute(self, context):
        scene = context.scene
        render = scene.render
        previous_path, previous_format = render.filepath, render.image_settings.file_format
        try:
            destination = Path(bpy.path.abspath(self.filepath))
            destination.parent.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix='.wfrl-still-', dir=destination.parent) as temporary:
                render.image_settings.file_format = 'PNG'
                render.filepath = str(Path(temporary) / destination.name)
                if 'FINISHED' not in bpy.ops.render.render(write_still=True):
                    raise RuntimeError('Blender cancelled the render')
                outputs = list(Path(temporary).iterdir())
                if not outputs or any(not path.is_file() or path.stat().st_size == 0 for path in outputs):
                    raise RuntimeError('Blender did not save a complete PNG')
                for path in outputs:
                    path.replace(destination.parent / path.name)
            render.filepath = str(destination)
            scene['wfrl_last_render'] = render.filepath
        except Exception as exc:
            render.filepath, render.image_settings.file_format = previous_path, previous_format
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {"FINISHED"}


class WFRL_OT_RenderAnimation(bpy.types.Operator):
    bl_idname = "wfrl.render_animation"
    bl_label = "后台渲染动画"
    bl_description = "独立场景副本按当前相机与帧范围生成 PNG；提供进度与取消，不改变源场景"
    directory: bpy.props.StringProperty(subtype="DIR_PATH", default="//wfrl-animation/")

    @classmethod
    def poll(cls, context):
        from ..animation_render import job
        return context.scene.camera is not None and not job.active

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from .. import animation_render_ui
        try:
            path = animation_render_ui.start(context, self.directory)
        except (OSError, ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, '动画正在后台渲染：' + str(path))
        return {"FINISHED"}


class WFRL_OT_CancelRenderAnimation(bpy.types.Operator):
    bl_idname = 'wfrl.cancel_render_animation'
    bl_label = '取消后台动画渲染'

    @classmethod
    def poll(cls, context):
        from ..animation_render import job
        return job.active

    def execute(self, context):
        from ..animation_render import job
        job.cancel()
        return {'FINISHED'}


CLASSES = (WFRL_OT_LoadDemo, WFRL_OT_LoadDualBeam, WFRL_OT_SelectCamera, WFRL_OT_RenderStill,
           WFRL_OT_RenderAnimation, WFRL_OT_CancelRenderAnimation)
