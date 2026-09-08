"""Lightweight, explicitly non-physical atmosphere presets."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping


@dataclass(frozen=True)
class AtmospherePreset:
    name: str
    world_color: tuple[float, float, float, float]
    world_strength: float
    sun_color: tuple[float, float, float]
    sun_energy: float
    fog_color: tuple[float, float, float, float]
    fog_density: float
    horizon_color: tuple[float, float, float]


PRESETS: Mapping[str, AtmospherePreset] = {
    "clear": AtmospherePreset("clear", (0.12, 0.28, 0.55, 1.0), 0.5,
                               (0.95, 0.98, 1.0), 3.0, (0.55, 0.70, 0.84, 1.0), 0.00005, (0.16, 0.29, 0.42)),
    "overcast": AtmospherePreset("overcast", (0.32, 0.38, 0.44, 1.0), 0.72,
                                  (0.72, 0.78, 0.82), 1.5, (0.47, 0.52, 0.56, 1.0), 0.00022, (0.28, 0.32, 0.35)),
    "dusk": AtmospherePreset("dusk", (0.42, 0.10, 0.045, 1.0), 0.68,
                             (1.0, 0.46, 0.22), 2.0, (0.45, 0.17, 0.12, 1.0), 0.00012, (0.35, 0.10, 0.08)),
}


def get_preset(name: str) -> AtmospherePreset:
    key = str(name).lower()
    if key not in PRESETS:
        raise ValueError(f"unknown atmosphere preset: {name}")
    return PRESETS[key]


def apply_preset(scene, name: str = "clear", *, enabled: bool = True, quality: str = "realtime") -> AtmospherePreset:
    """Apply a preset to Blender scene nodes when Blender is available."""
    preset = get_preset(name)
    if quality not in {"realtime", "render"}:
        raise ValueError("quality must be realtime or render")
    scene["wfrl_atmosphere_preset"] = preset.name
    scene["wfrl_atmosphere_enabled"] = bool(enabled)
    scene["wfrl_atmosphere_quality"] = quality
    scene["wfrl_atmosphere_fog_density"] = preset.fog_density if enabled else 0.0
    world = getattr(scene, "world", None)
    if world is not None and getattr(world, "use_nodes", False):
        nodes = world.node_tree.nodes
        links = world.node_tree.links
        background = next((node for node in nodes if node.type == "BACKGROUND"), None)
        if background is not None:
            sky = nodes.get("WFRL.Atmosphere.Sky")
            if sky is None:
                sky = nodes.new("ShaderNodeTexSky")
                sky.name = "WFRL.Atmosphere.Sky"
            try:
                sky.sky_type = "NISHITA"
                sky.sun_elevation = 0.30 if preset.name == "clear" else 0.16
                sky.sun_rotation = 2.35
                sky.altitude = 0.2
                sky.air_density = 1.0
                sky.dust_density = 0.55 if preset.name == "clear" else 2.5
                sky.ozone_density = 0.35
            except (AttributeError, TypeError):
                pass
            for link in list(links):
                if link.to_node == background and link.to_socket == background.inputs["Color"]:
                    links.remove(link)
            if preset.name == "clear":
                from pathlib import Path
                import bpy
                env = nodes.get('WFRL.Atmosphere.HDRI') or nodes.new('ShaderNodeTexEnvironment')
                env.name = 'WFRL.Atmosphere.HDRI'
                env.image = bpy.data.images.load(str(Path(__file__).parent / 'assets/landscape/kloofendal_48d_partly_cloudy_puresky_2k.hdr'), check_existing=True)
                links.new(env.outputs['Color'], background.inputs['Color'])
            else:
                background.inputs["Color"].default_value = preset.world_color
            background.inputs["Strength"].default_value = preset.world_strength if enabled else 0.8
        output = next((node for node in nodes if node.type == "OUTPUT_WORLD"), None)
        if output is not None:
            volume = nodes.get("WFRL.Atmosphere.Volume")
            if volume is None:
                volume = nodes.new("ShaderNodeVolumePrincipled")
                volume.name = "WFRL.Atmosphere.Volume"
                volume.label = "WFRL visual atmosphere (not weather simulation)"
            volume.inputs["Color"].default_value = preset.fog_color
            # A world volume extends to infinity and can swallow the sky in
            # real-time preview.  Keep preview atmospheric color in the world
            # background; only explicit render quality enables a very light
            # depth cue.
            volume.inputs["Density"].default_value = (preset.fog_density * 0.02 if enabled and quality == "render" else 0.0)
            for link in list(links):
                if link.to_node == output and link.to_socket == output.inputs.get("Volume"):
                    links.remove(link)
            if enabled and quality == "render":
                links.new(volume.outputs["Volume"], output.inputs["Volume"])
    if hasattr(scene, "view_settings"):
        scene.view_settings.look = "AgX - Medium High Contrast"
    # Eevee's world volume is intentionally omitted: a compositor/world node
    # setup is version-sensitive, while the metadata and switches remain stable.
    return preset


def set_enabled(scene, enabled: bool) -> None:
    scene["wfrl_atmosphere_enabled"] = bool(enabled)


def is_enabled(scene) -> bool:
    return bool(scene.get("wfrl_atmosphere_enabled", True))


def build_sky_features(collection, *, center=(500.0, 30.0, 68.0), camera_location=None,
                       camera_target=None, ortho_width=1089.4, aspect=16.0 / 9.0):
    """Build reusable sun/cloud presentation geometry in a WFRL collection.

    These are deliberately separate from the simulation terrain and carry
    ``SYNTH`` metadata.  They provide a legible sky in Eevee without claiming
    to be a meteorological cloud or solar-radiation model.
    """
    import bpy
    from mathutils import Vector

    existing = [obj for obj in collection.objects if obj.name.startswith("WFRL.Atmosphere.")]
    for obj in existing:
        bpy.data.objects.remove(obj, do_unlink=True)
    sky_material = bpy.data.materials.get("WFRL.Sky.Cloud") or bpy.data.materials.new("WFRL.Sky.Cloud")
    sky_material.use_nodes = True
    cloud_shader = next((node for node in sky_material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
    if cloud_shader is not None:
        cloud_shader.inputs["Base Color"].default_value = (0.92, 0.96, 1.0, 1.0)
        cloud_shader.inputs["Roughness"].default_value = 0.85
        cloud_shader.inputs["Emission Color"].default_value = (0.18, 0.22, 0.28, 1.0)
        cloud_shader.inputs["Emission Strength"].default_value = 0.15
    target = Vector(camera_target or center)
    if camera_location is None:
        camera_location = target + Vector((0.0, -1700.0, 692.0))
    forward = (target - Vector(camera_location)).normalized()
    right = forward.cross(Vector((0.0, 0.0, 1.0))).normalized()
    up = right.cross(forward).normalized()
    # The world camera is orthographic.  Place presentation elements in its
    # screen plane so changes to turbine layout do not push them out of frame.
    screen_height = float(ortho_width) / max(float(aspect), 0.1)

    def screen_point(u: float, v: float, depth: float) -> Vector:
        return (target - forward * float(depth)
                + right * ((float(u) - 0.5) * float(ortho_width))
                + up * ((float(v) - 0.5) * screen_height))

    cloud_layout = (
        (0.20, 0.86, 118.0, 44.0, 980.0),
        (0.52, 0.91, 148.0, 50.0, 1120.0),
        (0.80, 0.84, 122.0, 44.0, 1180.0),
    )
    shadow_material = bpy.data.materials.get("WFRL.Sky.CloudShadow") or bpy.data.materials.new("WFRL.Sky.CloudShadow")
    shadow_material.use_nodes = True
    shadow_shader = next((node for node in shadow_material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
    if shadow_shader is not None:
        shadow_shader.inputs["Base Color"].default_value = (0.58, 0.67, 0.76, 1.0)
        shadow_shader.inputs["Roughness"].default_value = 0.96
        shadow_shader.inputs["Emission Color"].default_value = (0.05, 0.08, 0.12, 1.0)
        shadow_shader.inputs["Emission Strength"].default_value = 0.05
    for cloud_index, (u, v, width, height, depth) in enumerate(cloud_layout, start=1):
        cloud_center = screen_point(u, v, depth)
        for puff_index, (dx, dy, dz, sx, sy, sz) in enumerate((
            (-0.55, 0.08, 0.00, 0.55, 0.40, 0.45),
            (-0.20, -0.04, 0.22, 0.72, 0.52, 0.62),
            (0.18, 0.10, 0.10, 0.85, 0.58, 0.52),
            (0.50, -0.02, -0.02, 0.58, 0.38, 0.40),
        )):
            bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=1.0,
                                                   location=(cloud_center.x + dx * width,
                                                             cloud_center.y + dy * width,
                                                             cloud_center.z + dz * height))
            puff = bpy.context.object
            puff.name = f"WFRL.Atmosphere.Cloud{cloud_index:02d}.{puff_index:02d}"
            puff.scale = (width * sx, width * sy, height * sz)
            puff.data.materials.append(shadow_material if puff_index in {0, 3} else sky_material)
            puff["fidelity"] = "SYNTH"
            puff["provenance"] = "Presentation cloud bank; not weather simulation"
            for old in list(puff.users_collection):
                old.objects.unlink(puff)
            collection.objects.link(puff)

    return tuple(obj.name for obj in collection.objects if obj.name.startswith("WFRL.Atmosphere."))


def set_feature_visibility(scene, enabled: bool) -> None:
    """Toggle sun/cloud geometry without affecting simulation objects."""
    import bpy
    for obj in bpy.data.objects:
        if obj.name.startswith("WFRL.Atmosphere."):
            obj.hide_render = not enabled
            obj.hide_set(not enabled)
