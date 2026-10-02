"""Shared scene, camera and capture operators for the MAPPO demonstration."""
import bpy
from pathlib import Path

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
        return runtime.configuration_editable()

    def execute(self, context):
        from .. import load_demo_scene
        try:
            load_demo_scene(self.package_path or None)
        except (ValueError, OSError, KeyError, TypeError, ImportError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_SelectCamera(bpy.types.Operator):
    bl_idname = "wfrl.select_camera"
    bl_label = "Select WFRL Camera"
    camera_name: bpy.props.StringProperty()

    def execute(self, context):
        from .. import farm_flex
        if farm_flex.is_active(context.scene) and self.camera_name in {
                'WFRL.Camera.World', 'WFRL.Camera.Top', 'WFRL.Camera.Side'}:
            bpy.ops.wfrl.farm_flex_view(turbine='all')
            if self.camera_name == 'WFRL.Camera.World':
                return {'FINISHED'}
        if self.camera_name.endswith('.Gimbal'):
            from ..cameras import ensure_gimbal
            turbine = self.camera_name.split('.')[-2]
            try:
                camera = ensure_gimbal(context.scene, turbine)
            except ValueError as exc:
                self.report({'WARNING'}, str(exc))
                return {'CANCELLED'}
            context.scene.wfrl_gimbal_turbine = turbine
        else:
            camera = context.scene.objects.get(self.camera_name)
        if camera is None:
            return {"CANCELLED"}
        from .custom_cameras import dismiss_for_view_switch
        dismiss_for_view_switch(context.scene)
        context.scene.camera = camera
        context.scene["wfrl_camera"] = self.camera_name
        if context.screen:
            for area in context.screen.areas:
                if area.type == "VIEW_3D":
                    # Sensor/dual-view uses a local camera.  Clear that
                    # override when choosing any toolbar camera, otherwise
                    # the viewport remains locked to T1 after selecting World.
                    space = area.spaces.active
                    space.use_local_camera = False
                    space.camera = camera
                    space.region_3d.view_perspective = "CAMERA"
                    from ..cameras import fill_camera_view
                    fill_camera_view(area, context.scene)
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
        return runtime.configuration_editable()

    def invoke(self, context, event):
        if self.choose_file or not (self.builtin_package() / 'manifest.json').is_file():
            context.window_manager.fileselect_add(self)
            return {'RUNNING_MODAL'}
        return self.execute(context)

    def execute(self, context):
        from .. import load_demo_scene, farm_flex, clearance_replay
        path = Path(self.filepath).parent if self.filepath else self.builtin_package()
        try:
            _, overlay = farm_flex.dual_beam_api().resolve_package(path)
            if overlay is None:
                raise ValueError('请选择双束数据包的 manifest.json')
            if not self.filepath:
                expected = 'hub-tls.v1' if self.builtin_method == 'TLS' else 'hub-axis.v1'
                if overlay['config'].get('reconstruction_method', 'hub-axis.v1') != expected:
                    raise ValueError('内置双束数据包的方法与入口不一致：' + expected)
            load_demo_scene(path)
            context.scene.wfrl_farm_panel_page = 'RADAR'
        except (ValueError, OSError, KeyError, TypeError, ImportError) as exc:
            clearance_replay.clear(context.scene, '双束数据未就绪：' + str(exc))
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
        scene.render.image_settings.file_format = "PNG"
        scene.render.filepath = bpy.path.abspath(self.filepath)
        scene["wfrl_last_render"] = scene.render.filepath
        bpy.ops.render.render(write_still=True)
        return {"FINISHED"}


class WFRL_OT_RenderAnimation(bpy.types.Operator):
    bl_idname = "wfrl.render_animation"
    bl_label = "Render Animation"
    bl_description = "Render the current WFRL frame range from the active camera"
    directory: bpy.props.StringProperty(subtype="DIR_PATH", default="//wfrl-animation/")

    def execute(self, context):
        scene = context.scene
        scene.render.image_settings.file_format = "PNG"
        scene.render.filepath = bpy.path.abspath(self.directory)
        scene["wfrl_last_animation_render"] = scene.render.filepath
        bpy.ops.render.render(animation=True)
        return {"FINISHED"}


CLASSES = (WFRL_OT_LoadDemo, WFRL_OT_LoadDualBeam, WFRL_OT_SelectCamera, WFRL_OT_RenderStill,
           WFRL_OT_RenderAnimation)
