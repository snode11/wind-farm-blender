"""Fixed nacelle-mounted clearance radar; lines are direction illustrations only."""
import math


def ensure_radar(scene, turbine_id, origin=None, directions=None):
    import bpy
    from .scene_builder import _primitive
    from .materials import get_material
    yaw = scene.objects.get(f'WFRL.Turbine.{turbine_id}.YawRoot')
    if yaw is None:
        raise ValueError('Radar turbine is absent from scene: ' + turbine_id)
    collection = yaw.users_collection[0]
    prefix = f'WFRL.Turbine.{turbine_id}.ClearanceRadar'
    # Preview mount only; a loaded package supplies its own calibration.
    origin = origin if origin is not None else (-2, 0, 0)
    directions = directions or [(-math.sin(math.radians(a)), 0, -math.cos(math.radians(a)))
                                for a in (6.45, 8.5, 10.54)]
    body = scene.objects.get(prefix)
    if body is None:
        body = _primitive(collection, 'cube', prefix, (0, 0, 0), (.1, .08, .125), get_material('hub'))
        body.parent = yaw
        window = _primitive(collection, 'cube', prefix + '.Window', (0, 0, 0), (.06, .05, .005), get_material('graphite'))
        window.parent = body
        # Primitive parent scales are intentionally compensated.
        window.location = (0, 0, -1.04)
        window.scale = (.6, .625, .04)
    body.location = (origin[0], origin[1], origin[2] + .125)
    bracket = scene.objects.get(prefix + '.Bracket')
    if bracket is None:
        bracket = _primitive(collection, 'cube', prefix + '.Bracket', (0, 0, 0), (.025, .03, .13), get_material('hub'))
        bracket.parent = yaw
    bracket.location = (origin[0], origin[1], origin[2] + .37)
    body['provenance'] = '测量光束示意；限定长度，不代表命中点或测距结果'
    material = bpy.data.materials.get('WFRL.ClearanceBeam') or bpy.data.materials.new('WFRL.ClearanceBeam')
    material.diffuse_color = (1, .28, .015, 1)
    material.use_nodes = True
    shader = material.node_tree.nodes.get('Principled BSDF')
    shader.inputs['Base Color'].default_value = (1, .16, .005, 1)
    shader.inputs['Emission Color'].default_value = (1, .12, .002, 1)
    shader.inputs['Emission Strength'].default_value = 1
    from .turbine_geometry import geometry_data
    display_length = geometry_data()['scalars']['TipRad'] * .95
    for index, direction in enumerate(directions, 1):
        name = prefix + f'.Beam{index}'
        beam = scene.objects.get(name)
        if beam is None:
            curve = bpy.data.curves.new(name, 'CURVE'); curve.dimensions = '3D'; curve.bevel_depth = .012
            curve.splines.new('POLY').points.add(1)
            curve.materials.append(material)
            beam = bpy.data.objects.new(name, curve); collection.objects.link(beam); beam.parent = yaw
        norm = math.sqrt(sum(v*v for v in direction))
        beam.data.splines[0].points[0].co = (*origin, 1)
        beam.data.splines[0].points[1].co = (*(origin[i] + display_length*direction[i]/norm for i in range(3)), 1)
        beam['provenance'] = body['provenance']
    return body
