"""Mounted PTZ cameras with a rate joystick and viewport mouse control."""
import math
import time
import bpy
from bpy.props import EnumProperty, FloatProperty
from bpy.app.handlers import persistent
from ..cameras import ensure_gimbal, aim_gimbal, fill_camera_view

_ACTIVE = None


def camera_world_angles(camera, depsgraph):
    """World heading/elevation and optical roll from the evaluated camera axes.

    Pan is measured from world +X toward +Y; tilt is positive upward. Roll is
    rotation about local camera +Z relative to world-up. At a vertical optical
    axis heading and this roll reference are undefined, so do not invent them.
    """
    from mathutils import Vector
    rotation = camera.evaluated_get(depsgraph).matrix_world.to_quaternion()
    forward = rotation @ Vector((0, 0, -1))
    up = rotation @ Vector((0, 1, 0))
    tilt = math.degrees(math.asin(max(-1., min(1., forward.z))))
    right = forward.cross(Vector((0, 0, 1)))
    if right.length < 1e-6:
        return None, tilt, None
    right.normalize()
    upright = right.cross(forward).normalized()
    pan = math.degrees(math.atan2(forward.y, forward.x)) % 360
    roll = math.degrees(math.atan2(-up.dot(right), up.dot(upright)))
    return pan, tilt, roll


def angle_label(value):
    return '未定义（垂直视轴）' if value is None else f'{value:.1f}°'


def current(scene):
    return ensure_gimbal(scene, scene.wfrl_gimbal_turbine)


def show(context):
    camera = current(context.scene)
    space = context.space_data
    if space and space.type == 'VIEW_3D':
        # Local camera leaves another World view alone in dual/quad layouts.
        space.use_local_camera = True
        space.camera = camera
        space.region_3d.view_perspective = 'CAMERA'
        space.region_3d.view_camera_zoom = 0
        space.region_3d.view_camera_offset = (0, 0)
        fill_camera_view(context.area, context.scene)
    return camera


def switch(scene, context):
    if context and context.area and context.area.type == 'VIEW_3D':
        if scene.objects.get(f'WFRL.Turbine.{scene.wfrl_gimbal_turbine}.YawRoot'):
            show(context)
            context.area.tag_redraw()


def change(camera, dx=0, dy=0, zoom=0):
    aim_gimbal(camera, camera['gimbal_yaw'] + dx,
               camera['gimbal_pitch'] + dy, camera['gimbal_fov'] + zoom)


class GimbalAvailable:
    @classmethod
    def poll(cls, context):
        return (context.area is not None and context.area.type == 'VIEW_3D'
                and context.scene.objects.get(
                    f'WFRL.Turbine.{context.scene.wfrl_gimbal_turbine}.YawRoot') is not None)


class WFRL_OT_GimbalPreset(GimbalAvailable, bpy.types.Operator):
    bl_idname = 'wfrl.gimbal_preset'
    bl_label = 'Gimbal preset'
    preset: EnumProperty(items=[(v, v.title(), '') for v in ('DOWN', 'FRONT', 'BACK', 'RESET')])

    def execute(self, context):
        camera = show(context)
        # Reset is a useful inspection framing: keep the rotor plane and
        # nacelle in view.  The old reset duplicated DOWN (-90°), which aimed
        # straight at the ground and produced the clipped view seen in the UI.
        yaw, pitch = {'DOWN': (180, -90), 'FRONT': (180, 0),
                      'BACK': (0, 0), 'RESET': (180, -35)}[self.preset]
        aim_gimbal(camera, yaw, pitch, 75 if self.preset == 'RESET' else camera['gimbal_fov'])
        return {'FINISHED'}


class WFRL_OT_GimbalMode(GimbalAvailable, bpy.types.Operator):
    bl_idname = 'wfrl.gimbal_mode'
    bl_label = 'Camera Mode / 相机模式'
    bl_description = 'Drag to look, wheel to zoom; drag the joystick to turn continuously; Esc exits'

    def finish(self, context=None):
        global _ACTIVE
        if getattr(self, '_timer', None):
            self._wm.event_timer_remove(self._timer)
            self._timer = None
        if getattr(self, '_handle', None):
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        try:
            self._area.header_text_set(None)
            self._area.tag_redraw()
        except ReferenceError:
            pass
        self._closed = True
        if _ACTIVE is self:
            _ACTIVE = None

    def cancel(self, context):
        self.finish(context)

    def invoke(self, context, event):
        global _ACTIVE
        if _ACTIVE:
            _ACTIVE.finish(context)
            return {'FINISHED'}
        if context.area.type != 'VIEW_3D':
            return {'CANCELLED'}
        show(context)
        self._area, self._scene, self._wm = context.area, context.scene, context.window_manager
        self._region = next(r for r in context.area.regions if r.type == 'WINDOW')
        self._closed, self._drag, self._stick = False, None, (0, 0)
        self._last_time = time.monotonic()
        self._timer = self._wm.event_timer_add(.025, window=context.window)
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_overlay, (), 'WINDOW', 'POST_PIXEL')
        _ACTIVE = self
        self._wm.modal_handler_add(self)
        self._area.header_text_set('Gimbal: drag to look | wheel: zoom | joystick: hold to turn | Esc: exit')
        self._area.tag_redraw()
        return {'RUNNING_MODAL'}

    def geometry(self):
        # Region overlap can place the sidebar above the WINDOW region.
        sidebar = next((r.width for r in self._area.regions if r.type == 'UI' and r.width > 1), 0)
        overlap = bpy.context.preferences.system.use_region_overlap
        return max(85, self._region.width - (sidebar if overlap else 0) - 90), self._region.height - 125, 56

    def inside(self, event):
        # Respect the actual region under the pointer, including overlapping N sidebar.
        for region in self._area.regions:
            if region.type in {'UI', 'TOOLS', 'HEADER', 'TOOL_HEADER'} and region.width > 1:
                if region.x <= event.mouse_x < region.x + region.width and region.y <= event.mouse_y < region.y + region.height:
                    return False
        r = self._region
        return r.x <= event.mouse_x < r.x + r.width and r.y <= event.mouse_y < r.y + r.height

    def modal(self, context, event):
        if self._closed:
            return {'FINISHED'}
        try:
            if context.scene != self._scene or self._area.type != 'VIEW_3D':
                self.finish(context)
                return {'FINISHED'}
            if event.type == 'ESC' and event.value == 'PRESS':
                self.finish(context)
                return {'FINISHED'}
            camera = self._scene.objects.get(f'WFRL.Camera.{self._scene.wfrl_gimbal_turbine}.Gimbal')
            if camera is None or self._area.spaces.active.camera != camera:
                self.finish(context)
                return {'FINISHED'}
            if event.type == 'TIMER':
                now = time.monotonic()
                dt, self._last_time = min(now - self._last_time, .1), now
                if self._drag == 'stick':
                    x, y = self._stick
                    change(camera, -x * dt * self._scene.wfrl_gimbal_speed,
                           y * dt * self._scene.wfrl_gimbal_speed)
                    self._area.tag_redraw()
                return {'PASS_THROUGH'}
            x, y = event.mouse_x - self._region.x, event.mouse_y - self._region.y
            if event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and self._drag:
                self._drag, self._stick = None, (0, 0)
                self._area.tag_redraw()
                return {'RUNNING_MODAL'}
            if event.type == 'MOUSEMOVE' and self._drag:
                if self._drag == 'stick':
                    cx, cy, radius = self.geometry()
                    dx, dy = (x - cx) / radius, (y - cy) / radius
                    length = max(1, math.hypot(dx, dy))
                    self._stick = (dx / length, dy / length)
                else:
                    change(camera, -(x - self._last[0]) * .2, (y - self._last[1]) * .2)
                self._last = (x, y)
                self._area.tag_redraw()
                return {'RUNNING_MODAL'}
            if not self.inside(event):
                return {'PASS_THROUGH'}
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                cx, cy, radius = self.geometry()
                self._drag = 'stick' if math.hypot(x-cx, y-cy) <= radius else 'look'
                self._last = (x, y)
                if self._drag == 'stick':
                    self._stick = ((x-cx)/radius, (y-cy)/radius)
                return {'RUNNING_MODAL'}
            if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
                change(camera, zoom=-3 if event.type == 'WHEELUPMOUSE' else 3)
                self._area.tag_redraw()
                return {'RUNNING_MODAL'}
            # Prevent native orbit/pan while controlling a fixed mounted camera.
            if event.type in {'MIDDLEMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEROTATE'}:
                return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}
        except (ReferenceError, RuntimeError, KeyError):
            self.finish(context)
            return {'FINISHED'}

    def draw_overlay(self):
        if self._closed or bpy.context.area != self._area:
            return
        import gpu
        import blf
        from gpu_extras.batch import batch_for_shader
        cx, cy, radius = self.geometry()
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        def disk(x, y, r, color):
            vertices = [(x, y)] + [(x + r*math.cos(i*math.tau/48), y + r*math.sin(i*math.tau/48)) for i in range(49)]
            batch = batch_for_shader(shader, 'TRIS', {'pos': vertices}, indices=[(0, i+1, i+2) for i in range(48)])
            shader.bind()
            shader.uniform_float('color', color)
            batch.draw(shader)
        gpu.state.blend_set('ALPHA')
        try:
            disk(cx, cy, radius+2, (.4, .7, .8, .8))
            disk(cx, cy, radius, (.025, .05, .07, .88))
            dx, dy = self._stick
            disk(cx + dx*radius*.7, cy + dy*radius*.7, 15, (.2, .8, .9, 1))
            blf.size(0, 13)
            blf.color(0, 1, 1, 1, 1)
            for text, offset in ((f'{self._scene.wfrl_gimbal_turbine}  GIMBAL', radius+18), ('Drag / hold to turn', -radius-22)):
                blf.position(0, cx - blf.dimensions(0, text)[0]/2, cy+offset, 0)
                blf.draw(0, text)
        finally:
            gpu.state.blend_set('NONE')


class WFRL_PT_Gimbal(bpy.types.Panel):
    bl_label = 'WFRL / Camera 相机与演示'
    bl_idname = 'WFRL_PT_gimbal'
    bl_order = -200
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Item'

    def draw(self, context):
        scene, layout = context.scene, self.layout
        from .clearance import draw_navigation, fold
        draw_navigation(layout, scene)
        if fold(layout, scene, 'wfrl_clearance_show_camera', '云台控制'):
            row = layout.row(align=True)
            for turbine in ('T1', 'T2', 'T3'):
                cell = row.row(align=True)
                cell.enabled = scene.objects.get(f'WFRL.Turbine.{turbine}.YawRoot') is not None
                cell.prop_enum(scene, 'wfrl_gimbal_turbine', turbine)
            layout = layout.column()
            layout.enabled = scene.objects.get(f'WFRL.Turbine.{scene.wfrl_gimbal_turbine}.YawRoot') is not None
            layout.operator('wfrl.gimbal_mode', text='Exit Camera Mode / 退出' if _ACTIVE else 'Camera Mode / 相机模式', icon='CAMERA_DATA')
            layout.prop(scene, 'wfrl_gimbal_speed')
            row = layout.row(align=True)
            for value, label in (('DOWN', 'Down ↓'), ('FRONT', 'Front'), ('BACK', 'Back')):
                row.operator('wfrl.gimbal_preset', text=label).preset = value
            layout.operator('wfrl.gimbal_preset', text='Reset / 复位').preset = 'RESET'
            # Pose is an observation, so keep it readable even if the selected
            # turbine's joystick controls are unavailable.
            pose = self.layout.column(align=True)
            space = context.space_data
            camera = (space.camera if space and space.type == 'VIEW_3D' and space.use_local_camera else scene.camera)
            if camera:
                pan, tilt, roll = camera_world_angles(camera, context.evaluated_depsgraph_get())
                pose.label(text='当前 Camera 仿真姿态 · 世界坐标')
                pose.label(text=camera.name.removeprefix('WFRL.Camera.'))
                pose.label(text=f'Pan {angle_label(pan)}  Tilt {angle_label(tilt)}')
                pose.label(text=f'Roll {angle_label(roll)} · 无独立控制')
            else:
                pose.label(text='Camera 仿真姿态：未选择相机')
            pose.label(text='拖动转向 · 滚轮缩放 · Esc 退出')
        from .clearance import draw as draw_clearance
        draw_clearance(self.layout, scene)


# Keep operators for existing scenes, without exposing the retired sidebar panel.
CLASSES = (WFRL_OT_GimbalPreset, WFRL_OT_GimbalMode, WFRL_PT_Gimbal)


@persistent
def cancel_on_load(_unused):
    if _ACTIVE:
        _ACTIVE.finish()


def register_properties():
    if cancel_on_load not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(cancel_on_load)
    bpy.types.Scene.wfrl_gimbal_turbine = EnumProperty(name='Camera turbine', items=[(t, t, '') for t in ('T1', 'T2', 'T3')], default='T1', update=switch)
    bpy.types.Scene.wfrl_gimbal_speed = FloatProperty(name='Joystick speed °/s', default=45, min=1, max=180)


def unregister_properties():
    if cancel_on_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(cancel_on_load)
    if _ACTIVE:
        _ACTIVE.finish()
    for name in ('wfrl_gimbal_turbine', 'wfrl_gimbal_speed'):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
