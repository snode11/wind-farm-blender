"""Three compact, vertically stacked pinhole cameras in one assumed housing.

Dimensions are engineering placeholders, not measurements inferred from a photo.
Build explicitly in a fresh scene; do not silently replace user camera layouts.
Camera coordinates remain in YawRoot metres for the existing capture pipeline.
"""
from dataclasses import dataclass, asdict
import math

from . import custom_cameras as core, camera_projection as projection

PREFIX = 'WFRL.T1.StackedRig'


@dataclass(frozen=True)
class RigParameters:
    # Optical centres C1 (bottom), C2, C3 (top), in nacelle local metres.
    center: tuple = (2.30, -2.12, 1.70)
    spacing_m: float = .080
    width_m: float = .180
    height_m: float = .290
    depth_m: float = .100
    housing_yaw_deg: float = -45.0
    output_long_edge_px: int = 1920
    # Along the same blade, with explicit shared span bands.
    span_ranges_m: tuple = ((0., 23.), (19., 44.), (40., 61.5))


def positions(config):
    values = (*config.center, config.spacing_m, config.width_m,
              config.height_m, config.depth_m, config.housing_yaw_deg)
    if len(config.center) != 3 or not all(math.isfinite(x) for x in values):
        raise ValueError('盒体参数必须是有限的米制尺寸')
    if min(config.spacing_m, config.width_m, config.height_m, config.depth_m) <= 0:
        raise ValueError('盒体尺寸与相机间距必须为正')
    if config.spacing_m < .072 or config.height_m < 3 * config.spacing_m or config.width_m < .10 or config.depth_m < .065:
        raise ValueError('盒体不足以容纳三台 28 mm 相机模块和窗口')
    if len(config.span_ranges_m) != 3 or any(not 0 <= lo < hi for lo, hi in config.span_ranges_m):
        raise ValueError('须指定三个有效叶片覆盖区间')
    if any(config.span_ranges_m[i][1] <= config.span_ranges_m[i+1][0] for i in (0, 1)):
        raise ValueError('相邻覆盖区间须重叠')
    return [tuple(config.center[i] + ((slot-2)*config.spacing_m if i == 2 else 0)
                  for i in range(3)) for slot in (1, 2, 3)]


def fit_segment(points, spans, location, interval, long_edge=1920):
    """Fit once at the reference pose. Never track or refit during playback."""
    import numpy as np
    origin = np.asarray(location)
    ids = np.where((spans >= interval[0] - 1e-4) & (spans <= interval[1] + 1e-4))[0]
    if len(ids) < 3:
        raise ValueError('参考叶片在目标区间没有足够网格点')
    target = points[ids]
    direction = target.mean(axis=0) - origin
    roll = 0.
    for _ in range(8):
        direction /= np.linalg.norm(direction)
        yaw = math.degrees(math.atan2(direction[1], direction[0])) % 360
        pitch = math.degrees(math.asin(direction[2]))
        base = np.asarray(projection.rotation(yaw, pitch, 0))
        ordered = np.argsort(spans[ids])
        delta = (target[ordered[-12:]].mean(axis=0) - target[ordered[:12]].mean(axis=0)) @ base
        roll = math.degrees(math.atan2(delta[1], delta[0]))
        basis = np.asarray(projection.rotation(yaw, pitch, roll))
        view = (target-origin) @ basis
        if np.any(view[:, 2] >= -.005):
            raise ValueError('叶片区间跨越相机后方，无法用单个针孔视场覆盖')
        uv = view[:, :2] / -view[:, 2, None]
        midpoint = (uv.min(axis=0) + uv.max(axis=0)) / 2
        if np.linalg.norm(midpoint) < 1e-7:
            break
        direction = basis @ np.array([*midpoint, -1.])
    # Keep common 16:9 rectangular sensors; focal lengths may differ.
    half = max(np.abs(uv[:, 0]).max(), np.abs(uv[:, 1]).max()*16/9)*1.10
    return core.validate_parameters(core.CameraParameters(
        tuple(location), yaw, pitch, roll,
        math.degrees(2*math.atan(half)), math.degrees(2*math.atan(half*9/16)),
        output_long_edge_px=long_edge))


def blade_geometry(scene):
    import bpy
    import numpy as np
    from . import farm_flex
    if not farm_flex.is_active(scene):
        raise ValueError('请先加载正式 FarmFlex 叶片回放')
    bpy.context.view_layer.update()
    obj, rest, *_ = next(row for row in farm_flex._ACTIVE.blades
                        if row[0].name == 'WFRL.Turbine.T1.Blade1')
    coords = np.empty(len(obj.data.vertices)*3, dtype=np.float32)
    obj.data.vertices.foreach_get('co', coords)
    matrix = np.asarray(scene.objects[core.ROOT_NAME].matrix_world.inverted() @ obj.matrix_world)
    points = coords.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
    spans = rest[:, 2] - 1.5
    used = np.unique([i for face in obj.data.polygons for i in face.vertices])
    return obj, points, spans, used


def _housing(scene, cameras, config):
    import bpy
    from mathutils import Vector
    root = scene.objects[core.ROOT_NAME]
    collection = root.users_collection[0]
    assembly = bpy.data.objects.new(PREFIX, None)
    collection.objects.link(assembly)
    assembly.parent = root
    assembly.location = config.center
    assembly.rotation_euler.z = math.radians(config.housing_yaw_deg)
    assembly['assumptions_json'] = __import__('json').dumps(asdict(config))
    assembly['physical_status'] = 'assumed dimensions; ideal open optical windows; not hardware calibrated'
    colors = {'Body': (.10, .13, .16, 1), 'Seal': (.012, .016, .02, 1),
              'Module': (.025, .032, .037, 1), 'Lens': (.015, .035, .06, 1)}
    materials = {}
    for name, color in colors.items():
        mat = bpy.data.materials.new(PREFIX+'.'+name)
        mat.diffuse_color = color
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get('Principled BSDF')
        bsdf.inputs['Base Color'].default_value = color
        bsdf.inputs['Metallic'].default_value = .5 if name == 'Body' else .1
        bsdf.inputs['Roughness'].default_value = .35
        materials[name] = mat

    def cube(name, location, size, material='Body', rotation=None):
        bpy.ops.mesh.primitive_cube_add(size=1)
        obj = bpy.context.object
        for owner in tuple(obj.users_collection):
            owner.objects.unlink(obj)
        collection.objects.link(obj)
        obj.name = PREFIX+'.'+name
        obj.parent = assembly
        obj.location = location
        obj.scale = size
        if rotation is not None:
            obj.rotation_euler = rotation
        obj.data.materials.append(materials[material])
        obj['stacked_rig_physical'] = True
        return obj

    w, h, d = config.width_m, config.height_m, config.depth_m
    # Front is -Y. Optical centres sit 4 mm behind the open window plane.
    front = -.004
    cube('Back', (0, d-.01, 0), (w, .008, h))
    for sign in (-1, 1):
        cube('Side', (sign*(w/2-.004), d/2-.004, 0), (.008, d, h))
        cube('End', (0, d/2-.004, sign*(h/2-.004)), (w, d, .008))
    # Three rounded rectangular apertures. No opaque placeholder over a lens.
    for slot, camera in enumerate(cameras, 1):
        z = (slot-2)*config.spacing_m
        rx, rz, radius = w/2-.018, .032, .014
        ring = []
        for cx, cz, start in ((rx-radius, rz-radius, 0), (-rx+radius, rz-radius, 90),
                               (-rx+radius, -rz+radius, 180), (rx-radius, -rz+radius, 270)):
            for j in range(9):
                angle = math.radians(start+j*90/8)
                ring.append((cx+radius*math.cos(angle), cz+radius*math.sin(angle)))
        verts = [(x, y, zz+z) for y in (front, front+.004)
                 for factor in (1., 1.10) for x, zz in [(x*factor, zz*factor) for x, zz in ring]]
        n = len(ring)
        faces = []
        for a, b in ((0, n), (2*n, 3*n), (0, 2*n), (n, 3*n)):
            faces += [(a+i, a+(i+1)%n, b+(i+1)%n, b+i) for i in range(n)]
        mesh = bpy.data.meshes.new(PREFIX+'.WindowMesh'); mesh.from_pydata(verts, [], faces); mesh.update()
        seal = bpy.data.objects.new(PREFIX+f'.Window{slot}', mesh); collection.objects.link(seal)
        seal.parent = assembly; seal.data.materials.append(materials['Seal']); seal['stacked_rig_physical'] = True
        # Fill the front panel all the way to each aperture; only optical holes stay open.
        inner = [(x*1.10, zz*1.10) for x, zz in ring]
        outer = []
        for x, zz in ring:
            factor = min((w/2)/max(abs(x), 1e-9), (config.spacing_m/2)/max(abs(zz), 1e-9))
            outer.append((x*factor, zz*factor))
        panelmesh = bpy.data.meshes.new(PREFIX+'.FrontPanelMesh')
        panelmesh.from_pydata([(x, front+.001, zz+z) for x, zz in inner+outer], [],
                             [(i, (i+1)%n, n+(i+1)%n, n+i) for i in range(n)])
        panelmesh.update()
        panel = bpy.data.objects.new(PREFIX+f'.FrontPanel{slot}', panelmesh)
        collection.objects.link(panel); panel.parent = assembly
        panel.data.materials.append(materials['Body']); panel['stacked_rig_physical'] = True
        # Body and lens backing remain behind the optical centre, including near clip.
        for name, offset, size, material in (
                (f'Module{slot}', .029, (.028, .028, .028), 'Module'),
                (f'LensBack{slot}', .011, (.018, .018, .004), 'Lens')):
            body = cube(name, (0, 0, 0), size, material)
            body['stacked_rig_slot'] = slot
            body['stacked_rig_offset'] = offset
            body.parent = camera
            body.location = (0, 0, offset)
            body.rotation_euler = (0, 0, 0)
    extra = h/2-1.5*config.spacing_m
    if extra > 0:
        for sign in (-1, 1):
            cube('FrontCap', (0, front+.002, sign*(h/2-extra/2)), (w, .006, extra))
    for z in (-h/2+.015, -config.spacing_m/2, config.spacing_m/2, h/2-.015):
        cube('FrontRail', (0, front+.002, z), (w-.01, .006, .008))
    for sign in (-1, 1):
        cube('FrontSide', (sign*(w/2-.008), front+.002, 0), (.012, .006, h))
    cube('MountArm', (0, d+.13, 0), (.040, .28, .040))
    return assembly


def build(scene, config=RigParameters()):
    import bpy
    if any(core.get_camera(scene, slot) for slot in core.SLOTS) or scene.objects.get(PREFIX):
        raise ValueError('请使用新场景创建三相机盒体，避免覆盖已有相机布局')
    locs = positions(config)
    obj, points, spans, used = blade_geometry(scene)
    params = [fit_segment(points[used], spans[used], loc, interval, config.output_long_edge_px)
              for loc, interval in zip(locs, config.span_ranges_m)]
    for p in params:
        core.validate_research_position(scene, p.location, confirmed=True)
    cameras = []
    for slot, p in enumerate(params, 1):
        cam = core.begin_draft(scene, slot)
        core.apply_research(scene, cam, p, confirmed=True)
        cam['custom_label'] = f'C{slot} · '+('底层 / 叶根', '中层 / 中段', '顶层 / 叶尖')[slot-1]
        cam['stacked_rig_id'] = PREFIX
        core.commit_draft(scene, slot, cam)
        cameras.append(cam)
    assembly = _housing(scene, cameras, config)
    scene['wfrl_stacked_camera_rig'] = PREFIX
    scene['wfrl_stacked_reference_frame'] = scene.frame_current
    bpy.context.view_layer.update()
    return cameras, assembly


def sync_modules(scene):
    """Rebind physical bodies after draft commit, layout import, undo or clear."""
    if not scene.get('wfrl_stacked_camera_rig'):
        return
    for obj in scene.objects:
        slot = obj.get('stacked_rig_slot')
        if slot not in (1, 2, 3):
            continue
        camera = core.get_camera(scene, slot)
        obj.hide_set(camera is None)
        obj.hide_render = camera is None
        obj.parent = camera
        obj.matrix_parent_inverse.identity()
        obj.location = (0, 0, obj['stacked_rig_offset'])
        obj.rotation_euler = (0, 0, 0)


def pose(scene):
    box = scene.objects.get(PREFIX)
    if box is None:
        return None
    return {'center': [float(v) for v in box.location],
            'housing_yaw_deg': math.degrees(box.rotation_euler.z)}


def validate_pose(scene, payload, records):
    import json
    box = scene.objects.get(PREFIX)
    incoming = payload.get('rig_pose')
    if incoming is not None and box is None:
        raise ValueError('请先加载三相机盒体，再导入带盒体位置的布局')
    if box is None:
        return
    proposed = incoming if incoming is not None else pose(scene)
    center = proposed.get('center', [])
    yaw = proposed.get('housing_yaw_deg')
    if len(center) != 3 or not core._finite((*center, yaw)):
        raise ValueError('盒体位置与朝向须为有限数值')
    spacing = json.loads(box['assumptions_json'])['spacing_m']
    for record, params, _ in records:
        expected = tuple(center[i] + ((record['slot_id']-2)*spacing if i == 2 else 0) for i in range(3))
        if max(abs(a-b) for a,b in zip(expected,params.location)) > 2e-5:
            raise ValueError('相机必须保持盒内竖直堆叠，请整体移动盒体后再调俯仰角')


def apply_pose(scene, value):
    import json
    box = scene.objects.get(PREFIX)
    if box is None or value is None:
        return
    box.location = value['center']
    box.rotation_euler.z = math.radians(value['housing_yaw_deg'])
    data = json.loads(box['assumptions_json'])
    data.update(center=list(value['center']), housing_yaw_deg=value['housing_yaw_deg'])
    box['assumptions_json'] = json.dumps(data)


def move_box(scene, center, yaw):
    """One reversible change for the housing and all camera optical centres."""
    import copy
    from . import custom_camera_history as history, custom_camera_capture as capture
    from .panels.custom_cameras import installation_block_reason
    if capture.active() or any(o.get('wfrl_custom_draft') for o in scene.objects):
        raise ValueError('请先结束采集或确认当前相机调整')
    reason = installation_block_reason(scene)
    if reason:
        raise ValueError(reason)
    before = history.before_change(scene)
    layout = copy.deepcopy(before[1])
    previous = pose(scene)
    if previous is None:
        raise ValueError('请先加载三相机盒体')
    delta = yaw-previous['housing_yaw_deg']
    layout['rig_pose'] = {'center':list(center),'housing_yaw_deg':yaw}
    for record in layout['cameras']:
        p = record['parameters']
        p['location'] = [p['location'][i]+center[i]-previous['center'][i] for i in range(3)]
        p['yaw'] = (p['yaw']+delta)%360
        result = core.validate_research_position(scene, p['location'], confirmed=True)
        record.update(mount_mode='RESEARCH', mount_validation_status=result['status'],
                      research_confirmed=True, nearest_distance_m=result['nearest_distance_m'],
                      mount_warning=result['warning'], anchor=None)
    core.restore_layout(scene, layout)
    history.committed(scene, before, '整体移动三相机盒体')
