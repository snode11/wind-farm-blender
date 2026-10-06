"""Bake saved reconstruction states into native, independently playable Blender data.

The saved forward-model mesh remains exact. Absolute shape keys and constant
native material animation hold each 10 Hz result for six frames on the MAPPO
60 Hz timeline. A green surface band requires both adjacent saved sections to be
constrained; orange otherwise. The tip cap uses its single saved tip section.
No frame callback, driver or executable Text block is needed for reconstruction
playback after saving and reopening a .blend.
"""
from __future__ import annotations

import time

import bpy
import numpy as np


GREEN = (0.15, 0.75, 0.25, 1.0)
ORANGE = (1.0, 0.55, 0.10, 1.0)
PREFIX = "SplitRecon"


def _curve(action, datablock, data_path, index, frames, values):
    curve = action.fcurve_ensure_for_datablock(
        datablock, data_path, index=index, group_name="Saved reconstruction"
    )
    curve.keyframe_points.add(len(frames))
    coordinates = np.column_stack((frames, values)).astype(np.float32)
    curve.keyframe_points.foreach_set("co", coordinates.reshape(-1))
    for point in curve.keyframe_points:
        point.interpolation = "CONSTANT"
    curve.update()
    return curve


def _action(datablock, name):
    action = bpy.data.actions.new(name)
    datablock.animation_data_create().action = action
    return action


def band_observed(sequence):
    """Conservative native surface colour: two constrained ends per span."""
    mask = sequence.observed.copy()
    mask[:, :, :-1] = sequence.observed[:, :, :-1] & sequence.observed[:, :, 1:]
    return mask


def timeline_index(frame, count=601):
    """Source index on this experiment's fixed 1..3601, 60 Hz timeline."""
    return min(max((int(frame) - 1) // 6, 0), count - 1)


def build(scene, sequence):
    """Return baked blades, native actions and evidence metadata for ``scene``.

    This function only creates the three reconstruction meshes, their materials
    and native animation. The caller owns scene timing, cameras, tower, viewports,
    saving and UI. Source JSON, source geometry and the reconstruction solver are
    not modified.
    """
    started = time.perf_counter()
    count = len(sequence.states)
    if count != 601 or not np.allclose(sequence.times, np.arange(count) / 10, atol=1e-9, rtol=0):
        raise ValueError("Expected this experiment's 601 saved states at 10 Hz")
    if not np.array_equal(sequence.source_frames, np.arange(count)):
        raise ValueError("Expected contiguous saved reconstruction source IDs 0..600")
    if any(obj.name.startswith(PREFIX + ".B") for obj in scene.objects):
        raise ValueError("SplitRecon blade meshes already exist in the target scene")

    frames = 1 + 6 * np.arange(count, dtype=np.int32)
    vertices, _ = sequence.geometry(0)
    faces = [tuple(face) for face in sequence.rotor.tpl.faces()]
    observed = band_observed(sequence)
    ring_count = sequence.rotor.cfg.n_ring
    blades, materials, geometry_actions, colour_actions, keys = [], [], [], [], []
    for blade in range(3):
        mesh = bpy.data.meshes.new(f"{PREFIX}.B{blade + 1}.Mesh")
        mesh.from_pydata(vertices[blade].reshape(-1, 3).tolist(), [], faces)
        for section in range(sequence.rotor.cfg.n_sections):
            material = bpy.data.materials.new(f"{PREFIX}.B{blade + 1}.Span{section:02d}")
            material.diffuse_color = GREEN if observed[0, blade, section] else ORANGE
            material.use_nodes = False
            mesh.materials.append(material)
            materials.append(material)
        for polygon in mesh.polygons:
            polygon.material_index = min(polygon.vertices) // ring_count
        mesh.update()
        obj = bpy.data.objects.new(f"{PREFIX}.B{blade + 1}", mesh)
        scene.collection.objects.link(obj)
        obj.hide_select = False
        obj["split_reconstruction_owned"] = True
        obj["split_reconstruction_blade"] = blade
        obj["split_reconstruction_source_sha256"] = sequence.source_sha256
        obj["split_reconstruction_sampling_hz"] = 10.0
        obj["split_reconstruction_timeline_hz"] = 60.0
        obj["split_reconstruction_colour_rule"] = "GREEN_IF_BOTH_ADJACENT_SECTIONS_CONSTRAINED; TIP_CAP_USES_TIP_SECTION"
        basis = obj.shape_key_add(name="Saved.000000", from_mix=False)
        mesh.shape_keys.use_relative = False
        basis.interpolation = "KEY_LINEAR"
        keys.append([basis])
        blades.append(obj)

    # Evaluate each saved state once, then retain its original mesh in the keys.
    for index in range(1, count):
        vertices, _ = sequence.geometry(index)
        for blade, obj in enumerate(blades):
            key = obj.shape_key_add(name=f"Saved.{index:06d}", from_mix=False)
            key.interpolation = "KEY_LINEAR"
            key.data.foreach_set("co", vertices[blade].astype(np.float32).reshape(-1))
            keys[blade].append(key)

    colour_curve_count = colour_key_count = section_changes = 0
    for blade, obj in enumerate(blades):
        shape_data = obj.data.shape_keys
        geometry_action = _action(shape_data, f"{PREFIX}.B{blade + 1}.Geometry")
        _curve(geometry_action, shape_data, "eval_time", 0, frames,
               [key.frame for key in keys[blade]])
        shape_data.eval_time = keys[blade][0].frame
        geometry_actions.append(geometry_action)

        for section in range(sequence.rotor.cfg.n_sections):
            mask = observed[:, blade, section]
            changes = np.flatnonzero(mask[1:] != mask[:-1]) + 1
            section_changes += len(changes)
            if not len(changes):
                continue
            indices = np.r_[0, changes]
            colour_frames = frames[indices]
            colour_values = np.where(mask[indices, None], GREEN, ORANGE)
            material = obj.data.materials[section]
            colour_action = _action(material, f"{PREFIX}.B{blade + 1}.Span{section:02d}.Evidence")
            colour_actions.append(colour_action)
            # Alpha is always 1.0; animate only RGB and only at actual changes.
            for channel in range(3):
                _curve(colour_action, material, "diffuse_color", channel,
                       colour_frames, colour_values[:, channel])
                colour_curve_count += 1
                colour_key_count += len(indices)

    return dict(
        blades=blades,
        objects=blades,
        materials=materials,
        geometry_actions=geometry_actions,
        colour_actions=colour_actions,
        source_frames=sequence.source_frames.tolist(),
        timeline_frames=frames.tolist(),
        frame_count=count,
        geometry_key_count=sum(len(items) for items in keys),
        colour_curve_count=colour_curve_count,
        colour_key_count=colour_key_count,
        section_transition_count=section_changes,
        sampling_hz=10.0,
        timeline_hz=60.0,
        interpolation="CONSTANT",
        colour_semantics="GREEN_IF_BOTH_ADJACENT_SECTIONS_CONSTRAINED; TIP_CAP_USES_TIP_SECTION",
        required_viewport_colour_type="MATERIAL",
        playback_kind="NATIVE_SAVED_RECONSTRUCTION_REPLAY",
        build_seconds=time.perf_counter() - started,
    )


def validate(scene, sequence, built, benchmark_frames=100):
    """Check exact native geometry, conservative band colours and state holds."""
    original_frame = scene.frame_current
    checks = []
    tested = [1, 6, 7, 103, 901, 3601, 103]
    observed = band_observed(sequence)
    try:
        for frame in tested:
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            index = timeline_index(frame, len(sequence.states))
            expected_vertices = sequence.geometry(index)[0]
            expected_colours = np.where(observed[index, ..., None], GREEN, ORANGE)
            depsgraph = bpy.context.evaluated_depsgraph_get()
            max_geometry_error = max_colour_error = 0.0
            for blade, obj in enumerate(built["blades"]):
                evaluated = obj.evaluated_get(depsgraph)
                actual = np.empty(len(evaluated.data.vertices) * 3, dtype=np.float32)
                evaluated.data.vertices.foreach_get("co", actual)
                error = float(np.max(np.abs(actual.reshape(expected_vertices[blade].shape)
                                            - expected_vertices[blade])))
                colours = np.asarray([list(material.diffuse_color) for material in obj.data.materials])
                colour_error = float(np.max(np.abs(colours - expected_colours[blade])))
                for polygon in obj.data.polygons:
                    if polygon.material_index != min(polygon.vertices) // sequence.rotor.cfg.n_ring:
                        raise AssertionError("Surface-band material mapping does not match the source mesh")
                if error > 1e-4 or colour_error > 1e-6:
                    raise AssertionError(f"Native reconstruction mismatch at frame {frame}, B{blade + 1}: {error}, {colour_error}")
                max_geometry_error = max(max_geometry_error, error)
                max_colour_error = max(max_colour_error, colour_error)
            checks.append(dict(timeline_frame=frame, source_index=index,
                               source_time_s=float(sequence.times[index]),
                               max_geometry_error_m=max_geometry_error,
                               max_colour_error=max_colour_error))
        benchmark = None
        if benchmark_frames:
            started = time.perf_counter()
            for frame in range(1, benchmark_frames + 1):
                scene.frame_set(frame)
                bpy.context.view_layer.update()
            elapsed = time.perf_counter() - started
            benchmark = dict(frames=benchmark_frames, seconds=elapsed,
                             native_frame_set_per_second=benchmark_frames / elapsed,
                             scope="BACKGROUND_DATA_EVALUATION_ONLY; NOT_VISIBLE_FPS")
        return dict(status="NATIVE_GEOMETRY_COLOURS_AND_CONSTANT_HOLDS_VERIFIED",
                    checks=checks, benchmark=benchmark,
                    colour_semantics="GREEN_IF_BOTH_ADJACENT_SECTIONS_CONSTRAINED; TIP_CAP_USES_TIP_SECTION")
    finally:
        scene.frame_set(original_frame)
