"""Render and audit the NREL nacelle camera against saved flexible motion.

Run in Blender with --python this_file -- --output NEW_DIR. Installed mode
uses WFRL_ADDON_MODULE and WFRL_EXPECTED_ADDON_ROOT without source fallback.
The audit preserves turbine meshes and separates frustum coverage from finite
first-hit samples. Neither establishes field camera calibration.
"""
from pathlib import Path
import argparse
import hashlib
import importlib
import json
import math
import os
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(args):
    import bpy
    import numpy as np
    from mathutils import Vector
    module = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
    if module == 'wfrl_blender':
        sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
    addon = importlib.import_module(module)
    package_root = Path(addon.__file__).resolve().parent
    expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
    if expected and package_root != Path(expected).resolve():
        raise RuntimeError(f'Unexpected addon root: {package_root}')
    farm_flex = importlib.import_module(module + '.farm_flex')
    cameras = importlib.import_module(module + '.cameras')
    visibility = importlib.import_module(module + '.nacelle_visibility')
    clearance_replay = importlib.import_module(module + '.clearance_replay')
    args.output = args.output.expanduser().resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    addon.register()
    addon.load_demo_scene(camera_rig=False)
    scene = bpy.context.scene
    scene.wfrl_deflection_visible = False
    scene.wfrl_flex_show_tip_trails = False
    bpy.ops.wfrl.farm_flex_view(turbine='T1', angle='NACELLE')
    camera = scene.camera
    cameras.reset_nacelle_gimbal(camera)
    cameras.apply_nacelle_output(scene, camera)
    active = farm_flex.active_for(scene)
    active.export_turbines = {0}
    blade = scene.objects['WFRL.Turbine.T1.Blade1']
    rest = next(row[1] for row in active.blades if row[0] == blade)
    angles = np.degrees(np.unwrap(np.radians(active.poses[:, 0, 1])))
    target = 180 + 360 * math.ceil((angles[0] - 180) / 360)
    deltas = np.linspace(-30, 30, args.frames)
    times = np.interp(target + deltas, angles, active.times)
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.light = 'STUDIO'
    scene.display.shading.color_type = 'MATERIAL'
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.display.shading.show_specular_highlight = True
    scene.render.resolution_percentage = args.scale
    scene.render.image_settings.file_format = 'PNG'
    scene.render.film_transparent = False
    scene.view_settings.view_transform = 'Standard'
    native_projection = camera.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),
        x=scene.render.resolution_x, y=scene.render.resolution_y,
        scale_x=scene.render.pixel_aspect_x, scale_y=scene.render.pixel_aspect_y)
    effective_fov = [math.degrees(2 * math.atan(1 / native_projection[i][i])) for i in (0, 1)]
    if max(abs(effective_fov[i] - value) for i, value in enumerate((59., 40.))) > 1e-5:
        raise RuntimeError(f'Native camera gate differs from the nominal intrinsics: {effective_fov}')
    frames = args.output / 'frames'
    frames.mkdir()
    audit_rows = []
    manifest = {
        'schema': 'wfrl.nrel-nacelle-camera-check.v1',
        'module': module, 'addon_path': str(package_root),
        'blender': bpy.app.version_string, 'binary': bpy.app.binary_path,
        'build_info': json.loads((package_root / 'build-info.json').read_text())
            if (package_root / 'build-info.json').is_file() else None,
        'module_sha256': {name: digest(package_root / name) for name in
                          ('cameras.py', 'nacelle_camera.py', 'nacelle_visibility.py',
                           'farm_flex.py', 'turbine_geometry.py')},
        'script_sha256': digest(__file__),
        'source_package': str(farm_flex.default_package()),
        'source_manifest_sha256': digest(farm_flex.default_package() / 'manifest.json'),
        'physics_rerun': False, 'geometry_modified': False,
        'render_engine': scene.render.engine,
        'render_scope': 'actual Blender material-color camera render; no photometric field calibration',
        'lens_mm': camera.data.lens, 'hfov_deg': 59., 'vfov_deg': 40.,
        'effective_native_fov_deg': effective_fov,
        'sensor_mm': [camera.data.sensor_width, camera.data.sensor_height],
        'resolution': [scene.render.resolution_x, scene.render.resolution_y],
        'resolution_percentage': args.scale,
        'pixel_aspect': [scene.render.pixel_aspect_x, scene.render.pixel_aspect_y],
        'mount_offset_from_mechanical_hub_yaw_local_m': [1.5, 0., -1.5],
        'mount_yaw_local_m': list(camera.location),
        'optical_axis_yaw_local': list(camera.rotation_euler.to_matrix() @ Vector((0, 0, -1))),
        'optical_roll_deg': float(camera.get('gimbal_roll', 0)),
        'surface_scope': 'B1 lateral surface; constant-span root closure is retained as a scene occluder',
        'span_bands_m': .5,
        'visibility_scope': 'triangle/frustum clipping plus finite first-hit surface samples; no connectivity proof',
        'frames': audit_rows,
    }
    inspection_indices = set(np.linspace(0, args.frames - 1, 7).round().astype(int))
    for index, (delta, at) in enumerate(zip(deltas, times)):
        frame_value = scene.frame_start + (float(at) - float(active.times[0])) * 60
        scene.frame_set(math.floor(frame_value), subframe=frame_value % 1)
        active.update(scene, sim_time_s=float(at), record_telemetry=False, update_comparison=False)
        bpy.context.view_layer.update()
        deps = bpy.context.evaluated_depsgraph_get()
        world = camera.evaluated_get(deps).matrix_world
        eye = world.translation
        basis = world.to_3x3()
        row = {'index': index, 'delta_from_down_deg': float(delta), 'time_s': float(at),
               'frame': scene.frame_current, 'subframe': scene.frame_subframe,
               'T_world_from_camera': [list(r) for r in world],
               'render': f'frames/{index:04d}.png'}
        if index in inspection_indices:
            evaluated = blade.evaluated_get(deps)
            mesh = evaluated.to_mesh()
            try:
                if len(mesh.vertices) != len(rest):
                    raise RuntimeError('Evaluated blade topology differs from saved rest-span identity')
                mesh.calc_loop_triangles()
                points = np.array([evaluated.matrix_world @ v.co for v in mesh.vertices])
                faces = np.array([tuple(t.vertices) for t in mesh.loop_triangles])
                spans = np.asarray(rest)[:, 2] - 1.5
                cam = visibility.CameraFrustum(
                    eye=tuple(eye), right=tuple(basis.col[0]), up=tuple(basis.col[1]),
                    forward=tuple(-basis.col[2]), hfov=59., vfov=40.,
                    near=camera.data.clip_start, far=camera.data.clip_end)
                clipped = visibility.clip_surface(points, faces, spans, cam)
                used = np.unique(faces)
                span_bounds = [float(spans[used].min()), float(spans[used].max())]
                bands = visibility.sample_span_bands(clipped, np.linspace(*span_bounds, 124))
                blockers = {}

                def visible(sample):
                    p = Vector(sample.point)
                    direction = p - eye
                    distance = direction.length
                    hit, point, normal, face, obj, matrix = scene.ray_cast(
                        deps, eye, direction.normalized(), distance=distance + .002)
                    matched = bool(hit and obj.original == blade and (point - p).length < .002)
                    passed = matched and normal.dot(direction) < 0
                    if not passed:
                        key = 'B1_INTERIOR_SIDE' if matched else (obj.name if hit else 'NO_HIT')
                        blockers[key] = blockers.get(key, 0) + 1
                    return passed

                report = visibility.evaluate_visibility(bands, visible)
                row['surface'] = {
                    'mesh_span_bounds_m': span_bounds,
                    'full_outline_in_frame': bool(clipped.full_outline_in_frame),
                    'lateral_span_intervals_m': clipped.lateral_span_intervals,
                    'all_bands_partly_in_frustum': report.all_bands_partly_in_frustum,
                    'all_span_in_frustum': report.all_span_in_frustum,
                    'all_bands_sampled_visible': report.all_bands_sampled_visible,
                    'visible_band_count': sum(item.visible_count > 0 for item in report.bands),
                    'sample_count': sum(item.sample_count for item in report.bands),
                    'visible_sample_count': sum(item.visible_count for item in report.bands),
                    'bands': [{'span_m': [item.band.lower, item.band.upper],
                               'coverage_fraction': item.band.coverage_fraction,
                               'sample_count': item.sample_count,
                               'visible_sample_count': item.visible_count}
                              for item in report.bands],
                    'blockers': blockers,
                }
                hits = {}
                distances = []
                for u in np.linspace(-1, 1, 31):
                    for v in np.linspace(-1, 1, 21):
                        direction = basis @ Vector((u * math.tan(math.radians(29.5)),
                                                    v * math.tan(math.radians(20)), -1))
                        hit, p, normal, face, obj, matrix = scene.ray_cast(
                            deps, eye, direction.normalized(), distance=200)
                        key = obj.name if hit else 'NO_HIT'
                        hits[key] = hits.get(key, 0) + 1
                        if hit:
                            distances.append(float((p - eye).length))
                        if abs(u) < 1e-9 and abs(v) < 1e-9:
                            row['center_first_hit'] = {'object': key, 'face': face,
                                'distance_m': float((p - eye).length) if hit else None,
                                'world_m': list(p) if hit else None}
                row['first_hits'] = hits
                row['first_hit_distance_range_m'] = [min(distances), max(distances)] if distances else None
                local = evaluated.matrix_world.inverted() @ eye
                row['camera_in_blade_local_m'] = list(local)
            finally:
                evaluated.to_mesh_clear()
        before_mesh = np.array([v.co[:] for v in blade.data.vertices])
        before_pose = np.array(blade.matrix_world)
        before_camera = np.array(camera.matrix_world)
        scene.render.filepath = str(frames / f'{index:04d}.png')
        bpy.ops.render.render(write_still=True)
        bpy.context.view_layer.update()
        row['render_time_s'] = float(clearance_replay.sample(scene)['time_s'])
        row['render_mesh_change_max_m'] = float(np.max(np.abs(
            np.array([v.co[:] for v in blade.data.vertices]) - before_mesh)))
        row['render_pose_change_max'] = float(np.max(np.abs(np.array(blade.matrix_world) - before_pose)))
        row['render_camera_change_max'] = float(np.max(np.abs(np.array(camera.matrix_world) - before_camera)))
        if (abs(row['render_time_s'] - at) > 1e-6
                or row['render_mesh_change_max_m'] > 2e-5
                or row['render_pose_change_max'] > 2e-5
                or row['render_camera_change_max'] > 2e-5):
            raise RuntimeError(f'Render evaluation changed the audited replay state: {row}')
        row['render_sha256'] = digest(scene.render.filepath)
        audit_rows.append(row)
        (args.output / 'check.json').write_text(json.dumps(manifest, indent=2, allow_nan=False))
        print('NREL_NACELLE_FRAME', index, float(delta), float(at), flush=True)
    at = float(np.interp(target, angles, active.times))
    active.update(scene, sim_time_s=at, record_telemetry=False, update_comparison=False)
    bpy.context.view_layer.update()
    frame_value = scene.frame_start + (at - float(active.times[0])) * 60
    scene.frame_set(math.floor(frame_value), subframe=frame_value % 1)
    scene.wfrl_farm_panel_page = 'TOOLS'
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces.active.use_local_camera = False
                area.spaces.active.region_3d.view_perspective = 'CAMERA'
                cameras.fill_camera_view(area, scene)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / 'nrel-nacelle-baseline.blend'))
    manifest['scene_sha256'] = digest(args.output / 'nrel-nacelle-baseline.blend')
    manifest['status'] = 'RENDERED_AND_AUDITED'
    manifest['photo_match'] = 'REQUIRES_VISUAL_REVIEW'
    (args.output / 'check.json').write_text(json.dumps(manifest, indent=2, allow_nan=False))
    print('NREL_NACELLE_CHECK_COMPLETE', str(args.output), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--frames', type=int, default=31)
    parser.add_argument('--scale', type=int, default=50)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    if args.frames < 7 or not 1 <= args.scale <= 100:
        parser.error('frames >= 7 and 1 <= scale <= 100 required')
    import bpy
    if bpy.app.background:
        run(args)
    else:
        def capture_window():
            try:
                run(args)
            except Exception:
                print(traceback.format_exc(), flush=True)
                raise
            return None
        bpy.app.timers.register(capture_window, first_interval=1.)
