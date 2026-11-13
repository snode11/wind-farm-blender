"""Bounded world-space blade-tip trails for the Down/gimbal view.

The trail is a presentation aid attached to the existing replay timeline.  It
samples the actual (possibly deformed) blade mesh after replay writes its pose;
it does not infer a tip from the radar beams or alter the simulation pose.
"""

from collections import deque
import math
import numpy as np


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
    from . import playback
    return playback.is_playing(scene)


def opacity(age_s, lifetime_s=LIFETIME_S):
    """Smooth fade in simulation seconds, independent of render/playback FPS."""
    x = max(0.0, min(1.0, 1.0 - age_s / lifetime_s))
    return x * x * (3.0 - 2.0 * x)


def _simulation_time(scene):
    from . import clearance_replay
    reader = clearance_replay.reader_for(scene)
    if reader is not None:
        return clearance_replay.simulation_time(scene, reader)
    # Freeze the mapping for non-replay previews too. Render FPS may change.
    if 'wfrl_tip_trail_timebase_fps' not in scene:
        scene['wfrl_tip_trail_timebase_fps'] = scene.render.fps / scene.render.fps_base
    timebase = scene['wfrl_tip_trail_timebase_fps']
    if type(timebase) not in (int, float) or not math.isfinite(timebase) or timebase <= 0:
        raise ValueError('Invalid tip-trail timebase')
    return (scene.frame_current + scene.frame_subframe - scene.frame_start) / timebase


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


def _fade_material(name, color, alpha, *, created=None):
    import bpy
    material = bpy.data.materials.get(name)
    if material is not None:
        material.diffuse_color = (*color[:3], alpha)
        for node in material.node_tree.nodes:
            if node.type == 'EMISSION':
                node.inputs['Color'].default_value = color
        return material
    material = bpy.data.materials.new(name)
    if created is not None:
        created.append(('materials', material, None))
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


def _release_created(created, error):
    """Roll back owned IDs while retaining external links and data users."""
    import bpy
    for kind, block, collection in reversed(created):
        try:
            if collection is not None and collection.objects.get(block.name) == block:
                collection.objects.unlink(block)
            if block.users == 0 and block.library is None and not block.use_fake_user:
                getattr(bpy.data, kind).remove(block)
        except Exception as cleanup_error:
            error.add_note(f'Tip-trail {kind} cleanup failed: {cleanup_error}')


def _ensure_curve(scene, turbine_id, blade_id, *, created=None):
    import bpy
    name = f'WFRL.Turbine.{turbine_id}.TipTrail.B{blade_id}'
    obj = scene.objects.get(name)
    if obj is None:
        obj = next((item for item in scene.objects
                    if item.get('wfrl_tip_trail_turbine') == turbine_id
                    and item.get('blade_id') == blade_id), None)
    if obj is not None and (any(owner != scene for owner in getattr(obj, 'users_scene', ()))
            or getattr(obj.data, 'users', 1) > 1):
        raise ValueError('Tip-trail curve is shared with another object or scene')
    owned = []
    previous_materials = list(obj.data.materials) if obj is not None else None
    try:
        collection = _collection_for(scene, turbine_id)
        materials = [_fade_material(f'WFRL.TipTrail.B{blade_id}.Fade{i:02d}',
                               TRAIL_COLORS[blade_id], i / (FADE_LEVELS - 1), created=owned)
                     for i in range(FADE_LEVELS)]
        if obj is None:
            data = bpy.data.curves.new(name + '.Curve', 'CURVE')
            owned.append(('curves', data, None))
            data.dimensions = '3D'
            data.resolution_u = 1
            # The camera is a long-lens Down view; a slightly thicker line remains
            # legible against the blade and tower without obscuring the tip.
            data.bevel_depth = .08
            data.bevel_resolution = 2
            obj = bpy.data.objects.new(name, data)
            owned.append(('objects', obj, collection))
            collection.objects.link(obj)
        obj.data.materials.clear()
        for material in materials:
            obj.data.materials.append(material)
        obj['provenance'] = '叶尖运动轨迹辅助线；取形变后叶片网格世界坐标；不代表测量或安全边界'
        obj['blade_id'] = blade_id
        obj['wfrl_tip_trail_turbine'] = turbine_id
        obj.hide_render = True
    except Exception as error:
        if previous_materials is not None:
            try:
                obj.data.materials.clear()
                for material in previous_materials:
                    obj.data.materials.append(material)
            except Exception as cleanup_error:
                error.add_note(f'Tip-trail materials restore failed: {cleanup_error}')
        _release_created(owned, error)
        raise
    if created is not None:
        created.extend(owned)
    return obj


def _tip_world(obj, depsgraph=None, *, scene=None):
    """Follow the stable apex authored by turbine_geometry.blade_mesh()."""
    # blade_mesh appends its apex last. Re-selecting max local Z after flexing
    # can jump onto an adjacent surface vertex instead of following that apex.
    vertices = getattr(obj.data, 'vertices', ())
    if not vertices:
        return None
    from . import farm_flex
    flex = farm_flex.active_for(scene)
    matrix = obj.matrix_world if flex is None else flex.world_matrix(obj)
    return matrix @ vertices[-1].co


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
        self.objects = {}
        created = []
        try:
            for bid in (1, 2, 3):
                self.objects[bid] = _ensure_curve(scene, self.turbine_id, bid, created=created)
            self._geometry_revision = {bid: 0 for bid in (1, 2, 3)}
            self._drawn = {bid: None for bid in (1, 2, 3)}
            self.enabled = True
            self.last_frame = None
            self._set_visible(True)
            self.scene['wfrl_tip_trail_turbine'] = self.turbine_id
            self.scene['wfrl_tip_trail_max_points'] = max_points
            self.scene['wfrl_tip_trail_lifetime_s'] = self.lifetime_s
            self.scene['wfrl_tip_trail_hold_s'] = HOLD_S
            self.scene['wfrl_tip_trail_sample_interval_s'] = self.interval_s
            self.scene['wfrl_tip_trail_enabled'] = True
        except Exception as error:
            _release_created(created, error)
            raise

    def _set_visible(self, visible):
        try:
            layers = getattr(self.scene, 'view_layers', None)
            layers = list(layers) if layers is not None else [None]
        except ReferenceError:
            layers = []
        for obj in self.objects.values():
            try:
                for layer in layers:
                    if layer is None:
                        obj.hide_set(not visible)
                    elif obj.name in layer.objects:
                        obj.hide_set(not visible, view_layer=layer)
                obj.hide_render = not visible
            except ReferenceError:
                pass

    def _clear_curve(self, obj):
        data = obj.data
        if data is not None:
            data.splines.clear()

    def clear(self, reason='manual'):
        for bid, obj in self.objects.items():
            self.points[bid].clear()
            self.times[bid].clear()
            self.completed[bid].clear()
            try:
                self._clear_curve(obj)
            except ReferenceError:
                pass
            self._geometry_revision[bid] = 0
            self._drawn[bid] = None
        self.last_frame = None
        self.last_time = None
        self.last_angle = None
        self.turn_angle = 0.0
        self.compare_since = None
        try:
            self.scene['wfrl_tip_trail_clear_reason'] = str(reason)
        except ReferenceError:
            pass

    def disable(self, reason='disabled'):
        self.enabled = False
        self.clear(reason)
        self._set_visible(False)
        try:
            self.scene['wfrl_tip_trail_enabled'] = False
        except ReferenceError:
            pass

    def enable(self):
        if self.enabled:
            return
        self.enabled = True
        self._set_visible(True)
        self.scene['wfrl_tip_trail_enabled'] = True
        self.last_frame = None  # next update starts exactly at current frame
        self._drawn = {bid: None for bid in (1, 2, 3)}

    def _redraw(self, bid, now):
        alpha = 1.0 if self.compare_since is None else opacity(
            max(0.0, now - self.compare_since - HOLD_S))
        level = round(alpha * (FADE_LEVELS - 1))
        revision = self._geometry_revision[bid]
        drawn = self._drawn[bid]
        if drawn == (revision, level):
            return
        obj = self.objects[bid]
        if drawn is not None and drawn[0] == revision and drawn[1] > 0 and level > 0:
            for spline in obj.data.splines:
                spline.material_index = level
            self._drawn[bid] = (revision, level)
            return
        obj.data.splines.clear()
        groups = [(level, coordinates) for coordinates in
                  [*self.completed[bid], list(self.points[bid])]
                  if len(coordinates) >= 2 and level > 0]
        for level, coordinates in groups:
            spline = obj.data.splines.new('POLY')
            spline.material_index = level
            spline.points.add(len(coordinates) - 1)
            coordinates4 = np.empty((len(coordinates), 4), dtype=np.float32)
            coordinates4[:, :3] = coordinates
            coordinates4[:, 3] = 1.0
            spline.points.foreach_set('co', coordinates4.ravel())
        self._drawn[bid] = (revision, level)

    def update(self, scene, depsgraph=None):
        if scene != self.scene or not self.enabled:
            return
        frame = int(scene.frame_current)
        try:
            now = _simulation_time(scene)
        except (ValueError, TypeError, ZeroDivisionError, OverflowError):
            self.clear('time_unavailable')
            return
        if not math.isfinite(now):
            self.clear('time_unavailable')
            return
        rotor = scene.objects.get(f'WFRL.Turbine.{self.turbine_id}.Rotor')
        if rotor is None or not math.isfinite(float(rotor.rotation_euler.x)):
            self.clear('rotor_unavailable')
            return
        angle = float(rotor.rotation_euler.x)
        if self.last_time == now:
            return  # paused redraws cannot age or brighten the trail
        if self.last_time is not None and (now < self.last_time or
                (frame > self.last_frame + 1 and not _is_playing(scene))):
            self.clear('timeline_seek')
        self.last_frame = frame
        self.last_time = now
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
            geometry_changed = False
            blade = scene.objects.get(f'WFRL.Turbine.{self.turbine_id}.Blade{bid}')
            point = _tip_world(blade, depsgraph, scene=scene) if blade is not None else None
            if point is None or not all(math.isfinite(float(v)) for v in point):
                self.clear('tip_unavailable')
                return
            xyz = tuple(float(v) for v in point)
            if crossed and points:
                geometry_changed = True
                boundary = tuple(a + fraction * (b - a) for a, b in zip(points[-1], xyz))
                self.completed[bid].append([*points, boundary])
                points.clear()
                times.clear()
                if len(self.completed[bid]) < 2:
                    points.append(boundary)
                    times.append(now)
            if len(self.completed[bid]) < 2:
                if len(points) >= self.max_points - 1:
                    geometry_changed = True
                    # Preserve the entire revolution even at low rotor speed.
                    kept = list(zip(times, points))[::2]
                    times.clear()
                    points.clear()
                    for t, p in kept:
                        times.append(t)
                        points.append(p)
                if not crossed and len(times) > 1 and math.floor((times[-1] + 1e-9) / self.interval_s) == bucket:
                    geometry_changed = geometry_changed or points[-1] != xyz
                    times[-1], points[-1] = now, xyz
                else:
                    geometry_changed = True
                    times.append(now)
                    points.append(xyz)
            if geometry_changed:
                self._geometry_revision[bid] += 1
        if crossed:
            self.turn_angle = max(0.0, self.turn_angle - math.tau)
            if len(self.completed[1]) == 2:
                self.compare_since = now
        for bid in (1, 2, 3):
            self._redraw(bid, now)


def active(scene):
    key = None
    try:
        key = _scene_key(scene)
        trail = _ACTIVE.get(key)
        if trail is not None and trail.scene == scene:
            _scene_key(trail.scene)
            return trail
    except ReferenceError:
        _ACTIVE.pop(key, None)
    return None


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
        try:
            trail.update(scene, depsgraph)
        except ReferenceError:
            discard(scene, 'scene_data_unavailable')


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
    errors = []
    for trail in list(_ACTIVE.values()):
        try:
            trail.disable('addon_unload')
        except ReferenceError:
            pass
        except Exception as exc:
            errors.append(exc)
    _ACTIVE.clear()
    if on_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(on_load_pre)
    if update in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(update)
    if errors:
        raise RuntimeError('Tip trail cleanup failed: ' + '; '.join(map(str, errors)))
