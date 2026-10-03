"""Visible-window, source-only NREL capture using the existing GPU transaction.

Run Blender with --factory-startup --python this_file -- --output DIR.
No physics execution, installed-extension mutation, or truth segmentation.
"""
from pathlib import Path
import argparse
import hashlib
import importlib
import json
import math
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]


def center_times(start, duration, fps):
    """N centre samples on [start,start+duration), avoiding inclusive-end extras."""
    if not all(math.isfinite(v) for v in (start, duration, fps)) or duration <= 0 or fps <= 0:
        raise ValueError('finite positive duration/fps required')
    n = round(duration * fps)
    if not math.isclose(n, duration * fps, abs_tol=1e-8):
        raise ValueError('duration*fps must be an integer')
    return [start + (i + .5) / fps for i in range(n)]


def matrix(value):
    return [[float(v) for v in row] for row in value]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def arguments(argv):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--start', type=float, default=117.)
    p.add_argument('--duration', type=float, default=2.)
    p.add_argument('--fps', type=float, default=20.)
    p.add_argument('--times', help='Comma-separated sparse RGB inspection times')
    p.add_argument('--package', type=Path)
    p.add_argument('--truth-time', type=float)
    p.add_argument('--truth-output', type=Path)
    p.add_argument('--truth-only', action='store_true', help='Scoring export after RGB t_ref selection; no new RGB capture')
    p.add_argument('--reference-capture', type=Path, help='Verify truth against the immutable RGB capture identity/state')
    p.add_argument('--keep-open', action='store_true')
    return p.parse_args(argv)


def run(args):
    import bpy
    import numpy as np
    from mathutils import Matrix, Vector
    if bpy.app.background:
        raise RuntimeError('Capture requires a visible VIEW_3D/GPU window')
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
    addon = importlib.import_module('wfrl_blender')
    capture = importlib.import_module('wfrl_blender.custom_camera_capture')
    core = importlib.import_module('wfrl_blender.custom_cameras')
    projection = importlib.import_module('wfrl_blender.camera_projection')
    addon.register()
    addon.load_demo_scene(str(args.package) if args.package else None)
    scene = bpy.context.scene
    scene.wfrl_flex_show_tip_trails = False
    scene.wfrl_deflection_visible = False
    times = ([float(t) for t in args.times.split(',')] if args.times
             else center_times(args.start, args.duration, args.fps))
    args.output.mkdir(parents=True, exist_ok=True)
    from wfrl_blender import farm_flex
    farm = farm_flex._ACTIVE
    blade = scene.objects['WFRL.Turbine.T1.Blade1']
    area = next(a for a in bpy.context.window.screen.areas if a.type == 'VIEW_3D')
    window = bpy.context.window
    region = next(r for r in area.regions if r.type == 'WINDOW')
    state = {'job': None, 'started': time.perf_counter(), 'projection_checks': []}

    def projected_checks():
        """Independent graphic clip -> pixel versus K/CV camera projection."""
        deps = bpy.context.evaluated_depsgraph_get()
        checks = []
        for cam in core.enabled_cameras(scene):
            params = core.parameters(cam)
            w, h = projection.resolution(params.fov, params.vfov, params.output_long_edge_px)
            world = cam.evaluated_get(deps).matrix_world
            view = world.inverted()
            pg = np.asarray(projection.projection(params.fov, params.vfov, params.clip_near_m, params.clip_far_m))
            k = np.asarray(projection.intrinsics(params.fov, params.vfov, w, h))
            cv = np.asarray(projection.cv_from_world(view))
            errors = []
            for local in ((0, 0, -10), (.75, -.4, -8), (-1, .8, -12)):
                point = np.array([*(world @ Vector(local)), 1.])
                clip = pg @ np.asarray(view) @ point
                graphic = np.array([(clip[0]/clip[3]+1)*w/2-.5, (1-clip[1]/clip[3])*h/2-.5])
                q = k @ (cv @ point)[:3]
                vision = q[:2]/q[2]
                errors.append(float(np.linalg.norm(graphic-vision)))
            assert max(errors) < .02, errors
            checks.append({'camera_id':f"C{cam['wfrl_custom_slot']}", 'max_pixel_error':max(errors),
                           'time_s':float(scene['wfrl_clearance_time_s'])})
        return checks

    class ResearchCapture(capture.Capture):
        def __init__(self, context, directory, sample_times):
            self.restoring = False
            self.motion_records = []
            super().__init__(context, directory, sample_times)

        def evaluate(self, frame, subframe, sim_time):
            super().evaluate(frame, subframe, sim_time)
            if not self.restoring and self.index < len(self.times):
                deps = bpy.context.evaluated_depsgraph_get()
                pose = matrix(blade.evaluated_get(deps).matrix_world)
                root_pose = matrix(blade.evaluated_get(deps).matrix_world @ Matrix.Translation((0.,0.,1.5)))
                motion = farm.readers['T1'].at(sim_time)['motion']
                record = {'sample_index':self.index,'sim_time_s':sim_time,
                          'T_world_from_blade_local':pose,
                          'T_world_from_blade_root':root_pose,
                          'azimuth_deg':motion['azimuth_deg'], 'yaw_deg':motion['yaw_deg'],
                          'pitch_b1_deg':motion['pitch_deg'][0],
                          'source':'simulation ideal rigid running observation',
                          'coordinate_frame':'blade_root_local',
                          'local_coordinates':'x axial/thickness, y chordwise, z radial/span; origin hub-local z=1.5m',
                          'includes_deformation':False}
                self.motion_records.append(record)
                with (self.path/'motion_internal.jsonl').open('a', encoding='utf-8') as f:
                    f.write(json.dumps(record, allow_nan=False)+'\n')
                if self.index in (0, len(self.times)//2, len(self.times)-1):
                    state['projection_checks'].extend(projected_checks())

        def finish(self, *positional, **keywords):
            self.restoring = True
            try:
                return super().finish(*positional, **keywords)
            finally:
                self.restoring = False

    def export_truth(at, target):
        """Scoring only; never make this directory an algorithm input."""
        target.mkdir(parents=True, exist_ok=True)
        source_identity = None
        reference_records = None
        if args.reference_capture:
            source_identity = json.loads((args.reference_capture/'runtime_identity.json').read_text())
            for path, expected in source_identity['module_sources'].items():
                if sha256(path) != expected:
                    raise RuntimeError('Source module changed since RGB capture: '+path)
            if sha256(Path(scene['wfrl_farm_flex_path'])/'manifest.json') != source_identity['source_manifest_sha256']:
                raise RuntimeError('Source package changed since RGB capture')
            reference_records = [json.loads(line) for line in (args.reference_capture/'frames.jsonl').read_text().splitlines()]
            reference_records = [r for r in reference_records if math.isclose(r['time_s'],at,abs_tol=1e-9)]
            if len(reference_records) != 3:
                raise RuntimeError('t_ref must match one complete RGB three-camera group')
        with capture.readonly_frame_handlers():
            mapped = scene.frame_start+(at-farm.readers['T1'].start_s)*float(scene.get('wfrl_clearance_timebase_fps',60.))
            scene.frame_set(math.floor(mapped),subframe=mapped-math.floor(mapped))
            farm.update(scene, sim_time_s=at, record_telemetry=False, update_comparison=False)
            bpy.context.view_layer.update()
        from wfrl_blender import custom_camera_preview as preview
        state_hash = preview.simulation_state_hash(scene,bpy.context.evaluated_depsgraph_get())
        if reference_records and any(r['simulation_state_hash'] != state_hash for r in reference_records):
            raise RuntimeError('Evaluated video scene state differs at frozen t_ref')
        evaluated = blade.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            verts = [(float(v.co.x),float(v.co.y),float(v.co.z)-1.5) for v in mesh.vertices]
            faces = [tuple(t.vertices) for t in mesh.loop_triangles]
            used = sorted(set(v for face in faces for v in face))
            remap = {v:i for i,v in enumerate(used)}
            lines = ['ply','format ascii 1.0',f'element vertex {len(used)}',
                     'property float x','property float y','property float z',
                     f'element face {len(faces)}','property list uchar int vertex_indices','end_header']
            lines.extend(' '.join(f'{x:.9g}' for x in verts[v]) for v in used)
            lines.extend('3 '+' '.join(str(remap[v]) for v in face) for face in faces)
            (target/'T1_B1_truth.ply').write_text('\n'.join(lines)+'\n')
            capture.write_json(target/'truth_manifest.json', {
                'schema':'wfrl.evaluation-surface.v1','target':'T1/B1','t_ref_sim_time_s':at,
                'coordinate_frame':'blade_root_local','units':'m','span_axis':'z','span_bounds_m':[0.,61.5],
                'coordinates':'x axial/thickness, y chordwise, z radial/span; origin hub-local z=1.5m',
                'T_world_from_blade_root':matrix(evaluated.matrix_world @ Matrix.Translation((0.,0.,1.5))),
                'mesh_source':'actual evaluated mesh of video scene',
                'vertices':len(used),'triangles':len(faces),
                'surface_scope':'entire_evaluated_blade_surface',
                'includes':'full SourceLoft surface excluding loose diagnostic vertex',
                'source_package':str(scene['wfrl_farm_flex_path']),
                'source_manifest_sha256':sha256(Path(scene['wfrl_farm_flex_path'])/'manifest.json'),
                'module_file':addon.__file__,
                'module_sources':source_identity['module_sources'] if source_identity else None,
                'blender':bpy.app.version_string,
                'camera_layout_hash':core.layout_hash(core.layout_dict(scene)),
                'render_profile':preview.render_profile(scene),
                'simulation_state_hash':state_hash,
                'reference_capture':str(args.reference_capture) if args.reference_capture else None,
                'reference_capture_state_verified':bool(reference_records),
                'algorithm_input':False,'file_sha256':sha256(target/'T1_B1_truth.ply')})
        finally:
            evaluated.to_mesh_clear()

    def tick():
        try:
            with bpy.context.temp_override(window=window, area=area, region=region):
                if args.truth_only:
                    if args.truth_time is None or args.truth_output is None:
                        raise ValueError('--truth-only requires --truth-time and --truth-output')
                    export_truth(args.truth_time,args.truth_output)
                    capture.write_json(args.output/'truth_export_result.json', {
                        'status':'complete','t_ref_sim_time_s':args.truth_time,
                        'truth_output':str(args.truth_output),'module_file':addon.__file__,
                        'elapsed_s':time.perf_counter()-state['started']})
                    print('NREL_TRUTH_EXPORT_COMPLETE',args.truth_time,str(args.truth_output),flush=True)
                    if not args.keep_open:
                        bpy.ops.wm.quit_blender()
                    return None
                if state['job'] is None:
                    state['job'] = ResearchCapture(bpy.context, args.output, times)
                    job = state['job']
                    modules = (addon, capture, core, projection, farm_flex)
                    source_paths = [Path(m.__file__) for m in modules]
                    package_path = Path(scene['wfrl_farm_flex_path'])
                    manifest_path = package_path/'manifest.json'
                    capture.write_json(job.path/'runtime_identity.json', {
                        'blender':bpy.app.version_string,'blender_binary':bpy.app.binary_path,
                        'module':'wfrl_blender','module_file':addon.__file__,
                        'module_sources':{str(p):sha256(p) for p in source_paths},
                        'capture_adapter_sha256':sha256(__file__),
                        'source_package':str(package_path),'source_manifest_sha256':sha256(manifest_path),
                        'layout_sha256':sha256(Path(addon.__file__).parent/'assets/cameras/t1-three-camera-default.json'),
                        'times_policy':'explicit sparse RGB' if args.times else 'shared frame centres',
                        'fps':args.fps,'start_s':args.start,'duration_s':args.duration,
                        'physics_rerun':False,'installed_extension_modified':False})
                    capture.write_json(args.output/'capture_pointer.json', {'capture_path':str(job.path)})
                    print('NREL_CAPTURE_BEGIN',str(job.path),len(times),flush=True)
                job = state['job']
                finished = job.step(bpy.context)
                print('NREL_CAPTURE_PROGRESS',job.index,len(times),flush=True)
                if not finished:
                    return .05
                summary = {'status':job.manifest['status'],'error':job.error,
                           'groups':job.index,'images':job.index*len(job.cameras),
                           'capture_path':str(job.path),'elapsed_s':time.perf_counter()-state['started'],
                           'projection_checks':state['projection_checks']}
                capture.write_json(args.output/'capture_result.json',summary)
                if job.manifest['status'] != 'complete':
                    raise RuntimeError(job.error)
                if args.truth_time is not None:
                    if args.truth_output is None:
                        raise ValueError('--truth-time requires explicit --truth-output')
                    export_truth(args.truth_time,args.truth_output)
                print('NREL_CAPTURE_COMPLETE',json.dumps(summary),flush=True)
                if not args.keep_open:
                    bpy.ops.wm.quit_blender()
                return None
        except Exception:
            error = traceback.format_exc()
            print(error,flush=True)
            job = state['job']
            if job and not job.closed:
                job.finish(error=error)
            capture.write_json(args.output/'capture_result.json', {'status':'FAILED','error':error})
            if not args.keep_open:
                bpy.ops.wm.quit_blender()
            return None
    bpy.app.timers.register(tick, first_interval=2.)


if __name__ == '__main__':
    run(arguments(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []))
