"""Explicitly attached source-only flexible mesh preview, using real surfaces."""
import hashlib
import json
from pathlib import Path
import numpy as np

_ACTIVE = None


_TRAIL_COLLECTION = 'WFRL_FlexTipTrails'
# Keep the legend stable across the Blender and OpenCV previews: B1 orange,
# B2 blue, B3 red.
_TRAIL_COLORS = ((1.0, 0.32, 0.04, 1.0), (0.05, 0.45, 1.0, 1.0),
                 (0.95, 0.05, 0.08, 1.0))


def _trail_material(index):
    """Create a bright viewport material for one blade's tip trail."""
    import bpy
    name = f'WFRL.FlexTipTrail.Material{index}'
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.diffuse_color = _TRAIL_COLORS[index - 1]
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get('Principled BSDF')
    if bsdf is not None:
        color = _TRAIL_COLORS[index - 1]
        bsdf.inputs['Base Color'].default_value = color
        bsdf.inputs['Emission Color'].default_value = color
        bsdf.inputs['Emission Strength'].default_value = 3.0
        bsdf.inputs['Roughness'].default_value = .28
    return material


def _trail_collection(scene):
    import bpy
    collection = bpy.data.collections.get(_TRAIL_COLLECTION)
    if collection is None:
        collection = bpy.data.collections.new(_TRAIL_COLLECTION)
        scene.collection.children.link(collection)
    elif collection.name not in {c.name for c in scene.collection.children}:
        scene.collection.children.link(collection)
    return collection


def _remove_trails(scene):
    import bpy
    collection = bpy.data.collections.get(_TRAIL_COLLECTION)
    if collection is None:
        return
    for obj in list(collection.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def _make_trails(scene):
    """Build reusable curve objects; point coordinates are replaced per frame."""
    import bpy
    collection = _trail_collection(scene)
    trails = []
    for bid in (1, 2, 3):
        name = f'WFRL.FlexTipTrail.B{bid}'
        curve = bpy.data.curves.get(name + '.Data') or bpy.data.curves.new(name + '.Data', 'CURVE')
        curve.dimensions = '3D'
        curve.resolution_u = 2
        curve.bevel_depth = .12
        curve.bevel_resolution = 2
        curve.materials.clear()
        curve.materials.append(_trail_material(bid))
        spline = curve.splines[0] if curve.splines else curve.splines.new('POLY')
        spline.type = 'POLY'
        spline.points.add(1)
        spline.points[0].co = (0, 0, 0, 1)
        spline.points[1].co = (0, 0, 0, 1)
        obj = bpy.data.objects.get(name)
        if obj is None:
            obj = bpy.data.objects.new(name, curve)
            collection.objects.link(obj)
        elif obj not in collection.objects:
            collection.objects.link(obj)
        obj.hide_render = False
        obj.hide_set(False)
        obj['role'] = 'flexible blade tip trajectory'
        trails.append(obj)
    return trails


def _set_trail_points(obj, points):
    curve = obj.data
    spline = curve.splines[0] if curve.splines else curve.splines.new('POLY')
    spline.type = 'POLY'
    count = max(2, len(points))
    if len(spline.points) > count:
        # Curve points cannot be sliced; recreate the spline when seeking back
        # or changing the clip length to keep the trail deterministic.
        curve.splines.clear()
        spline = curve.splines.new('POLY')
        spline.points.add(count - 1)
    elif len(spline.points) < count:
        spline.points.add(count - len(spline.points))
    # ``points`` is normally a NumPy array from ``_tip_positions``; testing
    # it directly for truth raises the ambiguous-array ValueError and leaves
    # the toggle enabled without creating any curves.
    values = list(points) if points is not None and len(points) else [(0.0, 0.0, 0.0)] * 2
    if len(values) == 1:
        values.append(values[0])
    for point, value in zip(spline.points, values):
        point.co = (*map(float, value), 1.0)
    # Curve datablocks do not expose Mesh's ``update()`` method in Blender
    # 5.x; assigning spline coordinates already dirties the object.  Tag the
    # object when available so viewport redraws immediately.
    if hasattr(obj, 'update_tag'):
        obj.update_tag(refresh={'DATA'})


def set_tip_trails(scene, enabled, *, reset=False):
    """Show or hide the per-blade tip trajectories in the active scene."""
    enabled = bool(enabled)
    scene['wfrl_flex_tip_trails'] = enabled
    if not enabled:
        collection = __import__('bpy').data.collections.get(_TRAIL_COLLECTION)
        if collection:
            collection.hide_viewport = True
            collection.hide_render = True
        return
    if _ACTIVE is None or _ACTIVE.scene != scene or not _ACTIVE.enabled:
        return
    _ACTIVE.ensure_trails(reset=reset)


def is_active(scene):
    from . import clearance_replay
    try:
        return (_ACTIVE is not None and _ACTIVE.enabled and _ACTIVE.scene == scene
                and clearance_replay.reader_for(scene) is _ACTIVE.reader)
    except ReferenceError:
        return False


class FlexPreview:
    def __init__(self, scene, path, source_manifest=None):
        import bpy
        from . import clearance_replay
        from .turbine_geometry import geometry_data
        self.scene = scene
        reader = clearance_replay.reader_for(scene)
        self.reader = reader
        with np.load(path, allow_pickle=False) as archive:
            self.times = archive['times'].copy()
            self.transforms = archive['transforms'].copy()
            self.meta = json.loads(str(archive['metadata']))
        manifest = (Path(source_manifest) if source_manifest else
                    Path(scene.wfrl_clearance_near_tower_path)/'manifest.json')
        digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        if self.meta['package_manifest_sha256'] != digest or scene['wfrl_clearance_demo'] != 'near_tower':
            raise ValueError('Flex geometry and clearance package must match')
        if (self.meta['schema'] != 'wfrl.section-transforms.preview.v1'
                or self.meta['turbine_id'] != 'T1'
                or len(self.times) < 2
                or self.transforms.shape != (len(self.times), 3, 19, 3, 4)
                or not np.isfinite(self.transforms).all()
                or np.any(np.diff(self.times) <= 0)
                or abs(self.times[0]-reader.start_s)>1e-8
                or abs(self.times[-1]-reader.end_s)>1e-8):
            raise ValueError('Invalid or incomplete flexible preview')
        # This initial pose matches the archived t=0 fixed yaw/pitch NREL5MW run.
        motion = reader.at(reader.start_s)['motion']
        if motion['yaw_deg'] != 0 or any(motion['pitch_deg']):
            raise ValueError('Preview reference currently requires zero yaw and pitch')
        rotor = scene.objects['WFRL.Turbine.T1.Rotor']
        rotor.rotation_euler.x = 0
        bpy.context.view_layer.update()
        spans = np.array([s[0]+geometry_data()['scalars']['HubRad'] for s in geometry_data()['blade_stations']])
        self.blades = []
        for bid in (1, 2, 3):
            obj = scene.objects[f'WFRL.Turbine.T1.Blade{bid}']
            obj.data = obj.data.copy()  # Each blade must have independent vertices.
            rest = np.empty(len(obj.data.vertices)*3, dtype=np.float32)
            obj.data.vertices.foreach_get('co', rest)
            rest = rest.reshape(-1, 3)
            matrix = np.array(obj.matrix_world)
            world = rest@matrix[:3, :3].T+matrix[:3, 3]
            index = np.clip(np.searchsorted(spans, rest[:, 2], side='right')-1, 0, len(spans)-2)
            weight = np.clip((rest[:, 2]-spans[index])/(spans[index+1]-spans[index]), 0, 1)[:, None]
            self.blades.append((obj, rest, world, index, weight))
        self.trails = []
        self._trail_frame = None
        clearance_replay.update(scene)
        scene['wfrl_flex_preview'] = 'FAST.Farm 12 m/s · 单机真实弯曲 · 固定 9 rpm · 18 秒'
        self.enabled = True
        scene['wfrl_flex_active'] = True
        # Keep the wind envelope visible to the existing demo sidebar.  The
        # values come from the verified archive and are never used to alter
        # the physical transforms.
        wind = self.meta.get('wind_conditions')
        if wind is not None:
            scene['wfrl_flex_wind_conditions'] = json.dumps(wind, ensure_ascii=False)
            scene['wfrl_flex_preview'] = (
                f"FAST.Farm · 平均 {wind['mean_mps']:.1f} m/s · TI {wind['ti_percent']:.0f}% · "
                f"阵风峰值 {wind['gust_peak_mps']:.1f} m/s · {wind['gust_duration_s']:.1f} s")
        # The line is opt-in for normal scenes; the Down-view launcher enables
        # it explicitly so the user's first playback makes the motion legible.
        if not hasattr(scene, 'wfrl_flex_show_tip_trails'):
            scene['wfrl_flex_tip_trails'] = False
        if scene.get('wfrl_flex_tip_trails', False):
            self.ensure_trails(reset=True)

    def _tip_positions(self, times):
        """Return world-space blade-tip positions for each requested time."""
        times = np.asarray(times, dtype=float)
        indices = np.clip(np.searchsorted(self.times, times, side='right') - 1,
                          0, len(self.times) - 2)
        alpha = np.clip((times - self.times[indices]) /
                        (self.times[indices + 1] - self.times[indices]), 0, 1)
        transforms = self.transforms[indices] * (1.0 - alpha[:, None, None, None, None]) + \
            self.transforms[indices + 1] * alpha[:, None, None, None, None]
        tips = np.empty((len(times), 3, 3), dtype=float)
        for blade_index, (_obj, rest, world, _index, _weight) in enumerate(self.blades):
            top = rest[:, 2] >= np.max(rest[:, 2]) - 1e-4
            reference = np.mean(world[top], axis=0)
            tips[:, blade_index] = np.einsum(
                'nij,j->ni', transforms[:, blade_index, -1, :, :3], reference) + \
                transforms[:, blade_index, -1, :, 3]
        return tips

    def ensure_trails(self, *, reset=False):
        if not self.scene.get('wfrl_flex_tip_trails', False):
            return
        if reset or not self.trails:
            self.trails = _make_trails(self.scene)
            self._trail_frame = None
        frame = int(self.scene.frame_current)
        frame_count = max(2, frame - self.scene.frame_start + 1)
        # Keep one line vertex per rendered frame; the clip is short and this
        # remains small while preserving the visible gust oscillation.
        fps = float(self.scene.get('wfrl_clearance_timebase_fps',
                                   self.scene.render.fps / self.scene.render.fps_base))
        times = self.times[0] + np.arange(frame_count, dtype=float) / max(fps, 1e-6)
        times = np.clip(times, self.times[0], self.times[-1])
        tips = self._tip_positions(times)
        for index, obj in enumerate(self.trails):
            _set_trail_points(obj, tips[:, index, :])
            obj.hide_set(False)
            obj.hide_render = False
        self._trail_frame = frame

    def update(self, scene):
        import bpy
        from . import clearance_replay
        if scene != self.scene or not self.enabled:
            return
        if clearance_replay.reader_for(scene) is not self.reader:
            for obj, rest, *_ in self.blades:
                obj.data.vertices.foreach_set('co', rest.ravel())
                obj.data.update()
            self.enabled = False
            scene['wfrl_flex_active'] = False
            scene['wfrl_flex_preview'] = '形变预览已停用：请重新打开匹配的短片段'
            _remove_trails(scene)
            self.trails = []
            return
        value = clearance_replay.sample(scene)
        t = value['time_s']
        i = int(np.clip(np.searchsorted(self.times, t, side='right')-1, 0, len(self.times)-2))
        alpha = np.clip((t-self.times[i])/(self.times[i+1]-self.times[i]), 0, 1)
        transforms = self.transforms[i]*(1-alpha)+self.transforms[i+1]*alpha
        bpy.context.view_layer.update()
        for bid, (obj, rest, world, index, weight) in enumerate(self.blades):
            lo, hi = transforms[bid, index], transforms[bid, index+1]
            a = np.einsum('nij,nj->ni', lo[:, :, :3], world)+lo[:, :, 3]
            b = np.einsum('nij,nj->ni', hi[:, :, :3], world)+hi[:, :, 3]
            deformed = a*(1-weight)+b*weight
            inv = np.array(obj.matrix_world.inverted())
            local = deformed@inv[:3, :3].T+inv[:3, 3]
            obj.data.vertices.foreach_set('co', local.astype(np.float32).ravel())
            obj.data.update()
        if scene.get('wfrl_flex_tip_trails', False):
            self.ensure_trails()
        elif self.trails:
            for obj in self.trails:
                obj.hide_set(True)
                obj.hide_render = True


def update(scene, depsgraph=None):
    if _ACTIVE is not None:
        _ACTIVE.update(scene)


def attach(scene, path, source_manifest=None):
    import bpy
    global _ACTIVE
    _ACTIVE = FlexPreview(scene, path, source_manifest)
    if update not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(update)
    update(scene)
    return _ACTIVE
