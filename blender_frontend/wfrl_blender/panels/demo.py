"""Shared scene, camera and capture operators for the MAPPO demonstration."""
import bpy

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


CLASSES = (WFRL_OT_LoadDemo, WFRL_OT_SelectCamera, WFRL_OT_RenderStill,
           WFRL_OT_RenderAnimation)
