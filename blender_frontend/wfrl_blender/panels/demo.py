"""Native sidebar controls for the SYNTH offline demonstration."""
import bpy

from ..state import END_FRAME, sample_demo, time_for_frame


def _sync(scene):
    from .. import _update_demo_status
    _update_demo_status(scene)


def _pause_playback():
    from .. import _cancel_playback
    _cancel_playback()


def _play(context):
    if not bpy.app.background and context.screen and not context.screen.is_animation_playing:
        bpy.ops.screen.animation_play()


class DemoLoaded:
    @classmethod
    def poll(cls, context):
        from .. import runtime
        return runtime.get_state().connection == "LOCAL DEMO" and context.scene.objects.get("WFRL.Turbine.T1.Rotor") is not None


class WFRL_OT_LoadDemo(bpy.types.Operator):
    bl_idname = "wfrl.load_demo"
    bl_label = "Load Demo Scene"
    bl_description = "Build or rebuild the three-turbine offline SYNTH presentation"

    @classmethod
    def poll(cls, context):
        from .. import runtime
        return runtime.configuration_editable()

    def execute(self, context):
        from .. import load_demo_scene
        load_demo_scene()
        return {"FINISHED"}


class WFRL_OT_DemoStart(DemoLoaded, bpy.types.Operator):
    bl_idname = "wfrl.demo_start"
    bl_label = "Start Demo"

    @classmethod
    def poll(cls, context):
        from .. import runtime
        return DemoLoaded.poll(context) and runtime.get_state().allows("start")

    def execute(self, context):
        scene = context.scene
        if scene.get("wfrl_run_status") == "PAUSED":
            return bpy.ops.wfrl.demo_resume()
        scene.wfrl_manual_enabled = False
        scene["wfrl_run_status"] = "STARTING"
        scene.frame_set(1)
        _sync(scene)
        _play(context)
        return {"FINISHED"}


class WFRL_OT_DemoResume(DemoLoaded, bpy.types.Operator):
    bl_idname = "wfrl.demo_resume"
    bl_label = "Resume Demo"

    @classmethod
    def poll(cls, context):
        return DemoLoaded.poll(context) and context.scene.get("wfrl_run_status") == "PAUSED"

    def execute(self, context):
        scene = context.scene
        scene.wfrl_manual_enabled = False
        scene["wfrl_run_status"] = str(sample_demo(time_for_frame(scene.frame_current)).status)
        _sync(scene)
        if scene.frame_current < END_FRAME:
            _play(context)
        return {"FINISHED"}


class WFRL_OT_DemoPause(DemoLoaded, bpy.types.Operator):
    bl_idname = "wfrl.demo_pause"
    bl_label = "Pause Demo"

    @classmethod
    def poll(cls, context):
        from .. import runtime
        return DemoLoaded.poll(context) and runtime.get_state().allows("pause")

    def execute(self, context):
        _pause_playback()
        context.scene["wfrl_run_status"] = "PAUSED"
        _sync(context.scene)
        return {"FINISHED"}


class WFRL_OT_DemoStep(DemoLoaded, bpy.types.Operator):
    bl_idname = "wfrl.demo_step"
    bl_label = "Step One Frame"
    bl_description = "Advance the paused SYNTH script by 0.04 seconds"

    @classmethod
    def poll(cls, context):
        return DemoLoaded.poll(context) and context.scene.get("wfrl_run_status") == "PAUSED"

    def execute(self, context):
        scene = context.scene
        scene.wfrl_manual_enabled = False
        scene.frame_set(min(END_FRAME, scene.frame_current + 1))
        if scene.frame_current >= END_FRAME:
            scene["wfrl_run_status"] = "STOPPED"
        _sync(scene)
        return {"FINISHED"}


class WFRL_OT_DemoStop(DemoLoaded, bpy.types.Operator):
    bl_idname = "wfrl.demo_stop"
    bl_label = "Stop & Feather"

    def execute(self, context):
        _pause_playback()
        context.scene.wfrl_manual_enabled = False
        context.scene["wfrl_run_status"] = "STOPPED"
        context.scene.frame_set(END_FRAME)
        _sync(context.scene)
        return {"FINISHED"}


class WFRL_OT_DemoReset(DemoLoaded, bpy.types.Operator):
    bl_idname = "wfrl.demo_reset"
    bl_label = "Reset to Ready"

    def execute(self, context):
        _pause_playback()
        context.scene.wfrl_manual_enabled = False
        context.scene["wfrl_run_status"] = "READY"
        context.scene.frame_set(1)
        _sync(context.scene)
        return {"FINISHED"}


class WFRL_OT_SelectCamera(bpy.types.Operator):
    bl_idname = "wfrl.select_camera"
    bl_label = "Select WFRL Camera"
    camera_name: bpy.props.StringProperty()

    def execute(self, context):
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
        context.scene.camera = bpy.data.objects['WFRL.Camera.World']
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


class WFRL_PT_Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Item"


class WFRL_PT_DemoPanel(WFRL_PT_Base, bpy.types.Panel):
    bl_label = "WFRL / PRESENTATION"
    bl_idname = "WFRL_PT_demo_panel"
    bl_order = -100

    @classmethod
    def poll(cls, context):
        from .. import runtime
        return runtime.get_state().connection == "LOCAL DEMO"

    def draw(self, context):
        scene = context.scene
        box = self.layout.box()
        box.label(text="LOCAL DEMO  |  OFFLINE", icon="WORLD")
        box.label(text=f"{scene.get('wfrl_run_status', 'READY')}  |  {time_for_frame(scene.frame_current):05.2f} / 66.00 s")
        box.label(text="SYNTH / No physical backend", icon="INFO")


class WFRL_PT_Scene(WFRL_PT_Base, bpy.types.Panel):
    bl_label = "Scene"
    bl_idname = "WFRL_PT_scene"
    bl_options = {"DEFAULT_CLOSED"}
    bl_parent_id = "WFRL_PT_demo_panel"

    def draw(self, context):
        layout = self.layout
        layout.operator("wfrl.load_demo", icon="FILE_REFRESH")
        layout.label(text="turb3_demo / 3 × NREL 5MW")
        layout.label(text="Wind fixture 8.0 m/s @ 270 deg")
        layout.label(text="Terrain is decorative", icon="INFO")


class WFRL_PT_Views(WFRL_PT_Base, bpy.types.Panel):
    bl_label = "Views & Layers"
    bl_idname = "WFRL_PT_views"
    bl_options = {"DEFAULT_CLOSED"}
    bl_parent_id = "WFRL_PT_demo_panel"

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        for label in ("World", "Top", "Side"):
            row.operator("wfrl.select_camera", text=label).camera_name = "WFRL.Camera." + label
        layout.label(text="Gimbal camera")
        row = layout.row(align=True)
        for turbine in ("T1", "T2", "T3"):
            row.operator("wfrl.select_camera", text=turbine, icon="CAMERA_DATA").camera_name = f"WFRL.Camera.{turbine}.Gimbal"
        layout.operator("wfrl.dual_view", icon="SPLITSCREEN")
        layout.prop(context.scene, "wfrl_show_wake", text="Wake proxy / SYNTH")
        layout.prop(context.scene, "wfrl_show_lidar", text="T1 Lidar rays / SYNTH")
        layout.prop(context.scene, "wfrl_show_disxy", text="FAST.Farm DisXY / EXPORTED")
        layout.prop(context.scene, "wfrl_show_atmosphere", text="Atmosphere visual layer")
        layout.prop(context.scene, "wfrl_atmosphere_preset", text="Environment")
        layout.prop(context.scene, "wfrl_wake_quality", text="Visual quality")
        row = layout.row(align=True)
        row.operator("wfrl.capture_screenshot", icon="RENDER_STILL")
        row.operator("wfrl.capture_recording", icon="RENDER_ANIMATION")


class WFRL_PT_Run(WFRL_PT_Base, bpy.types.Panel):
    bl_label = "Run"
    bl_idname = "WFRL_PT_run"
    bl_parent_id = "WFRL_PT_demo_panel"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        status = scene.get("wfrl_run_status", "READY")
        layout.label(text=scene.get("wfrl_demo_phase", "Load the demo scene"), icon="TIME")
        row = layout.row(align=True)
        if status == "PAUSED":
            row.operator("wfrl.demo_resume", text="Resume", icon="PLAY")
            row.operator("wfrl.demo_step", text="Step", icon="NEXT_KEYFRAME")
        elif status in {"STARTING", "RUNNING"}:
            row.operator("wfrl.demo_pause", text="Pause", icon="PAUSE")
        else:
            row.operator("wfrl.demo_start", text="Start Demo", icon="PLAY")
        row.operator("wfrl.demo_stop", text="Stop", icon="SNAP_FACE")
        layout.operator("wfrl.demo_reset", icon="LOOP_BACK")
        layout.label(text="25 fps / fixed single 66 s sequence")


class WFRL_PT_Telemetry(WFRL_PT_Base, bpy.types.Panel):
    bl_label = "Telemetry / SYNTH"
    bl_idname = "WFRL_PT_telemetry"
    bl_parent_id = "WFRL_PT_demo_panel"

    def draw(self, context):
        layout, scene = self.layout, context.scene
        layout.prop(scene, "wfrl_selected_turbine", text="Focus")
        if not scene.wfrl_channel_telemetry:
            layout.label(text="Channel OFF / sample frozen", icon="HIDE_ON")
            return
        i = ("T1", "T2", "T3").index(scene.wfrl_selected_turbine)
        yaw, pitch, rpm = (scene.get(key, [0.0] * 3)[i] for key in ("wfrl_yaw_deg", "wfrl_pitch_deg", "wfrl_rpm"))
        box = layout.box()
        box.label(text=f"{scene.wfrl_selected_turbine} yaw {yaw:+.2f} deg")
        box.label(text=f"Pitch {pitch:.2f} deg / {rpm:.2f} rpm")
        if scene.get("wfrl_power_available", True):
            power = scene.get("wfrl_power_mw", [0.0] * 3)
            box.label(text=f"Power {power[i]:.2f} MW / SYNTH")
            box.label(text=f"Farm {sum(power):.2f} MW", icon="LIGHT")
        else:
            box.label(text="Power unavailable in manual pose")
        box.label(text=f"Script sample {scene.get('wfrl_telemetry_time_s', 0):.2f} s")


class WFRL_PT_Channels(WFRL_PT_Base, bpy.types.Panel):
    bl_label = "Channels / Offline Fixtures"
    bl_idname = "WFRL_PT_channels"
    bl_parent_id = "WFRL_PT_demo_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        self.layout.prop(context.scene, "wfrl_channel_telemetry", text="Sample & display telemetry")
        self.layout.label(text="Source: deterministic script / SYNTH")
        self.layout.label(text="No network or backend subscription")
        self.layout.label(text="DisXY: waits for FAST.Farm export")


class WFRL_PT_Manual(WFRL_PT_Base, bpy.types.Panel):
    bl_label = "Manual Pose / Paused Demo"
    bl_idname = "WFRL_PT_manual"
    bl_parent_id = "WFRL_PT_demo_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        scene, layout = context.scene, self.layout
        layout.label(text="Geometry preview only / SYNTH")
        controls = layout.column()
        controls.enabled = scene.get("wfrl_run_status") == "PAUSED"
        controls.prop(scene, "wfrl_manual_enabled", text="Override selected turbine pose")
        controls.prop(scene, "wfrl_selected_turbine", text="Turbine")
        controls.prop(scene, "wfrl_manual_yaw", text="Yaw (deg)")
        controls.prop(scene, "wfrl_manual_pitch", text="Pitch (deg)")
        layout.label(text="Resume restores scripted pose")


class WFRL_PT_DemoEvents(WFRL_PT_Base, bpy.types.Panel):
    bl_label = "Demo Events / SYNTH"
    bl_idname = "WFRL_PT_demo_events"
    bl_parent_id = "WFRL_PT_demo_panel"

    def draw(self, context):
        layout = self.layout
        t = time_for_frame(context.scene.frame_current)
        layout.label(text="Script stages / no SafetyLimiter", icon="INFO")
        for seconds, label in ((0, "Startup ramp"), (4, "Rotors at demo RPM"), (56, "Feather / decelerate"), (66, "Stopped")):
            row = layout.row()
            row.enabled = t >= seconds
            row.label(text=f"{seconds // 60:02d}:{seconds % 60:02d}  {label}", icon="CHECKMARK" if t >= seconds else "TIME")


class WFRL_PT_Fixtures(WFRL_PT_Base, bpy.types.Panel):
    bl_label = "Review Fixtures / SYNTH"
    bl_idname = "WFRL_PT_fixtures"
    bl_parent_id = "WFRL_PT_demo_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        self.layout.prop(context.scene, "wfrl_fixture_state", text="Preview")
        message = {"WAITING": "Fixture: waiting for first frame", "CHANNEL_OFF": "Fixture: sampling stopped", "STALE": "Fixture: stale / age 4.2 s", "INCOMPATIBLE": "Fixture: checkpoint mismatch", "FAILED": "Fixture: backend unavailable"}.get(context.scene.wfrl_fixture_state, "Fixture: nominal")
        self.layout.label(text=message, icon="INFO")
        self.layout.label(text="Static preview; does not change run")


CLASSES = (WFRL_OT_LoadDemo, WFRL_OT_DemoStart, WFRL_OT_DemoResume, WFRL_OT_DemoPause,
           WFRL_OT_DemoStep, WFRL_OT_DemoStop, WFRL_OT_DemoReset, WFRL_OT_SelectCamera,
           WFRL_OT_RenderStill, WFRL_OT_RenderAnimation, WFRL_OT_DualView,
           WFRL_PT_DemoPanel, WFRL_PT_Scene, WFRL_PT_Views, WFRL_PT_Run, WFRL_PT_Telemetry,
           WFRL_PT_Channels, WFRL_PT_Manual, WFRL_PT_DemoEvents, WFRL_PT_Fixtures)
