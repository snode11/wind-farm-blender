"""Bounded world-space blade-tip trails for the Down/gimbal view.

The trail is a presentation aid attached to the existing replay timeline.  It
samples the actual (possibly deformed) blade mesh after the frame is evaluated;
it does not infer a tip from the radar beams or alter the simulation pose.
"""

from collections import deque
import math


HOLD_S = 2.0
LIFETIME_S = 0.75  # whole-pair fade after the comparison hold
SAMPLE_INTERVAL_S = 1.0 / 40.0
FADE_LEVELS = 32
MAX_POINTS = 512  # per revolution; compact samples instead of losing the start
_ACTIVE = {}

# High-contrast colours are deliberately stable by blade number (B1/B2/B3).
TRAIL_COLORS = {
    1: (1.0, 0.05, 0.04, 1.0),
    2: (0.05, 1.0, 0.12, 1.0),
    3: (0.05, 0.35, 1.0, 1.0),
}


def _scene_key(scene):
    try:
        return scene.as_pointer()
    except AttributeError:
        return id(scene)


def _is_playing(scene):
    import bpy
    return any(window.scene == scene and window.screen.is_animation_playing
               for window in bpy.context.window_manager.windows)


def opacity(age_s, lifetime_s=LIFETIME_S):
    """Smooth fade in simulation seconds, independent of render/playback FPS."""
    x = max(0.0, min(1.0, 1.0 - age_s / lifetime_s))
    return x * x * (3.0 - 2.0 * x)


def _simulation_time(scene):
    from . import clearance_replay
    reader = clearance_replay.reader_for(scene)
    if reader is not None:
        timebase = scene['wfrl_clearance_timebase_fps']
        elapsed = (scene.frame_current + scene.frame_subframe - scene.frame_start) / timebase
        return min(reader.end_s, max(reader.start_s, reader.start_s + elapsed))
    # Freeze the mapping for non-replay previews too. Render FPS may change.
    if 'wfrl_tip_trail_timebase_fps' not in scene:
        scene['wfrl_tip_trail_timebase_fps'] = scene.render.fps / scene.render.fps_base
    return (scene.frame_current + scene.frame_subframe - scene.frame_start) / scene['wfrl_tip_trail_timebase_fps']


def _material(name, color):
    import bpy
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.diffuse_color = color
    material.use_nodes = True
    node = next((n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if node is not None:
        node.inputs['Base Color'].default_value = color
        node.inputs['Emission Color'].default_value = color
        node.inputs['Emission Strength'].default_value = 2.0
        node.inputs['Roughness'].default_value = .35
    return material


def _fade_material(name, color, alpha):
    import bpy
    material = bpy.data.materials.get(name)
    if material is not None:
        material.diffuse_color = (*color[:3], alpha)
        for node in material.node_tree.nodes:
            if node.type == 'EMISSION':
                node.inputs['Color'].default_value = color
        return material
    material = bpy.data.materials.new(name)
    material.diffuse_color = (*color[:3], alpha)
    material.use_nodes = True
    if hasattr(material, 'surface_render_method'):
        material.surface_render_method = 'DITHERED'
    elif hasattr(material, 'blend_method'):
        material.blend_method = 'HASHED'
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new('ShaderNodeOutputMaterial')
    transparent = nodes.new('ShaderNodeBsdfTransparent')
    emission = nodes.new('ShaderNodeEmission')
    emission.inputs['Color'].default_value = color
    emission.inputs['Strength'].default_value = 2.0
    mix = nodes.new('ShaderNodeMixShader')
    mix.inputs[0].default_value = alpha
    links = material.node_tree.links
    links.new(transparent.outputs[0], mix.inputs[1])
    links.new(emission.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], output.inputs['Surface'])
    return material


def _collection_for(scene, turbine_id):
    obj = scene.objects.get(f'WFRL.Turbine.{turbine_id}.YawRoot')
    if obj is None or not obj.users_collection:
        raise ValueError('Tip-trail turbine is absent from scene: ' + turbine_id)
    return obj.users_collection[0]


def _ensure_curve(scene, turbine_id, blade_id):
    import bpy
    name = f'WFRL.Turbine.{turbine_id}.TipTrail.B{blade_id}'
    materials = [_fade_material(f'WFRL.TipTrail.B{blade_id}.Fade{i:02d}',
                           TRAIL_COLORS[blade_id], i / (FADE_LEVELS - 1))
                 for i in range(FADE_LEVELS)]
    obj = scene.objects.get(name)
    if obj is None:
        data = bpy.data.curves.new(name + '.Curve', 'CURVE')
        data.dimensions = '3D'
        data.resolution_u = 1
        # The camera is a long-lens Down view; a slightly thicker line remains
        # legible against the blade and tower without obscuring the tip.
        data.bevel_depth = .08
        data.bevel_resolution = 2
        obj = bpy.data.objects.new(name, data)
        _collection_for(scene, turbine_id).objects.link(obj)
    obj.data.materials.clear()
    for material in materials:
        obj.data.materials.append(material)
    obj['provenance'] = '叶尖运动轨迹辅助线；取形变后叶片网格世界坐标；不代表测量或安全边界'
    obj['blade_id'] = blade_id
    obj.hide_render = True
    return obj


def _tip_world(obj, depsgraph=None):
    """Follow the stable apex authored by turbine_geometry.blade_mesh()."""
    # blade_mesh appends its apex last. Re-selecting max local Z after flexing
    # can jump onto an adjacent surface vertex instead of following that apex.
    vertices = getattr(obj.data, 'vertices', ())
    if not vertices:
        return None
    return obj.matrix_world @ vertices[-1].co


class TipTrail:
    """One scene's three bounded tip curves and timeline bookkeeping."""

    def __init__(self, scene, turbine_id='T1', max_points=MAX_POINTS):
        if type(max_points) is not int or max_points < 3:
            raise ValueError('Tip trail buffer must contain at least three points')
        self.scene = scene
        self.turbine_id = str(turbine_id)
        self.max_points = max_points
        self.lifetime_s = LIFETIME_S
        self.interval_s = SAMPLE_INTERVAL_S
        self.times = {bid: deque(maxlen=max_points) for bid in (1, 2, 3)}
        self.last_time = None
        self.completed = {bid: [] for bid in (1, 2, 3)}
        self.last_angle = None
        self.turn_angle = 0.0
        self.compare_since = None
        self.points = {bid: deque(maxlen=max_points) for bid in (1, 2, 3)}
        self.objects = {bid: _ensure_curve(scene, self.turbine_id, bid) for bid in (1, 2, 3)}
        self.enabled = True
        self.last_frame = None
        self.scene['wfrl_tip_trail_enabled'] = True
        self.scene['wfrl_tip_trail_turbine'] = self.turbine_id
        self.scene['wfrl_tip_trail_max_points'] = max_points
        self.scene['wfrl_tip_trail_lifetime_s'] = self.lifetime_s
        self.scene['wfrl_tip_trail_hold_s'] = HOLD_S
        self.scene['wfrl_tip_trail_sample_interval_s'] = self.interval_s
        self._set_visible(True)

    def _set_visible(self, visible):
        for obj in self.objects.values():
            obj.hide_set(not visible)
            obj.hide_render = not visible

    def _clear_curve(self, obj):
        obj.data.splines.clear()

    def clear(self, reason='manual'):
        for bid, obj in self.objects.items():
            self.points[bid].clear()
            self.times[bid].clear()
            self.completed[bid].clear()
            self._clear_curve(obj)
        self.last_frame = None
        self.last_time = None
        self.last_angle = None
        self.turn_angle = 0.0
        self.compare_since = None
        self.scene['wfrl_tip_trail_clear_reason'] = str(reason)

    def disable(self, reason='disabled'):
        self.enabled = False
        self.clear(reason)
        self._set_visible(False)
        self.scene['wfrl_tip_trail_enabled'] = False

    def enable(self):
        if self.enabled:
            return
        self.enabled = True
        self._set_visible(True)
        self.scene['wfrl_tip_trail_enabled'] = True
        self.last_frame = None  # next update starts exactly at current frame

    def _redraw(self, bid, now):
        obj = self.objects[bid]
        obj.data.splines.clear()
        alpha = 1.0 if self.compare_since is None else opacity(
            max(0.0, now - self.compare_since - HOLD_S))
        level = round(alpha * (FADE_LEVELS - 1))
        groups = [(level, coordinates) for coordinates in
                  [*self.completed[bid], list(self.points[bid])]
                  if len(coordinates) >= 2 and level > 0]
        for level, coordinates in groups:
            spline = obj.data.splines.new('POLY')
            spline.material_index = level
            spline.points.add(len(coordinates) - 1)
            spline.points.foreach_set('co', [v for xyz in coordinates for v in (*xyz, 1.0)])

    def update(self, scene, depsgraph=None):
        if scene != self.scene or not self.enabled:
            return
        frame = int(scene.frame_current)
        now = _simulation_time(scene)
        if self.last_time == now:
            return  # paused redraws cannot age or brighten the trail
        if self.last_time is not None and (now < self.last_time or
                (frame > self.last_frame + 1 and not _is_playing(scene))):
            self.clear('timeline_seek')
        self.last_frame = frame
        self.last_time = now
        rotor = scene.objects.get(f'WFRL.Turbine.{self.turbine_id}.Rotor')
        if rotor is None:
            return
        angle = float(rotor.rotation_euler.x)
        if self.compare_since is not None:
            if now - self.compare_since < HOLD_S + LIFETIME_S:
                for bid in (1, 2, 3):
                    self._redraw(bid, now)
                return
            self.clear('comparison_complete')
            self.last_frame, self.last_time = frame, now
        # Unwrap the actual rotor pose, including its 360 -> 0 degree seam.
        delta = 0.0 if self.last_angle is None else abs(
            (angle - self.last_angle + math.pi) % math.tau - math.pi)
        previous_angle = self.turn_angle
        self.turn_angle += delta
        self.last_angle = angle
        crossed = self.turn_angle >= math.tau - 1e-7
        fraction = min(1.0, max(0.0, (math.tau - previous_angle) / delta)) if crossed and delta else 1.0
        bucket = math.floor((now + 1e-9) / self.interval_s)
        for bid in (1, 2, 3):
            points, times = self.points[bid], self.times[bid]
            blade = scene.objects.get(f'WFRL.Turbine.{self.turbine_id}.Blade{bid}')
            point = _tip_world(blade, depsgraph) if blade is not None else None
            if point is None or not all(math.isfinite(float(v)) for v in point):
                self.clear('tip_unavailable')
                return
            xyz = tuple(float(v) for v in point)
            if crossed and points:
                boundary = tuple(a + fraction * (b - a) for a, b in zip(points[-1], xyz))
                self.completed[bid].append([*points, boundary])
                points.clear()
                times.clear()
                if len(self.completed[bid]) < 2:
                    points.append(boundary)
                    times.append(now)
            if len(self.completed[bid]) < 2:
                if len(points) >= self.max_points - 1:
                    # Preserve the entire revolution even at low rotor speed.
                    kept = list(zip(times, points))[::2]
                    times.clear()
                    points.clear()
                    for t, p in kept:
                        times.append(t)
                        points.append(p)
                if not crossed and len(times) > 1 and math.floor((times[-1] + 1e-9) / self.interval_s) == bucket:
                    times[-1], points[-1] = now, xyz
                else:
                    times.append(now)
                    points.append(xyz)
        if crossed:
            self.turn_angle = max(0.0, self.turn_angle - math.tau)
            if len(self.completed[1]) == 2:
                self.compare_since = now
        for bid in (1, 2, 3):
            self._redraw(bid, now)


def active(scene):
    trail = _ACTIVE.get(_scene_key(scene))
    return trail if trail is not None and trail.scene == scene else None


def enable(scene, turbine_id='T1', max_points=MAX_POINTS):
    """Enable trails and sample the current frame on the next update."""
    # Put the sampler at the end of frame handlers so a flexible blade preview
    # (which may be attached later) has already written the current mesh pose.
    try:
        import bpy
        handlers = bpy.app.handlers.frame_change_post
        if update in handlers:
            handlers.remove(update)
        handlers.append(update)
    except (ImportError, AttributeError):
        pass
    trail = active(scene)
    if trail is None or trail.turbine_id != str(turbine_id) or trail.max_points != max_points:
        if trail is not None:
            trail.disable('turbine_changed')
        trail = TipTrail(scene, turbine_id, max_points)
        _ACTIVE[_scene_key(scene)] = trail
    else:
        trail.enable()
    update(scene)
    return trail


def disable(scene, reason='disabled'):
    trail = active(scene)
    if trail is not None:
        trail.disable(reason)


def discard(scene, reason='scene_rebuild'):
    trail = _ACTIVE.pop(_scene_key(scene), None)
    if trail is not None:
        try:
            trail.disable(reason)
        except ReferenceError:
            pass  # The scene may already have been deleted externally.


def reset(scene, reason='reset'):
    trail = active(scene)
    if trail is not None:
        trail.clear(reason)


def update(scene, depsgraph=None):
    trail = active(scene)
    if trail is not None:
        from . import clearance_replay
        if scene.get('wfrl_scene_kind') == 'clearance_replay' and clearance_replay.reader_for(scene) is None:
            trail.clear('replay_unavailable')
            return
        trail.update(scene, depsgraph)


def on_load_pre(_unused):
    # Drop references before Blender frees the old scene and curve datablocks.
    _ACTIVE.clear()


def register():
    import bpy
    from bpy.app.handlers import persistent
    persistent(update)
    persistent(on_load_pre)
    if on_load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(on_load_pre)
    if update not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(update)


def unregister():
    import bpy
    for trail in list(_ACTIVE.values()):
        trail.disable('addon_unload')
    _ACTIVE.clear()
    if on_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(on_load_pre)
    if update in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(update)
