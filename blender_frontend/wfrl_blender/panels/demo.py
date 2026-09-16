"""Shared scene, camera and capture operators for the MAPPO demonstration."""
import bpy

class DemoLoaded:
    @classmethod
    def poll(cls, context):
        return context.scene.objects.get('WFRL.Turbine.T1.Rotor') is not None


class WFRL_OT_LoadDemo(bpy.types.Operator):
    bl_idname = 'wfrl.load_demo'
    bl_label = '加载 MAPPO · 60 秒'
    bl_description = '加载随扩展提供的三机 MAPPO 完整离线结果'

    @classmethod
    def poll(cls, context):
        from .. import runtime
        return runtime.configuration_editable()

    def execute(self, context):
        from .. import load_demo_scene
        try:
            load_demo_scene()
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


class WFRL_OT_DualView(DemoLoaded, bpy.types.Operator):
    bl_idname = "wfrl.dual_view"
    bl_label = "World + Nacelle View"
    bl_description = "Split the WFRL workspace into world and geometric nacelle views"

    def execute(self, context):
        if bpy.app.background:
            return {'CANCELLED'}
        screen = context.screen
        if screen.show_fullscreen:
            bpy.ops.screen.screen_full_area(use_hide_panels=False)
            screen = context.window.screen
        existing = [a for a in screen.areas if a.type == 'VIEW_3D' and a.spaces.active.use_local_camera]
        if existing and len([a for a in screen.areas if a.type == 'VIEW_3D']) >= 2:
            return {'FINISHED'}
        area = max((a for a in screen.areas if a.type == 'VIEW_3D'), key=lambda a:a.width*a.height)
        before = {a.as_pointer() for a in screen.areas}
        with context.temp_override(area=area):
            bpy.ops.screen.area_split(direction='VERTICAL', factor=.62)
        created = next(a for a in screen.areas if a.as_pointer() not in before)
        views = sorted((area,created), key=lambda a:a.x)
        left, right = views[0].spaces.active, views[1].spaces.active
        from .. import farm_flex
        if farm_flex.is_active(context.scene):
            bpy.ops.wfrl.farm_flex_view(turbine='all')
        context.scene.camera = bpy.data.objects.get('WFRL.Camera.FarmFlexOverview') or bpy.data.objects['WFRL.Camera.World']
        left.use_local_camera = False
        left.region_3d.view_perspective = 'CAMERA'
        right.use_local_camera = True
        right.camera = bpy.data.objects['WFRL.Camera.T1.Sensor']
        right.show_region_ui = False
        right.region_3d.view_perspective = 'CAMERA'
        right.region_3d.view_camera_zoom = 0
        right.region_3d.view_camera_offset = (0,0)
        context.scene['wfrl_dual_layout'] = True
        return {'FINISHED'}


CLASSES = (WFRL_OT_LoadDemo, WFRL_OT_SelectCamera, WFRL_OT_RenderStill,
           WFRL_OT_RenderAnimation, WFRL_OT_DualView)
