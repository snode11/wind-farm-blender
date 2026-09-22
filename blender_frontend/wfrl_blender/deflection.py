"""T1 ElastoDyn tip comparison, independent of the displayed tip taper.

The rigid reference uses turbine geometry and recorded rigid pose only. The
actual point is a dedicated vertex carried by the existing surface deformation
pipeline. Output deflections enter only the comparison, never either position.
"""
import hashlib
import json
from pathlib import Path
import numpy as np


def rotation(axis, degrees):
    a = np.radians(degrees)
    c, s = np.cos(a), np.sin(a)
    return (np.array(((1, 0, 0), (0, c, -s), (0, s, c))),
            np.array(((c, 0, s), (0, 1, 0), (-s, 0, c))),
            np.array(((c, -s, 0), (s, c, 0), (0, 0, 1))))[axis]


def rigid_frame(scalars, pose, blade, nacelle=None):
    """World origin, unpitched coned axes (TipDxc/yc/zc), pitched axes.

    Fixed tower/platform, 3 blades, zero skew/teeter. See ElastoDyn v3.5.3
    SetCoordSy axes i1/i2/i3 and CalcOutput tip motions. Pitch leaves i3 fixed.
    """
    yaw = rotation(2, pose[0]) if nacelle is None else np.asarray(nacelle)[:, :3]
    tilt = np.radians(scalars['ShftTilt'])
    hub = yaw @ np.array((scalars['OverHang'] * np.cos(tilt), 0,
                         scalars['TowerHt'] + scalars['Twr2Shft']
                         + scalars['OverHang'] * np.sin(tilt)))
    axes = (yaw @ rotation(1, -scalars['ShftTilt'])
            @ rotation(0, pose[1] + (blade - 1) * 120)
            @ rotation(1, scalars['PreCone(1)']))
    if nacelle is not None: hub = hub + np.asarray(nacelle)[:, 3]
    return hub, axes, axes @ rotation(2, -pose[blade + 2])


def compare(actual, reference, axes, simulation):
    components = axes.T @ (np.asarray(actual) - reference)
    return dict(actual=np.asarray(actual), reference=reference, axes=axes,
                components=components, simulation=np.asarray(simulation),
                error=components[:len(simulation)] - simulation,
                distance=float(np.linalg.norm(np.asarray(actual) - reference)))


def read_comparison(path, manifest, times, poses):
    """Optional sidecar: absent means unavailable; malformed must fail closed."""
    name = 'deflection-t1.json'
    if name not in manifest['files']:
        return None
    path = Path(path)
    raw = (path / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest['files'][name]:
        raise ValueError('T1 deflection integrity mismatch')
    data = json.loads(raw)
    beamdyn = data['schema'] == 'wfrl.tip-deflection.t1.v3'
    reference = ('BeamDyn structural tip; independent unloaded prebend' if beamdyn else 'ElastoDyn structural tip; local (0,0,TipRad)')
    frame = 'BeamDyn pitched root xyz' if beamdyn else 'ElastoDyn unpitched coned xc,yc,zc'
    if (data['schema'] not in ('wfrl.tip-deflection.t1.v1', 'wfrl.tip-deflection.t1.v2', 'wfrl.tip-deflection.t1.v3')
            or data['geometry_sha256'] != manifest['files']['geometry.npz']
            or data['turbine_id'] != 'T1'
            or data['reference'] != reference
            or data['frame'] != frame
            or data['unit'] != 'm'
            or not np.array_equal(data['times'], times)):
        raise ValueError('T1 deflection and geometry do not match')
    for key, shape in [('poses', (len(times), 6)), ('simulation', (len(times), 3, 3 if beamdyn else 2))]:
        data[key] = np.asarray(data[key], dtype=float)
        if data[key].shape != shape or not np.isfinite(data[key]).all():
            raise ValueError('Invalid T1 deflection samples: ' + key)
    if not np.allclose(data['poses'], poses[:, 0], atol=.002, rtol=0):
        raise ValueError('T1 deflection rigid poses differ')
    from .turbine_geometry import geometry_data
    for key in ('TipRad', 'HubRad', 'OverHang', 'TowerHt', 'Twr2Shft', 'ShftTilt', 'PreCone(1)'):
        if not np.isclose(data['scalars'][key], geometry_data()['scalars'][key], atol=1e-8, rtol=0):
            raise ValueError('T1 deflection model mismatch: ' + key)
    flexible = manifest.get('tower_model') == 'elastodyn-flexible'
    if flexible != (data['schema'] in ('wfrl.tip-deflection.t1.v2','wfrl.tip-deflection.t1.v3')):
        raise ValueError('Deflection/tower reference mismatch')
    if flexible:
        from .tower_motion import read_tower
        if data.get('tower_motion_sha256') != manifest['files']['tower-motion.npz']:
            raise ValueError('Deflection tower source mismatch')
        data['nacelle'] = read_tower(path, manifest, times)['nacelle'][:, 0]
    if beamdyn:
        from .prebend import read_reference
        ref = read_reference(path, manifest)
        if ref is None or data.get('reference_sha256') != manifest['files']['blade-reference.json']:
            raise ValueError('BeamDyn reference mismatch')
        data['tip_local_m'] = np.asarray(ref['tip_local_m'])
    elif manifest.get('schema') == 'wfrl.farm-flex-review.v3':
        raise ValueError('BeamDyn package requires BeamDyn deflection channels')
    return data


COLORS = {'Reference': (.55, .61, .69, 1), 'Actual': (1, .27, .04, 1),
          'Total': (1, .85, .3, 1), 'OoP': (.05, .8, 1, 1),
          'IP': (1, .15, .65, 1), 'Axial': (.6, .8, .35, 1)}


class ComparisonView:
    def __init__(self, owner, data, vertices, faces):
        import bpy
        from .scene_builder import _mesh_object, _primitive
        from .tip_tracking import _material
        self.owner, self.data = owner, data
        self.rows = {}
        self.labels = []
        self.objects = []
        self.rest = np.asarray(vertices, dtype=float)
        self.collection = owner.blades[0][0].users_collection[0]
        # Saved .blend files already contain the previous comparison geometry.
        for obj in list(owner.scene.objects):
            if obj.name.startswith('WFRL.Deflection.T1.'):
                bpy.data.objects.remove(obj, do_unlink=True)
        self.materials = {key: _material('WFRL.Deflection.' + key, color) for key, color in COLORS.items()}
        mat = _material('WFRL.Deflection.Ghost', COLORS['Reference'])
        mat.diffuse_color = (*COLORS['Reference'][:3], .38)
        node = mat.node_tree.nodes.get('Principled BSDF')
        node.inputs['Alpha'].default_value = .38
        node.inputs['Emission Strength'].default_value = .2
        # A reference overlay must not dither a false shadow onto the real blade.
        mat.surface_render_method = 'BLENDED'
        self.ghost = _mesh_object(self.collection, 'WFRL.Deflection.T1.Ghost', vertices, faces, mat)
        self.ghost.visible_shadow = False
        self.objects.append(self.ghost)
        self.markers = {}
        for key in ('Reference', 'Actual'):
            self.markers[key] = _primitive(self.collection, 'uv_sphere', 'WFRL.Deflection.T1.' + key,
                                           (0, 0, 0), (.32, .32, .32), self.materials[key])
            self.objects.append(self.markers[key])
        self.lines = {}
        for key in ('Total', 'OoP', 'IP', 'Axial'):
            curve = bpy.data.curves.new('WFRL.Deflection.' + key, 'CURVE')
            curve.dimensions = '3D'
            curve.bevel_depth = .055
            curve.bevel_resolution = 2
            curve.materials.append(self.materials[key])
            spline = curve.splines.new('POLY'); spline.points.add(1)
            obj = bpy.data.objects.new('WFRL.Deflection.T1.' + key, curve)
            self.collection.objects.link(obj)
            self.lines[key] = obj
            self.objects.append(obj)
        for obj in self.objects:
            obj.hide_select = True
            obj['provenance'] = data['reference'] + '; true scale in metres'
        self.handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_labels, (), 'WINDOW', 'POST_PIXEL')

    def visible(self, value):
        for obj in self.objects:
            obj.hide_set(not value)
            obj.hide_render = not value
        if not value:
            self.labels = []

    def update(self, scene, i, alpha, time):
        from mathutils import Vector
        data = self.data
        pose = data['poses'][i] * (1 - alpha) + data['poses'][i + 1] * alpha
        simulation = data['simulation'][i] * (1 - alpha) + data['simulation'][i + 1] * alpha
        nacelle = None
        if 'nacelle' in data:
            from .tower_motion import interpolate_transform
            nacelle = interpolate_transform(data['nacelle'][i], data['nacelle'][i+1], alpha)
        for b in (1, 2, 3):
            obj = self.owner.blades[b - 1][0]
            hub, axes, pitched = rigid_frame(data['scalars'], pose, b, nacelle)
            if 'tip_local_m' in data:
                axes = pitched
                reference = hub + pitched @ data['tip_local_m']
            else:
                reference = hub + axes[:, 2] * data['scalars']['TipRad']
            # -2 is the structural reference vertex, -1 remains the cosmetic apex.
            actual = obj.matrix_world @ obj.data.vertices[-2].co
            row = compare(actual, reference, axes, simulation[b - 1])
            row.update(time=float(time), blade=b, pitched=pitched, hub=hub,
                       interpolated=bool(1e-8 < alpha < 1 - 1e-8))
            self.rows[b] = row
        row = self.rows[int(scene.wfrl_deflection_blade)]
        if scene.camera and scene.camera.name == 'WFRL.Camera.T1.TipComparison':
            center = (row['reference'] + row['actual']) * .5 - row['axes'][:, 2] * 4
            # Include a radial viewing component so xc/yc do not collapse to
            # the same screen line when viewing their plane edge-on.
            scene.camera.location = Vector(center - row['axes'][:, 1] * 20
                                           - row['axes'][:, 0] * 18 + row['axes'][:, 2] * 18)
            scene.camera.rotation_euler = (Vector(center) - scene.camera.location).to_track_quat('-Z', 'Y').to_euler()
        show = scene.wfrl_deflection_visible and 0 in self.owner.visible_turbines
        self.visible(show)
        if not show:
            return
        points = self.rest @ row['pitched'].T + row['hub']
        self.ghost.data.vertices.foreach_set('co', points.astype(np.float32).ravel())
        self.ghost.data.update()
        ref, actual, axes, c = row['reference'], row['actual'], row['axes'], row['components']
        self.markers['Reference'].location = Vector(ref)
        self.markers['Actual'].location = Vector(actual)
        corners = [ref, ref + axes[:, 0] * c[0], ref + axes[:, 0] * c[0] + axes[:, 1] * c[1], actual]
        segments = {'Total': (ref, actual), 'OoP': corners[:2], 'IP': corners[1:3], 'Axial': corners[2:]}
        for key, (a, b) in segments.items():
            spline = self.lines[key].data.splines[0]
            spline.points[0].co = (*a, 1)
            spline.points[1].co = (*b, 1)
        # Fixed pixel label offsets keep nearby tip markers legible at farm scale.
        self.labels = [(ref, 'Reference', 'Rigid reference', (12, 42)),
                       (actual, 'Actual', f"T1 Blade {row['blade']} actual tip", (12, -24)),
                       (actual, 'OoP', f"{'Root x' if len(row['simulation']) == 3 else 'OoP'} {c[0]:+.4f} m", (12, -44)),
                       (actual, 'IP', f"{'Root y' if len(row['simulation']) == 3 else 'IP'} {c[1]:+.4f} m", (12, -64)),
                       (actual, 'Total', f"Total {row['distance']:.4f} m", (12, -84))]

    def draw_labels(self):
        import bpy, blf, gpu
        from gpu_extras.batch import batch_for_shader
        from bpy_extras.view3d_utils import location_3d_to_region_2d
        from mathutils import Vector
        context = bpy.context
        if (not self.owner.enabled or context.scene != self.owner.scene
                or not context.region_data or not self.labels
                or not context.space_data.overlay.show_overlays):
            return
        scale = max(1., context.preferences.system.ui_scale)
        blf.size(0, 13 * scale)
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        try:
            for point, key, label, offset in self.labels:
                xy = location_3d_to_region_2d(context.region, context.region_data, Vector(point))
                if xy is None:
                    continue
                x, y = xy.x + offset[0] * scale, xy.y + offset[1] * scale
                width = blf.dimensions(0, label)[0] + 10 * scale
                quad = [(x-4*scale, y-4*scale), (x+width, y-4*scale),
                        (x+width, y+15*scale), (x-4*scale, y+15*scale)]
                shader.bind(); shader.uniform_float('color', (.015, .025, .04, .82))
                batch_for_shader(shader, 'TRIS', {'pos': quad}, indices=((0,1,2),(0,2,3))).draw(shader)
                blf.color(0, *COLORS[key]); blf.position(0, x, y, 0); blf.draw(0, label)
        finally:
            gpu.state.blend_set('NONE')

    def close(self):
        import bpy
        if self.handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self.handle, 'WINDOW')
            self.handle = None
        self.labels = []; self.rows.clear()
        for obj in self.objects:
            try:
                data = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if data.users == 0:
                    if isinstance(data, bpy.types.Mesh): bpy.data.meshes.remove(data)
                    elif isinstance(data, bpy.types.Curve): bpy.data.curves.remove(data)
            except ReferenceError:
                pass
        self.objects.clear()


def refresh(scene, context):
    from . import farm_flex
    if farm_flex.is_active(scene):
        farm_flex._ACTIVE.update(scene)


def draw_panel(layout, scene):
    from . import farm_flex
    active = farm_flex._ACTIVE
    view = getattr(active, 'comparison', None)
    box = layout.box()
    box.label(text='T1 · 叶尖挠度核对', icon='DRIVER_DISTANCE')
    if view is None:
        box.label(text='未提供同源挠度数据，无法核对', icon='INFO')
        return
    box.row(align=True).prop(scene, 'wfrl_deflection_blade', text='叶片', expand=True)
    views = box.row(align=True)
    views.operator('wfrl.deflection_view', text='叶尖', icon='VIEWZOOM')
    op = views.operator('wfrl.farm_flex_view', text='叶轮')
    op.turbine = 'T1'; op.angle = 'FRONT'
    views.operator('wfrl.farm_flex_view', text='Down').turbine = 'T1'
    layers = box.row(align=True)
    layers.prop(scene, 'wfrl_deflection_visible', text='虚影 / 分量', toggle=True)
    layers.prop(scene, 'wfrl_flex_show_tip_trails', text='运动轨迹', toggle=True)
    row = view.rows.get(int(scene.wfrl_deflection_blade))
    if row is None:
        box.label(text='等待同步叶片坐标')
        return
    box.label(text='挠度对照 · ' + ('插值帧' if row['interpolated'] else '保存帧'))
    grid = box.row(align=True)
    for title, values in [('方向', (['根系 x', '根系 y', '根系 z'] if len(row['simulation']) == 3 else ['面外', '面内'])), ('仿真 / m', row['simulation']),
                          ('坐标差 / m', row['components'][:len(row['simulation'])]), ('偏差 / mm', row['error'] * 1000)]:
        column = grid.column(align=True); column.label(text=title)
        for value in values:
            column.label(text=value if isinstance(value, str) else f'{value:+.4f}')
    box.label(text=f"总位移   {row['distance']:.4f} m")
    if 0 not in active.visible_turbines:
        box.label(text='T1 已隐藏；点“叶尖”切回', icon='INFO')
    header, details = layout.panel('wfrl_deflection_details', default_closed=True)
    header.label(text='图例与计算口径')
    if details is not None:
        details.label(text='灰：参考点 · 橙：实际点')
        details.label(text='黄：总位移 · 青：根系 x' if len(row['simulation']) == 3 else '黄：总位移 · 青：面外')
        details.label(text='粉：根系 y · 绿：根系 z' if len(row['simulation']) == 3 else '粉：面内 · 绿：轴向')
        details.label(text=f"轴向坐标差  {row['components'][2]:+.4f} m")
        details.label(text='BeamDyn：相对预弯参考，含变桨根系 xyz' if len(row['simulation']) == 3 else '轴向未保存仿真输出，不作数值对照')
        details.label(text='偏差 = 坐标差投影 − 仿真输出')
        details.label(text='参考点随塔顶平移、倾斜与叶轮姿态移动' if active.tower_motion is not None else '参考点按同刻刚性姿态独立计算')
        details.label(text='结构叶尖与彩色轨迹尖端定义不同')
        details.label(text='轨迹：叶片 1 橙 · 叶片 2 蓝 · 叶片 3 红')
