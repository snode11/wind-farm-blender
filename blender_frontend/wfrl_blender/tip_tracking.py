"""Bounded world-space blade-tip trails for the Down/gimbal view.

The trail is a presentation aid attached to the existing replay timeline.  It
samples the actual (possibly deformed) blade mesh after the frame is evaluated;
it does not infer a tip from the radar beams or alter the simulation pose.
"""

from collections import deque
import math


MAX_POINTS = 240
_ACTIVE = {}

# High-contrast colours are deliberately stable by blade number (B1/B2/B3).
TRAIL_COLORS = {
    1: (1.0, 0.16, 0.04, 1.0),
    2: (0.05, 0.72, 1.0, 1.0),
    3: (0.95, 0.05, 0.08, 1.0),
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


def _collection_for(scene, turbine_id):
    obj = scene.objects.get(f'WFRL.Turbine.{turbine_id}.YawRoot')
    if obj is None or not obj.users_collection:
        raise ValueError('Tip-trail turbine is absent from scene: ' + turbine_id)
    return obj.users_collection[0]


def _ensure_curve(scene, turbine_id, blade_id):
    import bpy
    name = f'WFRL.Turbine.{turbine_id}.TipTrail.B{blade_id}'
    material = _material('WFRL.TipTrail.B' + str(blade_id), TRAIL_COLORS[blade_id])
    obj = scene.objects.get(name)
    if obj is None:
        data = bpy.data.curves.new(name + '.Curve', 'CURVE')
        data.dimensions = '3D'
        data.resolution_u = 1
        # The camera is a long-lens Down view; a slightly thicker line remains
        # legible against the blade and tower without obscuring the tip.
        data.bevel_depth = .08
        data.bevel_resolution = 2
        data.materials.append(material)
        obj = bpy.data.objects.new(name, data)
        _collection_for(scene, turbine_id).objects.link(obj)
    elif not obj.data.materials:
        obj.data.materials.append(material)
    else:
        obj.data.materials[0] = material
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
        if type(max_points) is not int or max_points < 2:
            raise ValueError('Tip trail buffer must contain at least two points')
        self.scene = scene
        self.turbine_id = str(turbine_id)
        self.max_points = max_points
        self.points = {bid: deque(maxlen=max_points) for bid in (1, 2, 3)}
        self.objects = {bid: _ensure_curve(scene, self.turbine_id, bid) for bid in (1, 2, 3)}
        self.enabled = True
        self.last_frame = None
        self.scene['wfrl_tip_trail_enabled'] = True
        self.scene['wfrl_tip_trail_turbine'] = self.turbine_id
        self.scene['wfrl_tip_trail_max_points'] = max_points
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
            self._clear_curve(obj)
        self.last_frame = None
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

    def _redraw(self, bid):
        obj = self.objects[bid]
        # Reuse one polyline per blade.  Replacing the spline keeps redraws
        # bounded during 60 FPS playback instead of leaving one spline behind
        # for every frame.
        obj.data.splines.clear()
        spline = obj.data.splines.new('POLY') if self.points[bid] else None
        if spline is None:
            return
        spline.points.add(len(self.points[bid]) - 1)
        for point, xyz in zip(spline.points, self.points[bid]):
            point.co = (*xyz, 1.0)

    def update(self, scene, depsgraph=None):
        if scene != self.scene or not self.enabled:
            return
        frame = int(scene.frame_current)
        if self.last_frame == frame:
            return  # redraws and paused playback never duplicate samples
        if self.last_frame is not None and (frame < self.last_frame or
                (frame > self.last_frame + 1 and not _is_playing(scene))):
            # A slider seek, restart, or reverse step starts a fresh visible
            # pass.  This prevents a misleading line across unrelated times.
            self.clear('timeline_seek')
        self.last_frame = frame
        for bid in (1, 2, 3):
            blade = scene.objects.get(f'WFRL.Turbine.{self.turbine_id}.Blade{bid}')
            point = _tip_world(blade, depsgraph) if blade is not None else None
            if point is None or not all(math.isfinite(float(v)) for v in point):
                continue
            self.points[bid].append(tuple(float(v) for v in point))
            self._redraw(bid)


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
