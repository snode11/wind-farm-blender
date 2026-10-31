"""Export a complete source-grid geometry recording in an isolated Blender process."""
from pathlib import Path
import argparse
import importlib
import json
import math
import os
import sys
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--start', type=float, help='Absolute source time; defaults to source start')
    parser.add_argument('--end', type=float, help='Absolute source time; defaults to start + 1 s')
    parser.add_argument('--fps', type=float, default=30.)
    parser.add_argument('--candidates', type=int, default=512)
    parser.add_argument('--location', type=float, nargs=3, default=(-3.5,-1.7,-2.))
    parser.add_argument('--rotation', type=float, nargs=3, default=(0,82.5,180))
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    module = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
    if module == 'wfrl_blender':
        root = Path(__file__).resolve().parents[2]
        sys.path[:0] = [str(root/'blender_frontend'), str(root)]
    addon = importlib.import_module(module)
    if module == 'wfrl_blender':
        addon.register()
    farm = importlib.import_module(module + '.farm_flex')
    api = importlib.import_module(module + '.radar_x100.core')
    scene_api = importlib.import_module(module + '.radar_x100.scene')
    import bpy
    addon.load_demo_scene(args.package or farm.default_package())
    sampler = scene_api.SceneSampler(farm.active_for(bpy.context.scene),
        mount_from_sensor=scene_api.installation_matrix(args.location,args.rotation))
    args.start = sampler.interval[0] if args.start is None else args.start
    args.end = min(sampler.interval[1], args.start + 1) if args.end is None else args.end
    if (not all(math.isfinite(x) for x in (args.start,args.end,args.fps))
            or not sampler.interval[0] <= args.start < args.end <= sampler.interval[1]
            or not 0 < args.fps <= 120 or (args.end-args.start)*args.fps > 100000):
        raise ValueError('Invalid export source interval or sample budget')
    sink = api.recording.Recorder(args.output, description=dict(source_kind='GEOMETRY_SIMULATION',
        source_grid_fps=args.fps, interval_s=[args.start,args.end], calibration_id=sampler.calibration_id))
    session = str(uuid.uuid4())
    try:
        for sequence in range(int(math.floor((args.end-args.start)*args.fps + 1e-7))+1):
            t = args.start + sequence/args.fps
            frame = api.simulation.simulate(sampler.sample,t,sensor_id='X100-T1',session_id=session,
                sequence=sequence,interval_s=sampler.interval,candidate_budget=args.candidates,
                visibility=scene_api.bvh_visibility)
            frame.provenance['calibration_id'] = sampler.calibration_id
            sink.append(frame,t-args.start)
        sink.close()
    except Exception as exc:
        sink.fail(str(exc))
        raise
    print('X100_EXPORT_PASS', json.dumps(dict(frames=sink.rows,output=str(args.output),core=api.schema.__file__)))
    addon.unregister()


if __name__ == '__main__':
    main()
