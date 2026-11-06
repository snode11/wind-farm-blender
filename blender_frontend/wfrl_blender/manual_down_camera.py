"""Owned manual Down cameras, blade-number overlays and display meshes."""
import math
import re

import bpy
from mathutils import Vector
import numpy as np

from . import cameras, manual_control
from .turbine_geometry import DEFAULT_MODEL
from . import manual_down as _down


def _entry(scene, turbine):
    entry = next((item for item in scene.wfrl_manual_turbines if item.turbine_id == turbine), None)
    if entry is None:
        raise ValueError(f'手动场景中没有 {turbine}')
    return entry


def _camera_name(scene, turbine):
    return f"{scene['wfrl_manual_owner']}.Camera.{turbine}.Down"


def ensure_down_camera(scene, turbine, *, reset=False):
    """Use the local owned camera contract without changing another scene's optics."""
    valid, reason = manual_control.validate_scene(scene)
    if not valid:
        raise ValueError(reason)
    entry = _down._entry(scene, turbine)
    manual_control._validate_controlled_rotation(entry)
    owner = scene['wfrl_manual_owner']
    for existing in entry.yaw_object.children:
        if cameras.is_down_camera(existing):
            manual_control._validate_native_controls((existing,))
    from .manual_geometry import set_detail
    set_detail(scene, 'CLOSEUP')
    with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
        camera = cameras.ensure_gimbal(scene, turbine, yaw_object=entry.yaw_object,
                                       name=_down._camera_name(scene, turbine), owner=owner)
        manual_control._validate_native_controls((camera,))
        if reset:
            cameras.down_gimbal(camera)
    camera['wfrl_turbine'] = turbine
    _down._ensure_labels(scene, camera)
    _down.update_labels(scene)
    return camera


def _down_cameras(scene):
    if not manual_control.is_manual(scene):
        return []
    return [obj for obj in scene.objects if obj.type == 'CAMERA' and obj.get('wfrl_down_camera')
            and obj.get('wfrl_turbine') and obj.get('wfrl_manual_owner') == scene.get('wfrl_manual_owner')]


def _label_material(scene, key, color):
    name = f"{scene['wfrl_manual_owner']}.Label.{key}"
    candidates = {material for obj in scene.objects if obj.get('wfrl_down_label')
                  for material in getattr(obj.data, 'materials', ()) if material
                  and material.get('wfrl_down_label_material') == key
                  and material.get('wfrl_manual_owner') == scene['wfrl_manual_owner']}
    material = next(iter(candidates), None)
    if material is None:
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        nodes = material.node_tree.nodes
        nodes.clear()
        emission = nodes.new('ShaderNodeEmission')
        emission.inputs['Strength'].default_value = 1.0
        output = nodes.new('ShaderNodeOutputMaterial')
        material.node_tree.links.new(emission.outputs['Emission'], output.inputs['Surface'])
    shared = any(other != scene and any(
        getattr(obj.data, 'materials', None) and material.name in obj.data.materials
        for obj in other.objects) for other in bpy.data.scenes)
    if shared:
        consumers = [obj for obj in scene.objects if obj.get('wfrl_down_label')
                     and material.name in getattr(obj.data, 'materials', ())]
        other_objects = manual_control.other_scene_objects(scene)
        if any(obj.data.users != 1 or obj.as_pointer() in other_objects
               for obj in consumers):
            raise ValueError('编号网格或文本与其他场景共享')
        original = material
        material = original.copy()
        material.name = name
        for obj in consumers:
            for index, existing in enumerate(obj.data.materials):
                if existing == original:
                    obj.data.materials[index] = material
    material.diffuse_color = (*color, 1.0)
    material.node_tree.nodes['Emission'].inputs['Color'].default_value = (*color, 1.0)
    material['wfrl_manual_owner'] = scene['wfrl_manual_owner']
    material['wfrl_down_label_material'] = key
    material['provenance'] = 'Blade-number annotation overlay; not part of the turbine'
    return material


def _label_children(scene):
    children = {}
    for obj in bpy.data.objects:
        if obj.parent is not None:
            children.setdefault(obj.parent.as_pointer(), []).append(obj)
    return children


def _label_objects(scene, camera, *, children=None, shared=None):
    result = {}
    children = _down._label_children(scene) if children is None else children
    pending = list(children.get(camera.as_pointer(), ()))
    while pending:
        obj = pending.pop()
        pending.extend(children.get(obj.as_pointer(), ()))
        if not obj.get('wfrl_down_label'):
            continue
        if obj.get('wfrl_manual_owner') != scene['wfrl_manual_owner']:
            raise ValueError('编号对象不属于此场景')
        if shared is None:
            shared = manual_control.other_scene_objects(scene)
        if obj.as_pointer() in shared:
            raise ValueError('编号对象不属于此场景')
        role = obj.get('wfrl_down_role')
        if not role:
            match = re.search(r'(Legend\.(?:B[123]|Back|Title)|Label\.B[123](?:\.Back)?)(?:\.\d+)*$', obj.name)
            role = match.group(1) if match else None
        if role:
            if role in result:
                raise ValueError('同一相机包含重复编号角色')
            result[role] = obj
    return result


def _overlay_object(scene, camera, suffix, data):
    name = f'{camera.name}.{suffix}'
    obj = _down._label_objects(scene, camera).get(suffix)
    if obj is None:
        obj = bpy.data.objects.new(name, data)
        camera.users_collection[0].objects.link(obj)
    if obj.data != data or obj.as_pointer() in manual_control.other_scene_objects(scene):
        raise ValueError('编号对象或数据与其他场景共享')
    data['wfrl_manual_owner'] = scene['wfrl_manual_owner']
    if obj.get('wfrl_manual_owner', scene['wfrl_manual_owner']) != scene['wfrl_manual_owner']:
        raise ValueError('编号对象归属不匹配')
    obj.parent = camera
    obj.matrix_parent_inverse.identity()
    obj.rotation_euler = (0, 0, 0)
    obj.visible_shadow = False
    obj.hide_select = True
    obj['wfrl_manual_owner'] = scene['wfrl_manual_owner']
    obj['wfrl_down_label'] = True
    obj['wfrl_down_role'] = suffix
    return obj


def _text(scene, camera, suffix, body, size, align_x, align_y, material):
    name = f'{camera.name}.{suffix}'
    existing = _down._label_objects(scene, camera).get(suffix)
    curve = existing.data if existing else None
    if curve is not None and (curve.users != 1 or curve.get('wfrl_manual_owner') != scene['wfrl_manual_owner']):
        raise ValueError('编号文本数据不属于此场景')
    if curve is None:
        curve = bpy.data.curves.new(name, 'FONT')
    curve.body, curve.size = body, size
    curve.align_x, curve.align_y = align_x, align_y
    curve.materials.clear()
    curve.materials.append(material)
    return _down._overlay_object(scene, camera, suffix, curve)


def _plate(scene, camera, suffix, vertices, material, parent):
    name = f'{camera.name}.{suffix}'
    existing = _down._label_objects(scene, camera).get(suffix)
    mesh = existing.data if existing else None
    if mesh is not None and (mesh.users != 1 or mesh.get('wfrl_manual_owner') != scene['wfrl_manual_owner']):
        raise ValueError('编号背景数据不属于此场景')
    if mesh is None:
        mesh = bpy.data.meshes.new(name)
    mesh.clear_geometry()
    mesh.from_pydata(vertices, [], [tuple(range(len(vertices)))])
    mesh.materials.clear()
    mesh.materials.append(material)
    obj = _down._overlay_object(scene, camera, suffix, mesh)
    obj.parent = parent
    return obj


def _frame_tangents(camera, scene):
    tan_w = camera.data.sensor_width / 2 / camera.data.lens
    render = scene.render
    return tan_w, tan_w * render.resolution_y * render.pixel_aspect_y / (render.resolution_x * render.pixel_aspect_x)


def _ensure_labels(scene, camera):
    """Top-right box showing the number of the blade currently in view."""
    tan_w, tan_h = _down._frame_tangents(camera, scene)
    height = 2 * _down.LABEL_DEPTH * tan_h
    for suffix in ('Legend.Title', *(f'Label.B{i}{part}' for i in range(1, 4) for part in ('', '.Back'))):
        stale = _down._label_objects(scene, camera).get(suffix)
        if stale is not None:
            if (stale.get('wfrl_manual_owner') != scene['wfrl_manual_owner']
                    or stale.as_pointer() in manual_control.other_scene_objects(scene)):
                raise ValueError('编号对象不属于此场景')
            bpy.data.objects.remove(stale, do_unlink=True)
    dark = _down._label_material(scene, 'Back', (.02, .02, .025))
    line = .09 * height
    margin = .03 * height
    right, top = _down.LABEL_DEPTH * tan_w - margin, _down.LABEL_DEPTH * tan_h - margin
    pad = .3 * line
    width, depth = 2.2 * line, 1.0 * line
    rows = []
    for index, color in enumerate(_down.BLADE_COLORS, 1):
        row = _down._text(scene, camera, f'Legend.B{index}', f'B{index}', line, 'CENTER', 'CENTER',
                    _down._label_material(scene, f'B{index}', color))
        row.location = (right - pad - width / 2, top - pad - depth / 2, -_down.LABEL_DEPTH)
        rows.append(row)
    back = _down._plate(scene, camera, 'Legend.Back',
                  [(right, top, -_down.LABEL_DEPTH * 1.004), (right - width - 2 * pad, top, -_down.LABEL_DEPTH * 1.004),
                   (right - width - 2 * pad, top - depth - 2 * pad, -_down.LABEL_DEPTH * 1.004),
                   (right, top - depth - 2 * pad, -_down.LABEL_DEPTH * 1.004)], dark, camera)
    back.location = (0, 0, 0)
    camera['wfrl_label_aspect'] = scene.render.resolution_y / scene.render.resolution_x


def _stations(mesh):
    """Blade-local centroids of span stations from 15 % to 95 % of the blade length,
    nearest to ``LABEL_SPAN`` first."""
    key = (mesh.as_pointer(), len(mesh.vertices), mesh.get('wfrl_manual_geometry', mesh.get('wfrl_blade_model', DEFAULT_MODEL)))
    if not _down._STATIONS.get(key):
        _down._STATIONS.pop(key, None)
        if not len(mesh.vertices):
            raise ValueError('叶片缺少编号定位截面')
        co = np.empty(len(mesh.vertices) * 3, np.float32)
        mesh.vertices.foreach_get('co', co)
        co = co.reshape(-1, 3)
        low, high = float(co[:, 2].min()), float(co[:, 2].max())
        rows = []
        for fraction in np.linspace(.15, .95, 17):
            ring = co[np.abs(co[:, 2] - (low + (high - low) * fraction)) < (high - low) / 60]
            if len(ring):
                rows.append((abs(fraction - _down.LABEL_SPAN), Vector(ring.mean(0).tolist())))
        if not rows:
            raise ValueError('叶片缺少编号定位截面')
        _down._STATIONS[key] = [point for _distance, point in sorted(rows, key=lambda row: row[0])]
    return _down._STATIONS[key]


def _world(obj):
    """World matrix from current transform values, valid before the depsgraph updates."""
    matrix = obj.matrix_basis.copy()
    while obj.parent is not None:
        matrix = obj.matrix_parent_inverse @ matrix
        obj = obj.parent
        matrix = obj.matrix_basis @ matrix
    return matrix


def _set_hidden(obj, hidden):
    if obj.hide_viewport != hidden:
        obj.hide_viewport = hidden
    if obj.hide_render != hidden:
        obj.hide_render = hidden


def current_blade(camera, entry):
    """Number of the blade whose mid-span lies closest to the camera's line of sight.

    Each blade owns the rotor sector around its passage through the view, so
    the number stays defined while no blade is inside the frame."""
    inverse = _down._world(camera).inverted()
    best = None
    for index in range(1, 4):
        blade = getattr(entry, f'blade{index}_object')
        local = inverse @ _down._world(blade) @ _down._stations(blade.data)[0]
        angle = local.angle(Vector((0.0, 0.0, -1.0)), math.pi)
        if best is None or angle < best[0]:
            best = (angle, index)
    return best[1]


def update_labels(scene, *, visible=None):
    """Show the number of the blade currently in each Down view in its top-right box.

    Visibility is written only when it changes, since every change rebuilds
    depsgraph relations."""
    if not manual_control.is_manual(scene):
        return
    shown = scene.wfrl_manual_blade_labels if visible is None else visible
    children = shared = None
    for camera in _down._down_cameras(scene):
        if children is None:
            children = _down._label_children(scene)
            shared = manual_control.other_scene_objects(scene)
        objects = _down._label_objects(scene, camera, children=children, shared=shared)
        if not objects:
            continue
        if shown and ('Legend.Title' in objects or abs(
                camera.get('wfrl_label_aspect', 0) - scene.render.resolution_y / scene.render.resolution_x) > 1e-6):
            _down._ensure_labels(scene, camera)
            children = _down._label_children(scene)
            shared = manual_control.other_scene_objects(scene)
            objects = _down._label_objects(scene, camera, children=children, shared=shared)
        try:
            current = _down.current_blade(camera, _down._entry(scene, camera['wfrl_turbine'])) if shown else None
        except ValueError as exc:
            current = None
            if camera.get('wfrl_label_error') != str(exc):
                camera['wfrl_label_error'] = str(exc)
        else:
            camera.pop('wfrl_label_error', None)
        _down._set_hidden(objects['Legend.Back'], current is None)
        for index in range(1, 4):
            _down._set_hidden(objects[f'Legend.B{index}'], current != index)


def _labels_listener(scene):
    if scene.get('wfrl_manual_exporting'):
        return
    _down.update_labels(scene)


def _labels_update(scene, context):
    _down.update_labels(scene)


def apply_blade_model(scene):
    """Select owned display meshes while retaining poses and motion clocks."""
    if not manual_control.is_manual(scene) or not scene.wfrl_manual_turbines:
        return
    from . import manual_scene
    manual_scene._ensure_blade_model(scene)
    _down._STATIONS.clear()
    _down.update_labels(scene)
