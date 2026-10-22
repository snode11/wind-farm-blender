"""Native workflow configuration; launch options map directly to existing backends."""
import bpy
from .. import runtime
from ..preferences import get_preferences


class WFRL_WorkflowSettings(bpy.types.PropertyGroup):
    override_scene: bpy.props.BoolProperty(name='Override scene configuration', default=False)
    backend: bpy.props.EnumProperty(name='Backend', items=[('fastfarm', 'FAST.Farm', ''), ('floris', 'FLORIS', '')])
    wind_speed: bpy.props.FloatProperty(name='Requested wind (m/s)', default=8, min=3, max=28)
    wind_direction: bpy.props.FloatProperty(name='Requested direction (deg)', default=270, min=0, max=359.999)
    turbulence: bpy.props.StringProperty(name='Turbulence box')
    terrain: bpy.props.EnumProperty(name='Decorative terrain', items=[('none', 'None', ''), ('flat', 'Flat', ''), ('gobi', 'Gobi', ''), ('mountains', 'Mountains', '')])
    controls: bpy.props.EnumProperty(name='Control channels', items=[('yaw', 'Yaw', ''), ('yaw,pitch', 'Yaw / Pitch', ''), ('yaw,pitch,torque', 'Yaw / Pitch / Torque', '')])
    iters: bpy.props.IntProperty(name='Iterations', default=8, min=1)
    n_steps: bpy.props.IntProperty(name='Rollout steps', default=64, min=1)
    warmup_steps: bpy.props.IntProperty(name='Warmup steps', default=4, min=0)
    seed: bpy.props.IntProperty(name='Seed', default=0, min=0)
    checkpoint: bpy.props.StringProperty(name='Checkpoint', subtype='FILE_PATH')
    replay_steps: bpy.props.IntProperty(name='Replay steps', default=400, min=1)
    topic: bpy.props.StringProperty(name='Sensor topic', default='lidar')


def scene_overrides(settings):
    if not settings.override_scene:
        return {}
    return dict(backend=settings.backend, terrain=None if settings.terrain == 'none' else settings.terrain,
                controls=settings.controls.split(','), inflow=dict(speed=settings.wind_speed,
                direction=settings.wind_direction, turbulence=settings.turbulence or None))


def launch_options(context, mode):
    settings = context.scene.wfrl_workflow
    prefs = get_preferences(context)
    options = {'scene': bpy.path.abspath(prefs.default_scene) if prefs else ''}
    if not options['scene']:
        raise ValueError('Choose a scene YAML in Preferences')
    overrides = scene_overrides(settings)
    if overrides: options['scene_overrides'] = overrides
    if mode in {'interactive_training', 'formal_training'}:
        options.update({key: getattr(settings, key) for key in ('iters', 'n_steps', 'warmup_steps', 'seed')})
    if mode == 'replay':
        if not settings.checkpoint: raise ValueError('Choose a compatible checkpoint for Replay')
        options.update(ckpt_path=bpy.path.abspath(settings.checkpoint), replay_steps=settings.replay_steps,
                       warmup_steps=settings.warmup_steps, seed=settings.seed)
    elif mode == 'formal_training' and settings.checkpoint:
        options['resume_from'] = bpy.path.abspath(settings.checkpoint)
    return options


class WFRL_OT_LoadConfiguredScene(bpy.types.Operator):
    bl_idname = 'wfrl.load_configured_scene'
    bl_label = 'Load & Validate Scene'

    @classmethod
    def poll(cls, context):
        return runtime.configuration_editable() and runtime.get_state().connection == 'CONNECTED'

    def execute(self, context):
        try:
            prefs = get_preferences(context)
            if not prefs or not prefs.default_scene: raise ValueError('Choose a scene YAML in Preferences')
            runtime.send_workflow_command('scene.load', {'scene': bpy.path.abspath(prefs.default_scene),
                'scene_overrides': scene_overrides(context.scene.wfrl_workflow)})
        except ValueError as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_SetChannel(bpy.types.Operator):
    bl_idname = 'wfrl.set_channel'
    bl_label = 'Set Sensor Subscription'
    enabled: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        try:
            runtime.send_workflow_command('channel.set', {'topic': context.scene.wfrl_workflow.topic,
                                                         'enabled': self.enabled})
        except ValueError as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}
        return {'FINISHED'}


CLASSES = (WFRL_WorkflowSettings, WFRL_OT_LoadConfiguredScene, WFRL_OT_SetChannel)


def register_properties():
    bpy.types.Scene.wfrl_workflow = bpy.props.PointerProperty(type=WFRL_WorkflowSettings)


def unregister_properties():
    if hasattr(bpy.types.Scene, 'wfrl_workflow'): del bpy.types.Scene.wfrl_workflow
