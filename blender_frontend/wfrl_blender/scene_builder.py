"""Idempotent procedural scene construction for the Part 2 static prototype."""

from __future__ import annotations

import math

from .materials import get_material
from .scene_model import SceneDTO


COLLECTION_NAME = "WFRL_Scene"


def _bpy():
    import bpy
    return bpy


def _link(collection, obj):
    collection.objects.link(obj)


def _finish(obj, bevel: float = 0.0, smooth: bool = False):
    """Apply small presentation modifiers without baking the mesh."""
    if smooth and hasattr(obj.data, "polygons"):
        for polygon in obj.data.polygons:
            polygon.use_smooth = len(polygon.vertices) <= 4
    if bevel:
        modifier = obj.modifiers.new("WFRL.EdgeSoftening", "BEVEL")
        modifier.width = bevel
        modifier.segments = 3
        modifier.limit_method = "ANGLE"
    return obj


def _mesh_object(collection, name: str, vertices, faces, material):
    bpy = _bpy()
    mesh = bpy.data.meshes.new(name + ".Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    mesh.materials.append(material)
    _link(collection, obj)
    return obj


def _primitive(collection, kind: str, name: str, location, scale, material):
    bpy = _bpy()
    if kind == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1.0, depth=2.0, location=location)
    elif kind == "cone":
        bpy.ops.mesh.primitive_cone_add(vertices=32, radius1=1.0, radius2=0.78, depth=2.0, location=location)
    elif kind == "cube":
        bpy.ops.mesh.primitive_cube_add(location=location)
    elif kind == "uv_sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, location=location)
    else:
        raise ValueError(kind)
    obj = bpy.context.view_layer.objects.active
    obj.name = name
    obj.data.name = name + ".Mesh"
    obj.scale = scale
    obj.data.materials.append(material)
    for old in list(obj.users_collection):
        old.objects.unlink(obj)
    _link(collection, obj)
    return obj


def _rounded_nacelle_mesh(length: float, width: float, height: float):
    """Rounded rectangular service shell, preserving the configured envelope."""
    # Rounded end shoulders meet both the straight body and flat end plates
    # tangentially. Preserve the configured external envelope.
    sections = []
    steps = 16
    for k in range(steps, -1, -1):
        angle = math.pi*k/(2*steps)
        sections.append((-length*(.40+.10*math.sin(angle)), .72+.28*math.cos(angle)))
    sections.append((length*.38, 1.0))
    for k in range(1, steps+1):
        angle = math.pi*k/(2*steps)
        sections.append((length*(.38+.12*math.sin(angle)), .72+.28*math.cos(angle)))
    count = 64
    vertices = []
    for x, factor in sections:
        for index in range(count):
            angle = 2.0 * math.pi * index / count
            vertices.append((
                x,
                0.5 * width * factor * math.copysign(abs(math.cos(angle))**.65, math.cos(angle)),
                0.5 * height * factor * math.copysign(abs(math.sin(angle))**.65, math.sin(angle)),
            ))
    faces = []
    for section in range(len(sections) - 1):
        first, second = section * count, (section + 1) * count
        for index in range(count):
            next_index = (index + 1) % count
            faces.append((first + index, first + next_index,
                          second + next_index, second + index))
    faces.append(tuple(reversed(range(count))))
    last = (len(sections) - 1) * count
    faces.append(tuple(last + index for index in range(count)))
    return vertices, faces


def _cone(collection, name: str, radius1: float, radius2: float, depth: float, material):
    """Create a cone at the origin so its parent can own the local placement."""
    bpy = _bpy()
    bpy.ops.mesh.primitive_cone_add(
        vertices=96,
        radius1=radius1,
        radius2=radius2,
        depth=depth,
        location=(0, 0, 0),
    )
    obj = bpy.context.view_layer.objects.active
    obj.name = name
    obj.data.name = name + ".Mesh"
    obj.data.materials.append(material)
    for old in list(obj.users_collection):
        old.objects.unlink(obj)
    _link(collection, obj)
    return obj


def _make_blade(collection, parent, name: str, angle: float):
    from .turbine_geometry import blade_mesh, geometry_data

    bpy = _bpy()
    # ZYX gives Rx(azimuth) @ Ry(precone), followed by the blade's Rz(pitch).
    # Pitching therefore cannot collapse the three radial span directions.
    pitch_root = bpy.data.objects.new(name + ".PitchRoot", None)
    _link(collection, pitch_root)
    pitch_root.parent = parent
    pitch_root.rotation_mode = "ZYX"
    pitch_root.rotation_euler = (angle, math.radians(geometry_data()["scalars"]["PreCone(1)"]), 0.0)
    # Higher presentation sampling keeps the aerofoil trailing edge and root
    # transition smooth in close shots while preserving the source stations.
    shared = bpy.data.meshes.get("WFRL.SharedBlade.SourceLoft")
    if shared is None:
        vertices, faces = blade_mesh(subdiv=8, ring_points=96)
        blade = _mesh_object(collection, name, vertices, faces, get_material("blade"))
        blade.data.name = "WFRL.SharedBlade.SourceLoft"
    else:
        blade = bpy.data.objects.new(name, shared)
        _link(collection, blade)
    blade.parent = pitch_root
    blade.rotation_mode = "XYZ"
    _finish(blade, bevel=0.012, smooth=True)
    blade["pitch_axis"] = "LOCAL_Z"
    blade["geometry_source"] = "Packaged OpenFAST AeroDyn 19 stations / 8 airfoil outlines; smooth interpolation and illustrative tapered tip"
    root_fairing = _cone(
        collection,
        name + ".RootFairing",
        radius1=1.79,
        radius2=1.771,
        depth=0.18,
        material=get_material("blade"),
    )
    root_fairing.parent = pitch_root
    root_fairing.location = (0, 0, 1.62)
    _finish(root_fairing, bevel=0.012, smooth=True)
    root_fairing["geometry_source"] = "NREL 5MW blade-root fairing presentation detail"
    return blade


def _make_turbine(collection, turbine):
    from .turbine_geometry import geometry_data, hub_position, tower_mesh

    bpy = _bpy()
    data = geometry_data()
    scalars, shell = data["scalars"], data["shell"]
    prefix = f"WFRL.Turbine.{turbine.turbine_id}"
    root = bpy.data.objects.new(prefix, None)
    root.location = (turbine.x_m, turbine.y_m, 0.0)
    _link(collection, root)
    vertices, faces = tower_mesh()
    tower = _mesh_object(collection, prefix + ".Tower", vertices, faces, get_material("tower"))
    tower.parent = root
    _finish(tower, smooth=True)
    bearing = _primitive(
        collection,
        "cylinder",
        prefix + ".YawBearing",
        (0, 0, 0),
        (1.98, 1.98, 0.10),
        get_material("hub"),
    )
    bearing.parent = root
    bearing.location = (0, 0, scalars["TowerHt"] - 1.55)
    _finish(bearing, bevel=0.025, smooth=True)
    bearing["geometry_source"] = "NREL 5MW tower-top yaw bearing presentation detail"
    for z, radius in ((0.28, 3.06), (scalars["TowerHt"] - 1.78, 1.945)):
        collar = _primitive(collection, "cylinder", prefix + f".TowerCollar{int(z)}", (0, 0, 0), (radius, radius, 0.16), get_material("hub"))
        collar.parent = root
        collar.location = (0, 0, z)
        _finish(collar, bevel=0.04, smooth=True)
    yaw = bpy.data.objects.new(prefix + ".YawRoot", None)
    _link(collection, yaw)
    yaw.parent = root
    yaw.location = (0, 0, scalars["TowerHt"])
    hub_x, _, hub_z = hub_position()
    # Shell dimensions are explicit local YAML dimensions (8.03 x 3.30 x 3.30 m).
    # Elliptical longitudinal sections keep the source envelope while avoiding a boxy body.
    nacelle_vertices, nacelle_faces = _rounded_nacelle_mesh(
        shell["length"], shell["width"], shell["height"]
    )
    nacelle = _mesh_object(
        collection,
        prefix + ".Nacelle",
        nacelle_vertices,
        nacelle_faces,
        get_material("nacelle"),
    )
    nacelle.parent = yaw
    nacelle.location = (shell["nacelle_x_bias"] * scalars["OverHang"], 0, 0.15)
    _finish(nacelle, smooth=True)
    roof = _primitive(collection, "cube", prefix + ".NacelleRoof", (0, 0, 0), (1.2, 0.82, 0.045), get_material("nacelle"))
    roof.parent = yaw
    roof.location = (0.65, 0, shell["height"] / 2 + 0.09)
    _finish(roof, bevel=0.025)
    shaft = _primitive(collection, "cylinder", prefix + ".MainShaft", (0, 0, 0), (0.95, 0.95, 0.7), get_material("hub"))
    shaft.parent = yaw
    shaft.location = (hub_x + 0.7, 0, hub_z - scalars["TowerHt"] - 0.06)
    shaft.rotation_euler[1] = math.radians(90.0 - scalars["ShftTilt"])
    _finish(shaft, bevel=0.06, smooth=True)
    rotor = bpy.data.objects.new(prefix + ".Rotor", None)
    _link(collection, rotor)
    rotor.parent = yaw
    rotor.location = (hub_x, 0, hub_z - scalars["TowerHt"])
    rotor.rotation_mode = "XYZ"
    rotor.rotation_euler.y = math.radians(-scalars["ShftTilt"])
    radius = shell["spinner_radius"]
    hub = _primitive(
        collection,
        "uv_sphere",
        prefix + ".Hub",
        (0, 0, 0),
        (radius * 0.62, radius, radius * 0.95),
        get_material("hub"),
    )
    hub.parent = rotor
    hub.location = (-0.35, 0.0, 0.0)
    _finish(hub, smooth=True)
    # Rounded spinner closes to one pole; the previous truncated cone had
    # a visibly flat nose. Retain the same 3.2 m illustrative axial envelope.
    verts=[];faces=[];segments=64;rings=24
    for i in range(rings):
        angle=math.pi*i/(2*rings)
        x=.15-3.2*math.sin(angle);r=2.15*math.cos(angle)
        verts.extend((x,r*math.cos(math.tau*j/segments),r*math.sin(math.tau*j/segments)) for j in range(segments))
    for i in range(rings-1):
        a=i*segments;b=a+segments
        faces.extend((a+j,b+j,b+(j+1)%segments,a+(j+1)%segments) for j in range(segments))
    faces.append(tuple(range(segments)))
    pole=len(verts);verts.append((-3.05,0,0));a=(rings-1)*segments
    faces.extend((a+j,pole,a+(j+1)%segments) for j in range(segments))
    spinner = _mesh_object(collection,prefix + ".Spinner",verts,faces,get_material("hub"))
    spinner.parent = rotor
    _finish(spinner, smooth=True)
    spinner["geometry_source"] = "NREL 5MW rotor spinner presentation detail"
    for index in range(3):
        _make_blade(collection, rotor, prefix + f".Blade{index + 1}", math.radians(120.0 * index))
    from .mechanical_details import add_mechanical_details
    add_mechanical_details(collection, root, yaw, rotor, prefix, scalars, shell)
    root["wfrl_turbine_id"] = turbine.turbine_id
    root["geometry_source"] = data["source"]
    root["rotor_diameter_m"] = 2 * scalars["TipRad"]
    root["hub_height_m"] = hub_z
    return root


def _make_ground_grid(collection):
    material = get_material("grid")
    for x in range(-100, 1201, 100):
        _primitive(collection, "cube", f"WFRL.Grid.X{x}", (float(x), 30.0, 0.06), (0.22, 260.0, 0.035), material)
    for y in range(-200, 301, 100):
        _primitive(collection, "cube", f"WFRL.Grid.Y{y}", (500.0, float(y), 0.06), (700.0, 0.22, 0.035), material)


def _make_terrain_surface(collection):
    from .landscape import build_landscape
    return build_landscape(collection)


def _make_label(collection, text: str, name: str, location, size: float = 11.0):
    bpy = _bpy()
    curve = bpy.data.curves.new(name + ".Data", "FONT")
    curve.body = text
    curve.align_x = "CENTER"
    curve.align_y = "CENTER"
    curve.size = size
    curve.extrude = 0.025
    obj = bpy.data.objects.new(name, curve)
    obj.location = location
    obj.rotation_euler = (math.radians(72.0), 0.0, 0.0)
    curve.materials.append(get_material("accent"))
    _link(collection, obj)
    return obj


def _make_wake(collection, scene):
    """A soft illustrative volume; no measured field and no solid annuli."""
    from .materials import wake_volume_material
    from .turbine_geometry import hub_position, geometry_data
    bpy = _bpy()
    hub_z = hub_position()[2]
    tower_height = geometry_data()["scalars"]["TowerHt"]
    for turbine in scene.turbines:
        volume = _primitive(collection, "cube", f"WFRL.WakeProxy.{turbine.turbine_id}.Volume",
                            (turbine.x_m + 245, turbine.y_m, hub_z), (240, 100, 100), wake_volume_material())
        volume.parent = bpy.data.objects[f"WFRL.Turbine.{turbine.turbine_id}.YawRoot"]
        volume.location = (245, 0, hub_z - tower_height)
        volume["fidelity"] = "SYNTH"
        volume["provenance"] = "Illustrative expanding wake; no FLORIS solve or FAST.Farm DisXY"
        # The proxy body is a viewport envelope.  Rendering the large
        # procedural volume would absorb the world light and make the sky and
        # turbine silhouettes unreadable; the animated line layer carries the
        # visual wake cue in stills and animation.
        volume.hide_render = True
        volume.display_type = "WIRE"
        from .wake import PROXY_LINE_COUNT, PROXY_RING_COUNT, PROXY_PULSE_COUNT, proxy_line_points, proxy_ring_points_local, proxy_pulse_points
        for index in range(PROXY_LINE_COUNT):
            curve = bpy.data.curves.new(f"WFRL.WakeProxy.{turbine.turbine_id}.Line{index}.Data", 'CURVE')
            curve.dimensions = '3D'
            curve.bevel_depth = .14
            curve.bevel_resolution = 3
            spline = curve.splines.new('POLY')
            spline.points.add(47)
            for point, co in zip(spline.points, proxy_line_points(index, 48, 0.0)):
                point.co = co
            curve.materials.append(get_material('wake_line'))
            obj = bpy.data.objects.new(f"WFRL.WakeProxy.{turbine.turbine_id}.Line{index}", curve)
            _link(collection, obj)
            obj['fidelity'] = 'SYNTH'
            obj.visible_shadow = False
            obj['provenance'] = 'Downstream illustrative tracers; not a solved flow field'
            obj.parent = volume.parent
            # Points stay hub-local throughout animation; only the object
            # supplies the offset from the tower-top yaw pivot to the hub.
            obj.location = (0, 0, hub_z - tower_height)
        for index in range(PROXY_RING_COUNT):
            curve = bpy.data.curves.new(f"WFRL.WakeProxy.{turbine.turbine_id}.Ring{index}.Data", 'CURVE')
            curve.dimensions = '3D'
            curve.bevel_depth = .11
            curve.bevel_resolution = 2
            spline = curve.splines.new('POLY')
            spline.points.add(95)
            spline.use_cyclic_u = True
            for point, co in zip(spline.points, proxy_ring_points_local(index, 96, 0)):
                point.co = co
            curve.materials.append(get_material('wake_line'))
            obj = bpy.data.objects.new(f"WFRL.WakeProxy.{turbine.turbine_id}.Ring{index}", curve)
            _link(collection, obj)
            obj.parent = volume.parent
            obj.location = (0, 0, hub_z - tower_height)
            obj['fidelity'] = 'SYNTH'
            obj.visible_shadow = False
            obj['provenance'] = 'Advecting illustrative wake cross-section; not a solved flow field'
        for index in range(PROXY_PULSE_COUNT):
            curve = bpy.data.curves.new(f"WFRL.WakeProxy.{turbine.turbine_id}.Pulse{index}.Data", 'CURVE')
            curve.dimensions = '3D'
            curve.bevel_depth = .48
            curve.bevel_resolution = 3
            spline = curve.splines.new('POLY')
            spline.points.add(9)
            for j, (point, co) in enumerate(zip(spline.points, proxy_pulse_points(index, 10, 0))):
                point.co = co
                point.radius = .15 + .85 * math.sin(math.pi * j / 18)
            curve.materials.append(get_material('wake_pulse'))
            obj = bpy.data.objects.new(f"WFRL.WakeProxy.{turbine.turbine_id}.Pulse{index}", curve)
            _link(collection, obj)
            obj.parent = volume.parent
            obj.location = (0, 0, hub_z - tower_height)
            obj.visible_shadow = False
            obj['fidelity'] = 'SYNTH'
            obj['provenance'] = 'Illustrative advection cue; not sampled wind velocity'


def _make_sensor_fixtures(collection, scene):
    """Create sensor rays/frustum and an exported-layer placeholder."""
    from .sensors import frustum_vertices
    bpy = _bpy()
    material = get_material("accent")
    turbine = scene.turbines[0]
    for index, offset in enumerate((-7.0, 0.0, 7.0), start=1):
        ray = _primitive(
            collection,
            "cube",
            f"WFRL.Fixture.T1.LidarRay{index}",
            (turbine.x_m + 72.0, turbine.y_m + offset, 88.0),
            (72.0, 0.18, 0.18),
            material,
        )
        ray["fidelity"] = "SYNTH"
        ray["provenance"] = "T1 lidar presentation fixture"
    # A line-only camera frustum makes the sensor view auditable without
    # pretending that Blender is rendering an optical measurement.
    vertices = frustum_vertices((72.0, turbine.y_m, 88.0), (-1.0, 0.0, -0.18),
                                (0.0, 0.0, 1.0), fov_deg=75.0, near_m=4.0, far_m=180.0)
    curve = bpy.data.curves.new("WFRL.Fixture.T1.SensorFrustum.Data", "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = 0.10
    curve.bevel_resolution = 1
    for start, end in ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
                       (0, 4), (1, 5), (2, 6), (3, 7)):
        spline = curve.splines.new("POLY")
        spline.points.add(1)
        spline.points[0].co = (*vertices[start], 1.0)
        spline.points[1].co = (*vertices[end], 1.0)
    curve.materials.append(material)
    frustum = bpy.data.objects.new("WFRL.Fixture.T1.SensorFrustum", curve)
    collection.objects.link(frustum)
    frustum["fidelity"] = "SYNTH"
    frustum["provenance"] = "Nacelle camera geometric field of view; no optical simulation"
    # DisXY is intentionally a muted, hidden placeholder until a real export exists.
    for index, x in enumerate((250.0, 330.0, 410.0), start=1):
        marker = _primitive(collection, "cube", f"WFRL.Fixture.DisXY{index}", (x, 170.0, 2.0), (10.0, 10.0, 0.15), get_material("foundation"))
        marker.hide_render = True
        marker.hide_set(True)
        marker["fidelity"] = "EXPORTED"
        marker["provenance"] = "FAST.Farm DisXY placeholder; no export loaded"


def _configure_environment(collection, scene=None):
    bpy = _bpy()
    from mathutils import Vector

    world = bpy.context.scene.world or bpy.data.worlds.new("WFRL.World")
    bpy.context.scene.world = world
    world.use_nodes = True
    background = next((item for item in world.node_tree.nodes if item.type == "BACKGROUND"), None)
    background.inputs["Color"].default_value = (0.022, 0.034, 0.050, 1.0)
    background.inputs["Strength"].default_value = 0.62
    for name, light_type, location, energy, color in (
        ("WFRL.Light.Key", "SUN", (-400.0, -700.0, 800.0), 1.4, (1.0, 0.94, 0.82)),
        ("WFRL.Light.Fill", "SUN", (900.0, 500.0, 420.0), 0.0, (0.58, 0.76, 1.0)),
        ("WFRL.Light.Rim", "SUN", (0.0, 0.0, 800.0), 0.0, (1.0, 0.88, 0.70)),
    ):
        data = bpy.data.lights.new(name + ".Data", light_type)
        data.energy = energy
        data.color = color
        if light_type == "SUN":
            data.angle = math.radians(1.5)
        if light_type == "AREA":
            data.shape = "DISK"
            data.size = 600.0
        obj = bpy.data.objects.new(name, data)
        obj.location = location
        obj.rotation_euler = (Vector((500.0, 30.0, 65.0)) - obj.location).to_track_quat("-Z", "Y").to_euler()
        _link(collection, obj)
    from .atmosphere import build_sky_features
    if scene is not None and scene.turbines:
        center = (sum(t.x_m for t in scene.turbines) / len(scene.turbines),
                  sum(t.y_m for t in scene.turbines) / len(scene.turbines), 68.0)
    else:
        center = (500.0, 30.0, 68.0)
    # Natural world sky supplies the background; no camera-facing solid cloud props.


def _configure_render():
    scene = _bpy().context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 1.2


def clear_scene(collection_name: str = COLLECTION_NAME):
    bpy = _bpy()
    collection = bpy.data.collections.get(collection_name)
    if not collection:
        return
    for obj in list(collection.objects):
        data = obj.data
        action = obj.animation_data.action if obj.animation_data else None
        bpy.data.objects.remove(obj, do_unlink=True)
        for block in (data, action):
            if block is not None and block.users == 0 and block.name.startswith("WFRL."):
                bpy.data.batch_remove(ids=(block,))
    bpy.data.collections.remove(collection)


def build_scene(scene: SceneDTO, collection_name: str = COLLECTION_NAME):
    bpy = _bpy()
    existing = bpy.data.collections.get(collection_name)
    if existing:
        clear_scene(collection_name)
    collection = bpy.data.collections.new(collection_name)
    bpy.context.scene.collection.children.link(collection)
    collection["wfrl_scene"] = scene.name
    collection["backend"] = scene.backend
    collection["wind_direction_deg"] = scene.wind_direction_deg
    collection["wind_speed_mps"] = scene.wind_speed_mps
    collection["fidelity"] = "SYNTH"
    for turbine in scene.turbines:
        _make_turbine(collection, turbine)
        foundation = _cone(collection, f"WFRL.Turbine.{turbine.turbine_id}.Foundation", 7.5, 7.5, 1.15, get_material("foundation"))
        foundation.location = (turbine.x_m, turbine.y_m, .225)
        _finish(foundation, bevel=.08, smooth=True)
        _make_label(collection, turbine.turbine_id, f"WFRL.Label.{turbine.turbine_id}", (turbine.x_m, turbine.y_m - 18.0, 6.0))
    _make_terrain_surface(collection)
    _make_ground_grid(collection)
    # A restrained set of arrows makes the inflow direction legible without implying a measured field.
    for index, y in enumerate((-72.0, 30.0, 132.0), start=1):
        _primitive(collection, "cube", f"WFRL.Inflow.Arrow{index}.Shaft", (60.0, y, 1.0), (38.0, 0.8, 0.8), get_material("accent"))
        arrow = _primitive(collection, "cone", f"WFRL.Inflow.Arrow{index}.Head", (102.0, y, 1.0), (4.0, 3.2, 2.2), get_material("accent"))
        arrow.rotation_euler[1] = math.radians(90.0)
    _make_wake(collection, scene)
    _make_sensor_fixtures(collection, scene)
    for obj in collection.objects:
        if obj.name.startswith(("WFRL.Grid.", "WFRL.Inflow.", "WFRL.Fixture.T1.")):
            obj.hide_render = True
            obj.hide_set(True)
    _configure_environment(collection, scene)
    _configure_render()
    return collection
