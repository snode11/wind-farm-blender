"""Fixed nacelle-mounted clearance radar; lines are direction illustrations only."""
import math


def beam_material(active):
    import bpy
    name = 'WFRL.ClearanceBeam.' + ('Hit' if active else 'Idle')
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
        color = (1., .22, .015, 1.) if active else (.18, .55, .72, 1.)
        material.diffuse_color = color
        material.use_nodes = True
        shader = material.node_tree.nodes.get('Principled BSDF')
        shader.inputs['Base Color'].default_value = color
        shader.inputs['Emission Color'].default_value = color
        shader.inputs['Emission Strength'].default_value = 3. if active else .7
    return material


def update_beams(scene, turbine_id, activity=(False, False, False)):
    for index, active in enumerate(activity, 1):
        beam = scene.objects.get(f'WFRL.Turbine.{turbine_id}.ClearanceRadar.Beam{index}')
        if beam is not None and beam.get('wfrl_beam_active') != active:
            material = beam_material(active)
            if beam.data.materials:
                beam.data.materials[0] = material
            else:
                beam.data.materials.append(material)
            beam.data.bevel_depth = .030 if active else .018
            beam['wfrl_beam_active'] = active


def reset_beams(scene):
    from . import farm_flex
    protected = []
    for active in tuple(farm_flex._ACTIVES.values()):
        try:
            if active.scene != scene:
                protected.extend((obj, obj.data) for obj in active.scene.objects
                                 if '.ClearanceRadar.Beam' in obj.name)
        except ReferenceError:
            continue
    for beam in scene.objects:
        if '.ClearanceRadar.Beam' in beam.name:
            if any(beam == obj or beam.data == data for obj, data in protected):
                continue
            beam.data.materials.clear()
            beam.data.materials.append(beam_material(False))
            beam.data.bevel_depth = .018
            beam['wfrl_beam_active'] = False


def _radar_primitive(collection, kind, name, material):
    """Build in a private mesh, including during load_post in Edit Mode."""
    import bpy
    import bmesh
    mesh = bpy.data.meshes.new(name + '.Mesh')
    obj = None
    try:
        bm = bmesh.new()
        try:
            if kind == 'cube':
                bmesh.ops.create_cube(bm, size=2.0)
            elif kind == 'cylinder':
                bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=32,
                                     radius1=1.0, radius2=1.0, depth=2.0)
            else:
                raise ValueError(kind)
            bm.to_mesh(mesh)
        finally:
            bm.free()
        mesh.materials.append(material)
        obj = bpy.data.objects.new(name, mesh)
        collection.objects.link(obj)
        return obj
    except BaseException as error:
        for blocks, block in ((bpy.data.objects, obj), (bpy.data.meshes, mesh)):
            try:
                if block is not None and (block is obj or block.users == 0):
                    blocks.remove(block, do_unlink=True)
            except ReferenceError:
                pass
            except (RuntimeError, ValueError) as cleanup_error:
                error.add_note(f'Radar resource cleanup failed: {cleanup_error}')
        raise


def ensure_radar(scene, turbine_id, origin=None, directions=None, *, beam_origins=None):
    """Refresh fittings without touching the active scene, selection or mode."""
    import bpy
    pools = {name: getattr(bpy.data, name) for name in ('objects', 'meshes', 'curves', 'materials')}
    existing = {name: {block.as_pointer() for block in blocks} for name, blocks in pools.items()}
    try:
        return _ensure_radar(scene, turbine_id, origin, directions, beam_origins=beam_origins)
    except BaseException as error:
        for name, blocks in pools.items():
            for block in list(blocks):
                try:
                    if block.as_pointer() in existing[name] or block.library or block.use_fake_user:
                        continue
                    if name == 'objects':
                        if any(owner != scene for owner in block.users_scene):
                            continue
                        blocks.remove(block, do_unlink=True)
                    elif block.users == 0 and getattr(block, 'shape_keys', None) is None:
                        blocks.remove(block)
                except ReferenceError:
                    pass
                except Exception as cleanup_error:
                    error.add_note(f'Radar resource cleanup failed ({name}): {cleanup_error}')
        raise


def _ensure_radar(scene, turbine_id, origin, directions, *, beam_origins):
    import bpy
    from .materials import get_material
    yaw = scene.objects.get(f'WFRL.Turbine.{turbine_id}.YawRoot')
    if yaw is None:
        raise ValueError('Radar turbine is absent from scene: ' + turbine_id)
    collection = yaw.users_collection[0]
    prefix = f'WFRL.Turbine.{turbine_id}.ClearanceRadar'
    # Preview mount only; a loaded package supplies its own calibration.
    def vector(value):
        try:
            values = tuple(value)
            if len(values) == 3 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                        and math.isfinite(v) for v in values):
                return values
        except (TypeError, OverflowError):
            pass
        raise ValueError('Radar mounting vectors must contain three finite numbers')

    origin = vector(origin if origin is not None else (-2, 0, 0))
    if directions is None:
        directions = [(-math.sin(math.radians(a)), 0, -math.cos(math.radians(a)))
                      for a in (6.45, 8.5, 10.54)]
    try:
        directions = tuple(vector(direction) for direction in directions)
        norms = tuple(math.sqrt(sum(v*v for v in direction)) for direction in directions)
        if not norms or any(not math.isfinite(norm) or norm <= 0 for norm in norms):
            raise ValueError('Radar beam directions must have a finite nonzero norm')
        if beam_origins is not None:
            beam_origins = tuple(vector(value) for value in beam_origins)
            if len(beam_origins) != len(directions):
                raise ValueError('Radar beam origins must match the direction count')
    except (TypeError, OverflowError) as exc:
        raise ValueError('Invalid radar mounting calibration') from exc
    def part(suffix, offset, scale, material, kind='cube'):
        name = prefix + suffix
        obj = scene.objects.get(name)
        if obj is None:
            obj = _radar_primitive(collection, kind, name, get_material(material))
        obj.parent = yaw
        obj.matrix_parent_inverse.identity()
        obj.location = tuple(origin[i] + offset[i] for i in range(3))
        obj.scale = scale
        obj.data.materials[0] = get_material(material)
        if kind == 'cube':
            bevel = obj.modifiers.get('WFRL.Radar.EdgeSoftening')
            if bevel is None:
                bevel = obj.modifiers.new('WFRL.Radar.EdgeSoftening', 'BEVEL')
            bevel.width, bevel.segments = .06, 3
        obj['geometry_source'] = 'Illustrative radar housing and service fitting; not vendor CAD'
        return obj

    body = part('', (0, 0, .125), (.1, .08, .125), 'radar_body')
    part('.Bracket', (0, 0, .37), (.025, .03, .13), 'metal')
    part('.MountPlate', (0, 0, .485), (.10, .075, .015), 'metal')
    part('.MountPad', (0, 0, .507), (.105, .08, .008), 'rubber')
    # The original housing envelope and optical origin are unchanged. The
    # bezel surrounds the dark optical face; no simulated lens/echo is added.
    part('.Bezel', (0, 0, .002), (.078, .063, .012), 'metal')
    part('.Window', (0, 0, -.010), (.06, .047, .003), 'radar_window')
    part('.CoverSeal', (0, -.081, .125), (.085, .003, .110), 'rubber')
    part('.ServiceCover', (0, -.085, .125), (.081, .002, .105), 'radar_body')
    for i, (x, z) in enumerate(((-.061, .044), (.061, .044), (-.061, .206), (.061, .206))):
        bolt = part(f'.CoverScrew{i}', (x, -.090, z), (.007, .007, .003), 'metal', 'cylinder')
        bolt.rotation_euler.x = math.pi / 2
    gland = part('.CableGland', (-.106, 0, .205), (.014, .014, .020), 'rubber', 'cylinder')
    gland.rotation_euler.y = math.pi / 2
    name = prefix + '.Cable'
    cable = scene.objects.get(name)
    if cable is None:
        data = bpy.data.curves.new(name, 'CURVE')
        data.dimensions = '3D'
        data.bevel_depth = .006
        data.bevel_resolution = 2
        data.resolution_u = 8
        data.splines.new('BEZIER').bezier_points.add(4)
        data.materials.append(get_material('rubber'))
        cable = bpy.data.objects.new(name, data)
        collection.objects.link(cable)
        cable.parent = yaw
    for point, offset in zip(cable.data.splines[0].bezier_points,
                             ((-.123,0,.205), (-.17,0,.235), (-.16,0,.35), (-.13,0,.45), (-.13,0,.53))):
        point.co = tuple(origin[i] + offset[i] for i in range(3))
        point.handle_left_type = point.handle_right_type = 'AUTO'
    cable['geometry_source'] = 'Illustrative service cable; not a data connection'
    body['provenance'] = '浅蓝为光束方向；亮橙为有效测量提示；限定长度不代表命中点'
    material = beam_material(False)
    from .turbine_geometry import geometry_data
    display_length = geometry_data()['scalars']['TipRad'] * .95
    for index, (direction, norm) in enumerate(zip(directions, norms), 1):
        beam_origin = beam_origins[index-1] if beam_origins is not None else origin
        name = prefix + f'.Beam{index}'
        beam = scene.objects.get(name)
        if beam is None:
            curve = bpy.data.curves.new(name, 'CURVE')
            curve.dimensions = '3D'
            curve.bevel_depth = .012
            curve.splines.new('POLY').points.add(1)
            curve.materials.append(material)
            beam = bpy.data.objects.new(name, curve)
            collection.objects.link(beam)
            beam.parent = yaw
        beam.data.splines[0].points[0].co = (*beam_origin, 1)
        beam.data.splines[0].points[1].co = (*(beam_origin[i] + display_length*direction[i]/norm for i in range(3)), 1)
        beam['provenance'] = body['provenance']
        if 'wfrl_beam_active' in beam:
            del beam['wfrl_beam_active']
    update_beams(scene, turbine_id)
    return body
