"""Explicitly attached source-only flexible mesh preview, using real surfaces."""
import hashlib
import json
from pathlib import Path
import numpy as np

_ACTIVE = None


_TRAIL_COLLECTION = 'WFRL_FlexTipTrails'
_TRAIL_OWNER = 'wfrl_flex_preview_trail'
# Keep the legend stable across the Blender and OpenCV previews: B1 orange,
# B2 blue, B3 red.
_TRAIL_COLORS = ((1.0, 0.32, 0.04, 1.0), (0.05, 0.45, 1.0, 1.0),
                 (0.95, 0.05, 0.08, 1.0))


def _release(store, block):
    """Release a known allocation only when no Blender retention applies."""
    try:
        if (block.users == 0 and not block.use_fake_user and block.library is None
                and getattr(block, 'shape_keys', None) is None
                and store.get(block.name) == block):
            store.remove(block)
    except ReferenceError:
        pass


def _trail_material(index, resources):
    """Create a bright viewport material for one blade's tip trail."""
    import bpy
    name = f'WFRL.FlexTipTrail.Material{index}'
    material = bpy.data.materials.new(name)
    resources.append((bpy.data.materials, material))
    material[_TRAIL_OWNER] = True
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


def _trail_collection(scene, resources):
    import bpy
    collection = bpy.data.collections.new(_TRAIL_COLLECTION)
    resources.append((bpy.data.collections, collection))
    collection[_TRAIL_OWNER] = True
    scene.collection.children.link(collection)
    return collection


def _owned_trails(scene):
    return [obj for collection in scene.collection.children if collection.get(_TRAIL_OWNER)
            for obj in collection.objects if obj.get(_TRAIL_OWNER)]


def _remove_trails(scene, objects=None):
    import bpy
    for obj in _owned_trails(scene) if objects is None else objects:
        try:
            if len(obj.users_scene) > 1 or obj.use_fake_user or obj.library is not None:
                continue
            curve = obj.data
            materials = list(curve.materials) if curve is not None and curve.get(_TRAIL_OWNER) else []
            bpy.data.objects.remove(obj, do_unlink=True)
        except ReferenceError:
            continue
        if curve is not None and curve.get(_TRAIL_OWNER):
            _release(bpy.data.curves, curve)
        for material in materials:
            if material is not None and material.get(_TRAIL_OWNER):
                _release(bpy.data.materials, material)
    for collection in list(scene.collection.children):
        if (collection.get(_TRAIL_OWNER) and not collection.objects and not collection.children
                and collection.users == 1 and not collection.use_fake_user
                and collection.library is None):
            bpy.data.collections.remove(collection)


def _make_trails(scene, *, previous=None):
    """Build reusable curve objects; point coordinates are replaced per frame."""
    import bpy
    previous = _owned_trails(scene) if previous is None else previous
    trails, resources = [], []
    try:
        collection = _trail_collection(scene, resources)
        for bid in (1, 2, 3):
            name = f'WFRL.FlexTipTrail.B{bid}'
            curve = bpy.data.curves.new(name + '.Data', 'CURVE')
            resources.append((bpy.data.curves, curve))
            curve[_TRAIL_OWNER] = True
            curve.dimensions = '3D'
            curve.resolution_u = 2
            curve.bevel_depth = .12
            curve.bevel_resolution = 2
            curve.materials.append(_trail_material(bid, resources))
            spline = curve.splines.new('POLY')
            spline.points.add(1)
            spline.points[0].co = (0, 0, 0, 1)
            spline.points[1].co = (0, 0, 0, 1)
            obj = bpy.data.objects.new(name, curve)
            trails.append(obj)
            obj[_TRAIL_OWNER] = True
            collection.objects.link(obj)
            obj.hide_render = False
            obj.hide_set(False)
            obj['role'] = 'flexible blade tip trajectory'
    except Exception:
        _remove_trails(scene, trails)
        for store, block in resources:
            _release(store, block)
        raise
    _remove_trails(scene, previous)
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


def is_active(scene):
    from . import clearance_replay
    try:
        return (_ACTIVE is not None and _ACTIVE.enabled and _ACTIVE.scene == scene
                and clearance_replay.reader_for(scene) is _ACTIVE.reader)
    except ReferenceError:
        return False


_SCENE_KEYS = ('wfrl_flex_preview', 'wfrl_flex_active', 'wfrl_flex_wind_conditions',
               'wfrl_flex_tip_trails')


def _scene_state(scene):
    return {key: (key in scene, scene.get(key)) for key in _SCENE_KEYS}


def _restore_scene(scene, state):
    for key, (present, value) in state.items():
        if present:
            scene[key] = value
        elif key in scene:
            del scene[key]


def _preview_data(scene, path, source_manifest):
    from . import clearance_replay
    reader = clearance_replay.reader_for(scene)
    if reader is None:
        raise ValueError('Flexible preview requires an active clearance package')
    with np.load(path, allow_pickle=False) as archive:
        times = archive['times'].copy()
        transforms = archive['transforms'].copy()
        meta = json.loads(str(archive['metadata']))
    manifest = (Path(source_manifest) if source_manifest else
                Path(scene.wfrl_clearance_near_tower_path)/'manifest.json')
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    if (not isinstance(meta, dict) or meta.get('package_manifest_sha256') != digest
            or scene.get('wfrl_clearance_demo') != 'near_tower'):
        raise ValueError('Flex geometry and clearance package must match')
    if (meta.get('schema') != 'wfrl.section-transforms.preview.v1'
            or meta.get('turbine_id') != 'T1'
            or times.ndim != 1 or len(times) < 2
            or not np.isfinite(times).all()
            or transforms.shape != (len(times), 3, 19, 3, 4)
            or not np.isfinite(transforms).all()
            or np.any(np.diff(times) <= 0)
            or abs(times[0]-reader.start_s)>1e-8
            or abs(times[-1]-reader.end_s)>1e-8):
        raise ValueError('Invalid or incomplete flexible preview')
    # This reference matches the archived t=0 fixed yaw/pitch NREL5MW run.
    motion = reader.at(reader.start_s)['motion']
    if motion['yaw_deg'] != 0 or any(motion['pitch_deg']):
        raise ValueError('Preview reference currently requires zero yaw and pitch')
    return reader, times, transforms, meta


class FlexPreview:
    def __init__(self, scene, path, source_manifest=None, *, _data=None):
        import bpy
        from . import clearance_replay
        from .turbine_geometry import geometry_data
        self.scene = scene
        self.reader, self.times, self.transforms, self.meta = (
            _preview_data(scene, path, source_manifest) if _data is None else _data)
        self._bindings = []
        self.blades = []
        self.trails = []
        self._trail_frame = None
        self.enabled = False
        state = _scene_state(scene)
        rotor = scene.objects['WFRL.Turbine.T1.Rotor']
        targets = [scene.objects[f'WFRL.Turbine.T1.Blade{bid}'] for bid in (1, 2, 3)]
        if any(len(obj.users_scene) > 1 or obj.library is not None for obj in [rotor, *targets]):
            raise ValueError('Flexible preview cannot rebind shared or linked objects')
        angle = rotor.rotation_euler.x
        try:
            rotor.rotation_euler.x = 0
            bpy.context.view_layer.update()
            spans = np.array([s[0]+geometry_data()['scalars']['HubRad'] for s in geometry_data()['blade_stations']])
            for bid in (1, 2, 3):
                obj = targets[bid - 1]
                original = obj.data
                mesh = original.copy()
                self._bindings.append((obj, original, mesh))
                obj.data = mesh  # Each blade must have independent vertices.
                rest = np.empty(len(obj.data.vertices)*3, dtype=np.float32)
                obj.data.vertices.foreach_get('co', rest)
                rest = rest.reshape(-1, 3)
                matrix = np.array(obj.matrix_world)
                world = rest@matrix[:3, :3].T+matrix[:3, 3]
                index = np.clip(np.searchsorted(spans, rest[:, 2], side='right')-1, 0, len(spans)-2)
                weight = np.clip((rest[:, 2]-spans[index])/(spans[index+1]-spans[index]), 0, 1)[:, None]
                self.blades.append((obj, rest, world, index, weight))
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
        except Exception:
            self.close()
            rotor.rotation_euler.x = angle
            _restore_scene(scene, state)
            raise

    def restore_bindings(self):
        for obj, original, mesh in self._bindings:
            try:
                if obj.data == mesh:
                    obj.data = original
            except ReferenceError:
                continue

    def resume_bindings(self):
        for obj, original, mesh in self._bindings:
            if obj.data == original:
                obj.data = mesh
        self.enabled = True

    def close(self):
        import bpy
        self.enabled = False
        self.restore_bindings()
        for _obj, _original, mesh in self._bindings:
            _release(bpy.data.meshes, mesh)
        self._bindings.clear()
        try:
            _remove_trails(self.scene, self.trails)
        except ReferenceError:
            pass
        self.trails.clear()

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
            self.trails = _make_trails(self.scene, previous=self.trails)
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
            if self is _ACTIVE:
                detach()
            else:
                self.close()
            scene['wfrl_flex_active'] = False
            scene['wfrl_flex_preview'] = '形变预览已停用：请重新打开匹配的短片段'
            return
        value = clearance_replay.sample(scene)
        try:
            t = float(value['time_s'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('Flexible preview sample time must be finite') from exc
        if not np.isfinite(t):
            raise ValueError('Flexible preview sample time must be finite')
        for obj, _original, mesh in self._bindings:
            if (scene.objects.get(obj.name) != obj or obj.data != mesh
                    or len(obj.users_scene) > 1 or mesh.users > 1):
                raise ValueError('Flexible preview mesh binding changed or became shared')
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
        try:
            _ACTIVE.update(scene)
        except ReferenceError:
            detach()
        except ValueError as exc:
            detach()
            scene['wfrl_flex_preview'] = '形变预览已停用：' + str(exc)
            raise


def _on_reset(_):
    """Release RNA references before loading or replacing native history."""
    detach()


_HANDLERS = (('frame_change_post', update), ('load_pre', _on_reset),
             ('undo_pre', _on_reset), ('redo_pre', _on_reset))


def detach():
    """Stop the explicit preview and restore its original mesh bindings."""
    import bpy
    global _ACTIVE
    if _ACTIVE is not None:
        _ACTIVE.close()
        try:
            _ACTIVE.scene['wfrl_flex_active'] = False
        except ReferenceError:
            pass
        _ACTIVE = None
    for name, callback in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if callback in handlers:
            handlers.remove(callback)


def attach(scene, path, source_manifest=None):
    import bpy
    global _ACTIVE
    data = _preview_data(scene, path, source_manifest)
    previous = _ACTIVE
    state = _scene_state(scene)
    rotor = scene.objects['WFRL.Turbine.T1.Rotor']
    angle = rotor.rotation_euler.x
    candidate = None
    if previous is not None:
        previous.enabled = False
        previous.restore_bindings()
    try:
        candidate = FlexPreview(scene, path, source_manifest, _data=data)
        candidate.update(scene)
        for name, callback in _HANDLERS:
            handlers = getattr(bpy.app.handlers, name)
            if callback not in handlers:
                bpy.app.handlers.persistent(callback)
                handlers.append(callback)
    except Exception:
        if candidate is not None:
            candidate.close()
        rotor.rotation_euler.x = angle
        _restore_scene(scene, state)
        if previous is not None:
            previous.resume_bindings()
        else:
            detach()
        raise
    if previous is not None:
        previous.close()
        if previous.scene != scene:
            previous.scene['wfrl_flex_active'] = False
    _ACTIVE = candidate
    return _ACTIVE
