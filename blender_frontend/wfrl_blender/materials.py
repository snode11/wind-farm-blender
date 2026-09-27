"""Small Eevee-safe material palette for the WFRL industrial presentation."""

from __future__ import annotations


PALETTE = {
    "graphite": (0.018, 0.025, 0.035, 1.0),
    "tower": (0.62, 0.67, 0.70, 1.0),
    "blade": (0.82, 0.83, 0.80, 1.0),
    "nacelle": (0.57, 0.64, 0.68, 1.0),
    "hub": (0.67, 0.70, 0.71, 1.0),
    "metal": (0.34, 0.39, 0.44, 1.0),
    "rubber": (0.012, 0.016, 0.020, 1.0),
    "radar_body": (0.26, 0.32, 0.37, 1.0),
    "radar_window": (0.012, 0.035, 0.046, 1.0),
    "accent": (0.015, 0.55, 0.82, 1.0),
    "wake": (0.01, 0.48, 0.86, 1.0),
    "wake_line": (0.01, 1.0, 0.01, 1.0),
    "cinematic_line": (0.42, 0.72, 0.82, 1.0),
    "terrain": (0.10, 0.30, 0.12, 1.0),
    "terrain_dirt": (0.25, 0.13, 0.055, 1.0),
    "terrain_stone": (0.28, 0.31, 0.29, 1.0),
    "grid": (0.16, 0.38, 0.20, 1.0),
    "foundation": (0.20, 0.27, 0.25, 1.0),
}

# Roughness range, bump strength/distance, metallic and clear coat. Physical
# appearance only: these never feed geometry, calibration or measurement data.
SURFACES = {
    "tower": (.48, .58, .055, .010, 0., .04),
    "blade": (.36, .46, .025, .006, 0., .08),
    "nacelle": (.38, .48, .05, .010, 0., .08),
    "hub": (.34, .44, .04, .008, 0., .08),
    "metal": (.25, .37, .025, .004, .82, 0.),
    "rubber": (.72, .84, .045, .004, 0., 0.),
    "radar_body": (.38, .48, .04, .006, .35, .05),
    "radar_window": (.10, .14, .0, .0, 0., .32),
    "foundation": (.78, .94, .05, .018, 0., 0.),
}


def get_material(name: str):
    import bpy

    material = bpy.data.materials.get(f"WFRL.{name}") or bpy.data.materials.new(f"WFRL.{name}")
    material.diffuse_color = PALETTE.get(name, PALETTE["tower"])
    material.use_nodes = True
    if name in {"wake_line", "wake_pulse", "cinematic_line"}:
        return flow_material(material, pulse=name == "wake_pulse", cinematic=name == "cinematic_line")
    if name == "terrain":
        from .landscape import terrain_shader
        terrain_shader(material)
        return material
    node = next((item for item in material.node_tree.nodes if item.type == "BSDF_PRINCIPLED"), None)
    if node:
        node.inputs["Base Color"].default_value = PALETTE.get(name, PALETTE["tower"])
        node.inputs["Roughness"].default_value = 0.92 if name == "terrain" else (0.46 if name in {"tower", "blade"} else 0.66)
        node.inputs["Metallic"].default_value = 0.08 if name == "hub" else 0.0
        if name in SURFACES:
            low, high, strength, distance, metallic, coat = SURFACES[name]
            node.inputs["Metallic"].default_value = metallic
            node.inputs["Coat Weight"].default_value = coat
            node.inputs["Coat Roughness"].default_value = .25
            tex = material.node_tree.nodes.get("WFRL.MicroSurface") or material.node_tree.nodes.new("ShaderNodeTexNoise")
            tex.name = "WFRL.MicroSurface"
            tex.inputs["Scale"].default_value = 38.0
            tex.inputs["Detail"].default_value = 2.0
            tex.inputs["Roughness"].default_value = 0.65
            bump = material.node_tree.nodes.get("WFRL.MicroBump") or material.node_tree.nodes.new("ShaderNodeBump")
            bump.name = "WFRL.MicroBump"
            bump.inputs["Strength"].default_value = strength
            bump.inputs["Distance"].default_value = distance
            rough = material.node_tree.nodes.get("WFRL.PaintRoughness") or material.node_tree.nodes.new("ShaderNodeMapRange")
            rough.name = "WFRL.PaintRoughness"
            rough.inputs["To Min"].default_value = low
            rough.inputs["To Max"].default_value = high
            material.node_tree.links.new(tex.outputs["Fac"], rough.inputs["Value"])
            material.node_tree.links.new(rough.outputs[0], node.inputs["Roughness"])
            material.node_tree.links.new(tex.outputs["Fac"], bump.inputs["Height"])
            material.node_tree.links.new(bump.outputs["Normal"], node.inputs["Normal"])
        if name in SURFACES:
            # Do not fill contact shadows with emission on opaque machinery.
            node.inputs["Emission Strength"].default_value = 0.0
        elif name == "wake":
            node.inputs["Alpha"].default_value = 0.13
            node.inputs["Emission Color"].default_value = PALETTE["accent"]
            node.inputs["Emission Strength"].default_value = 0.16
            material.surface_render_method = "DITHERED"
        elif name in {"accent", "grid"}:
            node.inputs["Emission Color"].default_value = PALETTE[name]
            node.inputs["Emission Strength"].default_value = 0.22
    return material


def refresh_turbine_surfaces(objects):
    """Rebind only named WFRL fittings, including models saved by older builds."""
    import bpy
    for name in SURFACES:
        if bpy.data.materials.get('WFRL.' + name):
            get_material(name)
    metal_parts = ('MainShaft', 'YawBearing', 'YawPedestal', 'RootFlange',
                   'Vent.Louvre', 'ServiceDoor.Handle', 'ServiceDoor.Hinge', 'AccessRail.')
    rubber_parts = ('PitchSeal', 'YawSeal', 'YawSkirt', 'RoofGasket')
    for obj in objects:
        if obj.type != 'MESH' or not obj.name.startswith('WFRL.Turbine.'):
            continue
        if '.ClearanceRadar' in obj.name:
            continue  # Radar material slots are maintained by ensure_radar.
        kind = ('metal' if any(part in obj.name for part in metal_parts) else
                'rubber' if any(part in obj.name for part in rubber_parts) else None)
        if kind and obj.data.materials:
            obj.data.materials[0] = get_material(kind)


def wake_volume_material():
    """Soft radial and axial falloff in generated coordinates, without a shell."""
    import bpy
    material = bpy.data.materials.get('WFRL.WakeVolume') or bpy.data.materials.new('WFRL.WakeVolume')
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    output = nodes.new('ShaderNodeOutputMaterial')
    volume = nodes.new('ShaderNodeVolumePrincipled')
    volume.inputs['Color'].default_value = (.10, .58, .72, 1)
    volume.inputs['Anisotropy'].default_value = .15
    links.new(volume.outputs['Volume'], output.inputs['Volume'])
    coord = nodes.new('ShaderNodeTexCoord')
    split = nodes.new('ShaderNodeSeparateXYZ')
    links.new(coord.outputs['Generated'], split.inputs[0])
    def math_node(op, a, b=0):
        n = nodes.new('ShaderNodeMath'); n.operation = op
        for index, value in enumerate((a,b)):
            if isinstance(value, (float,int)): n.inputs[index].default_value = value
            else: links.new(value, n.inputs[index])
        return n.outputs[0]
    x,y,z = split.outputs['X'], split.outputs['Y'], split.outputs['Z']
    yy = math_node('SUBTRACT', y, .5); zz = math_node('SUBTRACT', z, .5)
    radial = math_node('SQRT', math_node('ADD', math_node('MULTIPLY', yy, yy), math_node('MULTIPLY', zz, zz)))
    radius = math_node('ADD', .30, math_node('MULTIPLY', x, .19))
    falloff = math_node('MAXIMUM', math_node('SUBTRACT', 1., math_node('DIVIDE', radial, radius)), 0.)
    falloff = math_node('POWER', falloff, 1.4)
    axial = math_node('MULTIPLY', math_node('MINIMUM', math_node('MULTIPLY', x, 14.), 1.), math_node('POWER', math_node('SUBTRACT', 1., x), .8))
    noise = nodes.new('ShaderNodeTexNoise'); noise.inputs['Scale'].default_value = 5.; noise.inputs['Detail'].default_value = 2.
    links.new(coord.outputs['Generated'], noise.inputs['Vector'])
    density = math_node('MULTIPLY', math_node('MULTIPLY', falloff, axial), math_node('MULTIPLY', noise.outputs['Fac'], .026))
    links.new(density, volume.inputs['Density'])
    return material


def flow_material(material, *, pulse=False, cinematic=False):
    """Hub-local downstream alpha falloff, shared by rings, traces and pulses."""
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    output = nodes.new('ShaderNodeOutputMaterial')
    emission = nodes.new('ShaderNodeEmission')
    emission.inputs['Color'].default_value = PALETTE['cinematic_line'] if cinematic else ((.22, 1.0, .045, 1) if pulse else (.025, .75, .006, 1))
    emission.inputs['Strength'].default_value = .28 if cinematic else (4.0 if pulse else 1.1)
    transparent = nodes.new('ShaderNodeBsdfTransparent')
    coord = nodes.new('ShaderNodeTexCoord')
    split = nodes.new('ShaderNodeSeparateXYZ')
    links.new(coord.outputs['Object'], split.inputs[0])
    fade = nodes.new('ShaderNodeMapRange')
    fade.name = 'WFRL.DownstreamFade'
    fade.clamp = True
    fade.inputs['From Min'].default_value = 240
    fade.inputs['From Max'].default_value = 457
    fade.inputs['To Min'].default_value = .72 if cinematic else (1.0 if pulse else .55)
    fade.inputs['To Max'].default_value = 0
    links.new(split.outputs['X'],fade.inputs['Value'])
    inlet = nodes.new('ShaderNodeMapRange'); inlet.clamp=True
    inlet.inputs['From Min'].default_value=12; inlet.inputs['From Max'].default_value=24
    links.new(split.outputs['X'],inlet.inputs['Value'])
    weight=nodes.new('ShaderNodeMath');weight.operation='MULTIPLY'
    links.new(fade.outputs[0],weight.inputs[0]);links.new(inlet.outputs[0],weight.inputs[1])
    mix=nodes.new('ShaderNodeMixShader')
    links.new(weight.outputs[0],mix.inputs[0]);links.new(transparent.outputs[0],mix.inputs[1]);links.new(emission.outputs[0],mix.inputs[2])
    links.new(mix.outputs[0],output.inputs['Surface'])
    material.surface_render_method='DITHERED'
    return material


# Distances from the actual mesh tip, metres. Paint wraps both surfaces and
# the leading/trailing edges; it never adds protruding geometry or changes
# the aerodynamic/source mesh. White gaps and the white terminal cap remain.
BLADE_TIP_BANDS = ((.25, 1.0), (1.7, 2.45), (3.15, 3.9))


def ensure_blade_tip_markings(objects):
    """Paint three tip bands on new and saved owned blade meshes, once per mesh."""
    import bpy
    material = bpy.data.materials.get('WFRL.BladeTipRed')
    if material is None:
        material = bpy.data.materials.new('WFRL.BladeTipRed')
        material.use_nodes = True
        material.diffuse_color = (.8, .006, .012, 1)
        node = material.node_tree.nodes.get('Principled BSDF')
        node.inputs['Base Color'].default_value = material.diffuse_color
        node.inputs['Roughness'].default_value = .36
        node.inputs['Emission Color'].default_value = (.8, .002, .004, 1)
        node.inputs['Emission Strength'].default_value = .7
        material['provenance'] = 'Illustrative night-visible tip paint; not a physical light specification'
    for obj in objects:
        if (obj.type != 'MESH' or not obj.name.startswith('WFRL.Turbine.')
                or obj.name.rsplit('.', 1)[-1] not in {'Blade1', 'Blade2', 'Blade3'}):
            continue
        mesh = obj.data
        if mesh.get('wfrl_tip_marking_revision', 0) >= 1:
            continue
        if not mesh.vertices:
            continue
        tip = max(v.co.z for v in mesh.vertices)
        slot = mesh.materials.find(material.name)
        if slot < 0:
            mesh.materials.append(material)
            slot = len(mesh.materials) - 1
        for face in mesh.polygons:
            distance = tip - sum(mesh.vertices[i].co.z for i in face.vertices) / len(face.vertices)
            if any(lo <= distance <= hi for lo, hi in BLADE_TIP_BANDS):
                face.material_index = slot
        mesh['wfrl_tip_marking_revision'] = 1
        mesh['wfrl_tip_marking_note'] = 'Three red bands on the original mesh; white cap; cosmetic only'
