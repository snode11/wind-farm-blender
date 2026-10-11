"""Isolated-process defect CLI resume/reopen regression; rendering is forbidden.

Run with host Python, for example::

    python3 projects/gw184-single/tests/defect_cli_regression.py \
      --blender /Applications/Blender.app/Contents/MacOS/Blender \
      --output /absolute/new/output-directory

Every operation starts Blender with factory settings.  Each child replaces the
gw184.defect_outputs module's bpy binding with a proxy that raises on render
dispatch.  This does not intercept arbitrary Blender operators elsewhere in
the process.  The regression also checks for image and video artifacts.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
ENTRY = ROOT/'scripts/blender/gw184_defects.py'


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n')


def digest_value(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def child(request_path):
    """Executed only inside one new factory-startup Blender process."""
    import bpy
    request = json.loads(Path(request_path).read_text())
    report_path = Path(request['report'])
    report = dict(operation=request['operation'], blender=bpy.app.version_string, rendered=False)
    sys.path[:0] = [str(Path(__file__).resolve().parent), str(ROOT/'projects/gw184-single'), str(ROOT/'blender_frontend')]
    from defect_render_guard import GUARD_SCOPE, forbid_output_render
    report['render_guard_scope'] = GUARD_SCOPE
    try:
        with forbid_output_render() as renderer:
            if request['operation'] == 'cli':
                spec = importlib.util.spec_from_file_location('gw184_defect_cli_under_test', ENTRY)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                editor = module.main(request['argv'])
                from gw184.defect_editor import session
                report.update(document=editor.store.document,
                    state_version=editor.store.version,
                    time_s=float(editor.scene['defect_time_s']),
                    blender_frame=editor.scene.frame_current,
                    blender_subframe=editor.scene.frame_subframe,
                    camera_names=[cam.name for cam in editor.rig['cameras']],
                    support_sha256=digest_value(editor.payload['supports']),
                    healthy_reference_sha256=editor.surface.reference_geometry_sha256,
                    active_payload_is_committed=editor.payload is editor.geometry.active,
                    session_registered=session() is editor)
            elif request['operation'] == 'resume_check':
                from gw184.defect_editor import register, EditorSession
                from gw184.defect_outputs import resume_run
                folder = Path(request['directory'])
                register()
                editor = EditorSession(json.loads((folder/'defects.json').read_text()))
                _, rows, index = resume_run(editor, folder)
                report.update(records=len(rows), next_frame_index=index)
            else:
                raise ValueError('Unknown child operation')
            report['render_calls'] = renderer.call_count
            assert renderer.call_count == 0, 'No output render may be attempted'
        report['result'] = 'PASS'
    except BaseException as exc:
        report.update(result='ERROR', error_type=type(exc).__name__, error=str(exc))
        write_json(report_path, report)
        raise
    write_json(report_path, report)


def parent(blender, output):
    output = output.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Choose an empty new output directory; regression evidence is never overwritten')
    output.mkdir(parents=True, exist_ok=True)
    checks, processes = [], []
    started = time.perf_counter()
    report = dict(result='RUNNING', checks=checks, processes=processes, rendered=False,
        output=str(output), test_file=str(Path(__file__).resolve()))

    def check(name, condition):
        if not condition: raise AssertionError(name)
        checks.append(name)
        print('PASS: '+name, flush=True)

    def invoke(label, operation='cli', argv=None, directory=None, expected_error=None):
        request_path = output/(label+'.request.json')
        child_report = output/(label+'.child.json')
        request = dict(operation=operation, argv=argv or [], report=str(child_report))
        if directory is not None: request['directory'] = str(directory)
        write_json(request_path, request)
        cmd = [str(blender), '--background', '--factory-startup', '--python-exit-code', '1',
               '--python', str(Path(__file__).resolve()), '--', '--child', str(request_path)]
        env = os.environ.copy()
        env['BLENDER_USER_CONFIG'] = str(output/('config-'+label))
        tick = time.perf_counter()
        with (output/(label+'.log')).open('w') as log:
            completed = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=180)
        data = json.loads(child_report.read_text()) if child_report.exists() else {}
        processes.append(dict(label=label, returncode=completed.returncode,
            seconds=time.perf_counter()-tick, log=label+'.log', report=child_report.name))
        if expected_error is None:
            if completed.returncode or data.get('result') != 'PASS':
                raise AssertionError(f'{label} failed: {data}; see {output/(label+".log")}')
            check(label+': no output render attempted', data.get('render_calls') == 0)
        else:
            check(label+': expected refusal', completed.returncode != 0 and expected_error in data.get('error', ''))
        return data

    def load(folder, filename): return json.loads((folder/filename).read_text())
    try:
        scan_dir = output/'scan'
        first = invoke('partial', argv=['--mode', 'scan', '--presets', '--output', str(scan_dir),
            '--start-s', '100.25', '--duration-s', '.1', '--time-s', '100.25', '--max-frames', '1'])
        manifest, partial_rows = load(scan_dir, 'run_manifest.json'), load(scan_dir, 'visibility.json')
        partial = load(scan_dir, 'visible_intervals.json')
        check('partial preserves original nonzero job start and next index',
              manifest['state'] == 'G_SCAN_PARTIAL' and manifest['frame_mapping']['start_s'] == 100.25
              and partial['completed_frames'] == 1 and partial['next_frame_index'] == 1)
        check('partial contains all supports and all three fixed cameras at frame zero',
              len(partial_rows) == 9 and {r['frame_index'] for r in partial_rows} == {0}
              and {r['camera_id'] for r in partial_rows} == {'C1', 'C2', 'C3'}
              and {r['time_s'] for r in partial_rows} == {100.25})
        check('subframe mapping retained without rounding time',
              all(abs(r['sample']['blender_frame_coordinate']-3008.5) < 1e-9
                  and r['sample']['blender_frame'] == 3008 and abs(r['sample']['blender_subframe']-.5) < 1e-9
                  for r in partial_rows))
        check('scan restores prior editor absolute time', abs(first['time_s']-100.25) < 1e-9)

        # Alter copies only; the valid partial run remains unchanged for resume.
        for label, mutation, message in [
            ('missing-prefix', lambda rows: rows[:-1], 'missing records'),
            ('duplicate-prefix', lambda rows: rows+[rows[0]], 'duplicate partial record')]:
            corrupt = output/label
            shutil.copytree(scan_dir, corrupt)
            write_json(corrupt/'visibility.json', mutation(partial_rows))
            invoke(label, operation='resume_check', directory=corrupt, expected_error=message)
        corrupt = output/'stale-source'
        shutil.copytree(scan_dir, corrupt)
        stale_manifest = load(corrupt, 'run_manifest.json')
        stale_manifest['source_hashes'][next(iter(stale_manifest['source_hashes']))] = '0'*64
        write_json(corrupt/'run_manifest.json', stale_manifest)
        invoke('stale-source', operation='resume_check', directory=corrupt,
               expected_error='source or assets changed')

        # Omit the original mapping flags intentionally: --resume must load the
        # frozen job mapping rather than substitute default start/time values.
        resumed = invoke('resumed', argv=['--mode', 'scan', '--output', str(scan_dir), '--resume'])
        final_manifest, rows = load(scan_dir, 'run_manifest.json'), load(scan_dir, 'visibility.json')
        summary = load(scan_dir, 'visible_intervals.json')
        check('resume uses exact original index and half-open three-frame mapping',
              final_manifest['state'] == 'G_SCAN_COMPLETE' and summary['completed']
              and summary['completed_frames'] == 3 and summary['completed_range'] == [0, 2]
              and summary['next_frame_index'] is None and summary['frame_mapping']['start_s'] == 100.25)
        check('resume retains every prior record and adds no duplicate identities',
              rows[:len(partial_rows)] == partial_rows and len(rows) == 27
              and len({(r['support_id'], r['camera_id'], r['frame_index']) for r in rows}) == 27)
        check('resume absolute times are from original job rather than restart',
              sorted({r['time_s'] for r in rows}) == [100.25+i/30 for i in range(3)])
        check('independent processes preserve unsuffixed fixed camera names and supports',
              first['camera_names'] == resumed['camera_names'] == ['C1', 'C2', 'C3']
              and first['support_sha256'] == resumed['support_sha256'])
        invoke('complete-refused', operation='resume_check', directory=scan_dir,
               expected_error='Only an explicitly partial G scan')

        snapshot = output/'snapshot'
        saved = invoke('snapshot', argv=['--mode', 'prepare', '--config', str(scan_dir/'defects.json'),
            '--output', str(snapshot), '--time-s', '123.456789'])
        check('prepare writes an editor snapshot without rendering',
              (snapshot/'GW184-defect-editor.blend').is_file()
              and load(snapshot, 'run_manifest.json')['scene_file'] == 'GW184-defect-editor.blend')
        reopened_dir = output/'reopened'
        reopened = invoke('reopened', argv=['--mode', 'prepare', '--reopen',
            str(snapshot/'GW184-defect-editor.blend'), '--output', str(reopened_dir)])
        check('reopen preserves saved IDs revisions seeds and exact configuration',
              saved['document'] == reopened['document']
              and load(snapshot, 'defects.json') == load(reopened_dir, 'defects.json'))
        # Blender stores a frame coordinate at finite float precision; the
        # authoritative animation time is passed separately to at_time().
        # Reopening must preserve both representations, not manufacture a
        # double-precision frame value Blender cannot actually store.
        check('reopen restores exact authoritative time and saved Blender subframe',
              abs(reopened['time_s']-123.456789) < 1e-9
              and reopened['blender_frame'] == saved['blender_frame']
              and reopened['blender_subframe'] == saved['blender_subframe'])
        check('reopen rebuilds same reference and supports and committed active state',
              saved['healthy_reference_sha256'] == reopened['healthy_reference_sha256']
              and saved['support_sha256'] == reopened['support_sha256']
              and reopened['active_payload_is_committed'] and reopened['session_registered'])
        check('reopen keeps fixed camera IDs without datablock suffixes', reopened['camera_names'] == ['C1', 'C2', 'C3'])
        check('no image or video artifacts generated',
              not [p for p in output.rglob('*') if p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.exr', '.mp4')])
        report['result'] = 'PASS'
    except BaseException as exc:
        report.update(result='FAIL', error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        report['seconds'] = time.perf_counter()-started
        write_json(output/'result.json', report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


def main():
    argv = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else sys.argv[1:]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child', type=Path)
    parser.add_argument('--blender', type=Path, default=Path('/Applications/Blender.app/Contents/MacOS/Blender'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.child:
        return child(args.child)
    if args.output is None: parser.error('--output is required')
    return parent(args.blender, args.output)


if __name__ == '__main__': main()
