"""Saved, reversible appearance inspection for Blade Recon v0.2.

Only the sampled texture color is enhanced.  Texture alpha, atlas coordinates,
and the observed/inferred evidence input keep their existing contracts.  The
yellow/white rectangle is a manually selected inspection region, never a defect
detector.  All changes live in shader nodes; packed image pixels remain intact.
"""
import bpy
from mathutils import Matrix, Vector
import numpy as np


GROUP_NAME = 'WFRL.BladeRecon.ManualInspection.v1'
NODE_NAME = 'ManualSurfaceInspection'
VERSION = 1
BASE_SRGB = 150.0 / 255.0


def _linear(value):
    """Match the sRGB texture's automatic decode into shader linear color."""
    value = max(0.0, min(1.0, float(value)))
    return value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4


def _group():
    existing = bpy.data.node_groups.get(GROUP_NAME)
    if existing is not None and existing.get('blade_recon_inspection_version') == VERSION:
        return existing
    group = bpy.data.node_groups.new(GROUP_NAME, 'ShaderNodeTree')
    group['blade_recon_inspection_version'] = VERSION
    group['blade_recon_owned'] = True
    group['inspection_scope'] = 'manual UV region; appearance only; not defect detection'
    defaults = {
        'Texture': ('NodeSocketColor', (.5, .5, .5, 1)),
        'UV': ('NodeSocketVector', (0, 0, 0)),
        'Contrast': ('NodeSocketFloat', 0),
        'FalseColor': ('NodeSocketFloat', 0),
        'Gain': ('NodeSocketFloat', 6),
        'Baseline': ('NodeSocketFloat', _linear(BASE_SRGB)),
        'Lower': ('NodeSocketFloat', _linear(BASE_SRGB - .06)),
        'Upper': ('NodeSocketFloat', _linear(BASE_SRGB + .06)),
        'CenterU': ('NodeSocketFloat', .6),
        'CenterV': ('NodeSocketFloat', .065),
        'HalfU': ('NodeSocketFloat', .2),
        'HalfV': ('NodeSocketFloat', .016),
        'BorderU': ('NodeSocketFloat', .003),
        'BorderV': ('NodeSocketFloat', .0003),
        'ShowBox': ('NodeSocketFloat', 0),
    }
    for name, (kind, default) in defaults.items():
        socket = group.interface.new_socket(name=name, in_out='INPUT', socket_type=kind)
        socket.default_value = default
    group.interface.new_socket(name='Color', in_out='OUTPUT', socket_type='NodeSocketColor')
    nodes, links = group.nodes, group.links
    inp, out = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
    inp.location, out.location = (-1000, 0), (1400, 0)

    def math_node(name, operation, a, b=None, clamp=False):
        node = nodes.new('ShaderNodeMath')
        node.name, node.operation, node.use_clamp = name, operation, clamp
        if isinstance(a, (int, float)):
            node.inputs[0].default_value = a
        else:
            links.new(a, node.inputs[0])
        if b is not None:
            if isinstance(b, (int, float)):
                node.inputs[1].default_value = b
            else:
                links.new(b, node.inputs[1])
        return node.outputs[0]

    def mix_node(name, factor, first, second):
        node = nodes.new('ShaderNodeMixRGB')
        node.name, node.blend_type = name, 'MIX'
        links.new(factor, node.inputs[0])
        for index, value in ((1, first), (2, second)):
            if isinstance(value, tuple):
                node.inputs[index].default_value = value
            else:
                links.new(value, node.inputs[index])
        return node.outputs[0]

    luminance = nodes.new('ShaderNodeRGBToBW')
    luminance.name = 'Linear texture luminance'
    links.new(inp.outputs['Texture'], luminance.inputs[0])
    delta = math_node('Luminance minus baseline', 'SUBTRACT', luminance.outputs[0], inp.outputs['Baseline'])
    amplified = math_node('Contrast gain', 'MULTIPLY', delta, inp.outputs['Gain'])
    grey = math_node('Contrasted grey', 'ADD', amplified, inp.outputs['Baseline'], clamp=True)
    dark = math_node('Below lower threshold', 'LESS_THAN', luminance.outputs[0], inp.outputs['Lower'])
    bright = math_node('Above upper threshold', 'GREATER_THAN', luminance.outputs[0], inp.outputs['Upper'])
    false_color = mix_node('Dark magenta', dark, grey, (1., .015, .5, 1.))
    false_color = mix_node('Bright cyan', bright, false_color, (.015, 1., 1., 1.))
    enhanced = mix_node('Original or contrast', inp.outputs['Contrast'], inp.outputs['Texture'], grey)
    enhanced = mix_node('Original or false color', inp.outputs['FalseColor'], enhanced, false_color)

    separate = nodes.new('ShaderNodeSeparateXYZ')
    separate.name = 'Manual region atlas UV'
    links.new(inp.outputs['UV'], separate.inputs[0])
    # U is periodic around the airfoil.  V stays non-periodic along the span.
    du = math_node('U center difference', 'SUBTRACT', separate.outputs['X'], inp.outputs['CenterU'])
    du = math_node('Centered periodic U', 'ADD', du, .5)
    du = math_node('Periodic U fraction', 'FRACT', du)
    du = math_node('Signed periodic U', 'SUBTRACT', du, .5)
    du = math_node('Absolute periodic U', 'ABSOLUTE', du)
    dv = math_node('V center difference', 'SUBTRACT', separate.outputs['Y'], inp.outputs['CenterV'])
    dv = math_node('Absolute V', 'ABSOLUTE', dv)
    inside_u = math_node('Inside U extent', 'LESS_THAN', du, inp.outputs['HalfU'])
    inside_v = math_node('Inside V extent', 'LESS_THAN', dv, inp.outputs['HalfV'])
    inner_u = math_node('Inner U extent', 'SUBTRACT', inp.outputs['HalfU'], inp.outputs['BorderU'])
    inner_v = math_node('Inner V extent', 'SUBTRACT', inp.outputs['HalfV'], inp.outputs['BorderV'])
    edge_u = math_node('U border', 'GREATER_THAN', du, inner_u)
    edge_v = math_node('V border', 'GREATER_THAN', dv, inner_v)
    outline = math_node('Rectangle edges', 'MAXIMUM', edge_u, edge_v)
    outline = math_node('Rectangle U limit', 'MULTIPLY', outline, inside_u)
    outline = math_node('Rectangle V limit', 'MULTIPLY', outline, inside_v)
    outline = math_node('Manual box enabled', 'MULTIPLY', outline, inp.outputs['ShowBox'])
    # Alternating white/yellow segments remain distinct against both false colors.
    stripe_u = math_node('Stripe U', 'MULTIPLY', separate.outputs['X'], 80)
    stripe_v = math_node('Stripe V', 'MULTIPLY', separate.outputs['Y'], 800)
    stripe = math_node('Stripe sum', 'ADD', stripe_u, stripe_v)
    stripe = math_node('Stripe fraction', 'FRACT', stripe)
    stripe = math_node('Stripe selector', 'GREATER_THAN', stripe, .5)
    border_color = mix_node('Manual white yellow boundary', stripe, (1., .8, .015, 1.), (1., 1., 1., 1.))
    result = mix_node('Manual inspection boundary', outline, enhanced, border_color)
    links.new(result, out.inputs['Color'])
    # Stable names and compact columns make the saved graph inspectable.
    for index, node in enumerate(nodes):
        if node not in (inp, out):
            node.location = (-700 + (index // 8) * 300, 450 - (index % 8) * 150)
    return group


def ensure_material(mat):
    """Upgrade a v0.2 material and return its independent inspection node.

    Returns None for geometry-only materials.  TexMix's factor and color1 links,
    SurfaceTexture's UV link, and the source image are never changed.
    """
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return None
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    texture, mix = nodes.get('SurfaceTexture'), nodes.get('TexMix')
    if texture is None or mix is None or texture.image is None:
        return None
    node = nodes.get(NODE_NAME)
    if node is None or node.bl_idname != 'ShaderNodeGroup':
        node = nodes.new('ShaderNodeGroup')
        node.name = NODE_NAME
        node.label = '手动区域 / 外观增强（非缺陷识别）'
        node.location = (texture.location.x + 250, texture.location.y - 250)
    if node.node_tree is None or node.node_tree.get('blade_recon_inspection_version') != VERSION:
        node.node_tree = _group()
    source = texture.outputs['Color']
    if not any(link.from_socket == source for link in node.inputs['Texture'].links):
        links.new(source, node.inputs['Texture'])
    uv = next((n for n in nodes if n.bl_idname == 'ShaderNodeUVMap' and n.uv_map == 'atlas'), None)
    if uv is None:
        uv = nodes.new('ShaderNodeUVMap')
        uv.name, uv.uv_map = 'ManualInspectionAtlasUV', 'atlas'
    if not any(link.from_socket == uv.outputs['UV'] for link in node.inputs['UV'].links):
        links.new(uv.outputs['UV'], node.inputs['UV'])
    if not any(link.from_socket == node.outputs['Color'] for link in mix.inputs[2].links):
        links.new(node.outputs['Color'], mix.inputs[2])
    mat['blade_recon_inspection_version'] = VERSION
    return node


def _coordinates(scene, rotor):
    radius = max(float(rotor.tpl.r[0]), min(float(rotor.tpl.r[-1]),
        float(getattr(scene, 'wfrl_recon_inspect_radius', 59.))))
    tau = float(getattr(scene, 'wfrl_recon_inspect_tau', .6)) % 1.
    size = max(.3, float(getattr(scene, 'wfrl_recon_inspect_size', 2.)))
    blade = max(0, min(2, int(getattr(scene, 'wfrl_recon_inspect_blade', '1')) - 1))
    return blade, radius, tau, size


def _perimeter(rotor, radius):
    pts = rotor.tpl.foils * rotor.tpl.chord[:, None, None]
    closed = np.concatenate((pts, pts[:, :1]), axis=1)
    lengths = np.linalg.norm(np.diff(closed, axis=1), axis=-1).sum(axis=1)
    return max(.01, float(np.interp(radius, rotor.tpl.r, lengths)))


def update(scene, blades, rotor):
    """Apply display properties only; safe to call after every saved sample."""
    selected, radius, tau, size = _coordinates(scene, rotor)
    span = float(rotor.tpl.r[-1] - rotor.tpl.r[0])
    circumference = _perimeter(rotor, radius)
    threshold = float(getattr(scene, 'wfrl_recon_texture_threshold', .06))
    mode = getattr(scene, 'wfrl_recon_texture_mode', 'ORIGINAL')
    values = {
        'Contrast': float(mode == 'CONTRAST'),
        'FalseColor': float(mode == 'FALSE_COLOR'),
        'Gain': float(getattr(scene, 'wfrl_recon_texture_gain', 6.)),
        'Baseline': _linear(BASE_SRGB),
        'Lower': _linear(BASE_SRGB - threshold),
        'Upper': _linear(BASE_SRGB + threshold),
        'CenterU': tau,
        'CenterV': 1. - (radius - float(rotor.tpl.r[0])) / span,
        'HalfU': min(.499, size / (2. * circumference)),
        'HalfV': size / (2. * span),
        'BorderU': min(.015, max(.001, size * .006 / circumference)),
        'BorderV': max(.00004, size * .006 / span),
    }
    for index, obj in enumerate(blades):
        for mat in obj.data.materials:
            node = ensure_material(mat)
            if node is None:
                continue
            for name, value in values.items():
                socket = node.inputs[name]
                # RNA stores shader values as float32.  Compare with that same
                # precision so an unchanged Python float does not tag the
                # material again on every saved geometry sample.
                value = float(np.float32(value))
                if socket.default_value != value:
                    socket.default_value = value
            show_box = float(index == selected
                and getattr(scene, 'wfrl_recon_inspect_box', False)
                and getattr(scene, 'wfrl_recon_show_texture', True))
            if node.inputs['ShowBox'].default_value != show_box:
                node.inputs['ShowBox'].default_value = show_box
    scope = 'manual atlas UV region; appearance contrast only; not defect detection'
    if scene.get('blade_recon_inspection_scope') != scope:
        scene['blade_recon_inspection_scope'] = scope


def set_texture_mode(scene, blades, rotor, mode):
    """Switch appearance only; usable by independent or split scene adapters."""
    if mode not in {'ORIGINAL', 'CONTRAST', 'FALSE_COLOR'}:
        raise ValueError('未知纹理显示方式：'+str(mode))
    scene.wfrl_recon_texture_mode = mode
    update(scene, blades, rotor)
    return mode


def toggle_texture_mode(scene, blades, rotor, enhanced='FALSE_COLOR'):
    """One click between the packed original and a reversible enhancement."""
    if enhanced not in {'CONTRAST', 'FALSE_COLOR'}:
        raise ValueError('增强显示方式必须是 CONTRAST 或 FALSE_COLOR')
    mode = enhanced if scene.wfrl_recon_texture_mode == 'ORIGINAL' else 'ORIGINAL'
    return set_texture_mode(scene, blades, rotor, mode)


def _surface_sample(sequence, obj, blade, index, radius, tau):
    """Interpolate the exact estimated mesh triangle containing the atlas UV."""
    vertices, axes = sequence.geometry(index)
    vertices = vertices[blade].reshape(-1, 3)
    rotor = sequence.rotor
    target_uv = np.array([tau, 1. - (radius - rotor.tpl.r[0]) / (rotor.tpl.r[-1] - rotor.tpl.r[0])])
    layer = obj.data.uv_layers.get('atlas')
    if layer is None:
        raise ValueError('局部检查需要表面纹理的 atlas UV')
    for polygon in obj.data.polygons:
        if len(polygon.vertices) != 3:
            continue
        uv = np.array([tuple(layer.data[i].uv) for i in polygon.loop_indices], dtype=float)
        edges = np.column_stack((uv[1] - uv[0], uv[2] - uv[0]))
        if abs(float(np.linalg.det(edges))) < 1e-12:
            continue  # UV-degenerate tip caps are outside the atlas surface.
        for shift in (0., 1., -1.):
            local_uv = target_uv + [shift, 0.]
            weights = np.linalg.solve(edges, local_uv - uv[0])
            weights = np.array([1. - weights.sum(), weights[0], weights[1]])
            if weights.min() < -1e-6 or weights.max() > 1. + 1e-6:
                continue
            points = vertices[list(polygon.vertices)]
            point = weights @ points
            normal = np.cross(points[1] - points[0], points[2] - points[0])
            section = min(max(int(np.searchsorted(rotor.tpl.r, radius)) - 1, 0), len(rotor.tpl.r) - 2)
            fraction = (radius - rotor.tpl.r[section]) / (rotor.tpl.r[section + 1] - rotor.tpl.r[section])
            axis = axes[blade, section] * (1. - fraction) + axes[blade, section + 1] * fraction
            if np.dot(normal, point - axis) < 0:
                normal = -normal
            up = axes[blade, section + 1] - axes[blade, section]
            matrix = obj.matrix_world
            point = matrix @ Vector(point)
            normal = matrix.to_3x3().inverted().transposed() @ Vector(normal)
            up = matrix.to_3x3() @ Vector(up)
            if normal.length < 1e-8:
                raise ValueError('选中表面三角形法线无效')
            normal.normalize()
            up -= normal * up.dot(normal)
            if up.length < 1e-8:
                up = normal.cross(Vector((1., 0., 0.)))
                if up.length < 1e-8:
                    up = normal.cross(Vector((0., 1., 0.)))
            up.normalize()
            return point, normal, up
    raise ValueError('所选 atlas UV 没有匹配到估计叶片表面')


def focus(context, sequence, blades, areas=None, scene=None, index=None):
    """Pause and face the selected estimated surface; repeated clicks are stable."""
    scene = scene or context.scene
    screen = context.screen
    if screen and screen.is_animation_playing:
        bpy.ops.screen.animation_cancel(restore_frame=False)
    blade, radius, tau, size = _coordinates(scene, sequence.rotor)
    if blade >= len(blades):
        raise ValueError('选中的重建叶片尚未加载')
    index = min(max(scene.frame_current - 1 if index is None else index, 0), len(sequence.states) - 1)
    target, normal, up = _surface_sample(sequence, blades[blade], blade, index, radius, tau)
    right = up.cross(normal).normalized()
    up = normal.cross(right).normalized()
    # View quaternion maps camera local +Z to the outward surface normal.
    rotation = Matrix(((right.x, up.x, normal.x),
                       (right.y, up.y, normal.y),
                       (right.z, up.z, normal.z))).to_quaternion()
    views = 0
    if screen or areas is not None:
        for area in (areas if areas is not None else screen.areas):
            if area.type != 'VIEW_3D':
                continue
            space = area.spaces.active
            space.show_region_ui = True
            regions = tuple(space.region_quadviews) or (space.region_3d,)
            for region in regions:
                if region is None:
                    continue
                region.view_perspective = 'ORTHO'
                region.view_location = target
                region.view_rotation = rotation
                # Fixed lens and distance make repeat clicks independent of zoom.
                space.lens = 50
                region.view_distance = size * 1.35
            space.clip_start, space.clip_end = .01, 3000.
            space.shading.type = 'MATERIAL'
            space.overlay.show_floor = False
            space.overlay.show_axis_x = space.overlay.show_axis_y = False
            area.tag_redraw()
            views += 1
    update(scene, blades, sequence.rotor)
    return dict(blade=blade + 1, radius_m=radius, tau=tau,
                target=tuple(target), normal=tuple(normal), views=views)
