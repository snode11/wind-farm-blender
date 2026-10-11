"""Independent stage-1 editor and opt-in image exporter; defaults never render."""
from pathlib import Path
import argparse
import json
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'projects/gw184-single'), str(ROOT/'blender_frontend')]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('edit', 'prepare', 'analyze', 'scan', 'images'), default='edit')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--reopen', type=Path, help='Rebuild an editor session from an editor .blend snapshot')
    examples = parser.add_mutually_exclusive_group()
    examples.add_argument('--presets', action='store_true', help='Start a new document with the two synthetic crack examples')
    examples.add_argument('--all-presets', action='store_true', help='Start a new document with all six synthetic defect examples')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--time-s', type=float)
    parser.add_argument('--start-s', type=float, default=100.)
    parser.add_argument('--duration-s', type=float, default=.1)
    parser.add_argument('--export-fps', type=float, default=30.)
    parser.add_argument('--max-frames', type=int)
    parser.add_argument('--resume', action='store_true', help='Resume the exact partial G scan in --output')
    parser.add_argument('--render-authorized', action='store_true', help='Use only after the user authorizes images')
    parser.add_argument('--camera', choices=('C1', 'C2', 'C3'), action='append')
    args = parser.parse_args(argv)
    if args.mode == 'images' and not args.render_authorized:
        raise ValueError('Rendering is disabled; images require explicit user authorization and --render-authorized')
    if args.mode != 'edit' and args.output is None:
        parser.error('--output is required for noninteractive modes')
    if args.max_frames is not None and args.max_frames <= 0:
        parser.error('--max-frames must be positive')
    if args.resume and (args.mode != 'scan' or args.output is None):
        parser.error('--resume requires --mode scan and --output')
    if args.resume and args.config is None:
        args.config = args.output/'defects.json'
    import bpy
    from gw184.defect_editor import register, EditorSession
    from gw184.defect_outputs import create_run, save_analysis, export_appearance, write_json, update_run, save_records, save_review, resume_run
    from gw184.defect_timeline import FrameMapping, scan, summarize_intervals
    mapping = FrameMapping(args.start_s, args.duration_s, args.export_fps, 30)
    if args.start_s + args.duration_s > 320. + 1e-9:
        raise ValueError('Scan exceeds 320 s timeline')
    restored_time = 100.
    restored_doc = None
    if args.reopen:
        if args.config:
            parser.error('--reopen and --config cannot be combined')
        bpy.ops.wm.open_mainfile(filepath=str(args.reopen.resolve()))
        if not bpy.context.scene.get('blade_defect_editor'):
            raise ValueError('Not a GW184 defect editor snapshot')
        restored_doc = json.loads(bpy.context.scene['defect_document'])
        restored_time = float(bpy.context.scene['defect_time_s'])
        # Rebuild from the embedded authoritative configuration in this new
        # process; discard loaded derived meshes so stable C1/C2/C3 IDs stay free.
        # The source .blend on disk is never overwritten.
        bpy.ops.wm.read_factory_settings(use_empty=True)
    register()
    doc = json.loads(args.config.read_text()) if args.config else restored_doc
    editor = EditorSession(doc, presets=args.presets, all_presets=args.all_presets)
    args.time_s = restored_time if args.time_s is None else args.time_s
    editor.set_time(args.time_s)
    if args.config:
        editor.scene.gw184_defects.config_path = str(args.config.resolve())
    if args.mode == 'edit':
        if bpy.app.background:
            raise ValueError('edit requires a visible Blender window; use prepare for background')
        if editor.store.document['defects']:
            selected = next((i for i,d in enumerate(editor.store.document['defects']) if d['morphology']=='open_crack'),0)
            editor.scene.gw184_defects.selected = selected
            editor.closeup(selected, True)
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces.active.show_region_ui = True
                area.spaces.active.shading.type = 'SOLID'
        return editor
    previous_rows, start_index = [], 0
    if args.resume:
        run, previous_rows, start_index = resume_run(editor, args.output)
        mapping = run['mapping']
    else:
        run = create_run(editor, args.output, mode='A' if args.mode == 'images' else 'G', mapping=mapping)
    if args.mode in ('prepare', 'analyze'):
        if args.mode == 'analyze':
            save_analysis(editor, run)
        bpy.ops.wm.save_as_mainfile(filepath=str(run['output']/'GW184-defect-editor.blend'))
        run['manifest']['scene_file'] = 'GW184-defect-editor.blend'
        update_run(run)
    elif args.mode == 'scan':
        import signal
        completed, interrupted = [0], [False]
        old_signal = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, lambda *_: interrupted.__setitem__(0, True))
        def cancel():
            return interrupted[0] or (args.max_frames is not None and completed[0] >= args.max_frames)
        def progress(*values):
            completed[0] += 1
        try:
            result = scan(editor.rig, editor.payload['supports'], mapping,
                          run_id=run['manifest']['run_id'], state_version=run['manifest']['state_version'],
                          cancel=cancel, progress=progress, start_index=start_index)
            all_rows = previous_rows + result.pop('records')
            result['completed_frames'] += start_index
            result['completed_range'] = [0, result['completed_frames']-1] if result['completed_frames'] else None
            result['coverage'] = 'all_target_frames' if result['completed'] else 'partial_target_range'
            result['intervals'] = summarize_intervals(all_rows, mapping, result['completed'])
            save_records(run, all_rows)
            write_json(run['output']/'visible_intervals.json', result)
            run['manifest']['products']['visible_intervals'] = dict(state='GENERATED', complete=result['completed'])
            run['manifest']['state'] = 'G_SCAN_COMPLETE' if result['completed'] else 'G_SCAN_PARTIAL'
        except BaseException as exc:
            run['manifest']['state'] = 'FAILED'
            run['manifest']['error'] = repr(exc)
            raise
        finally:
            signal.signal(signal.SIGINT, signal.SIG_DFL if old_signal is None else old_signal)
            update_run(run)
            save_review(editor, run)
    elif args.mode == 'images':
        export_appearance(editor, run, render_authorized=True, time_s=args.time_s, camera_ids=args.camera)
    print(json.dumps(dict(output=str(run['output']), state=run['manifest']['state'], rendered=args.mode == 'images'), ensure_ascii=False))
    return editor


if __name__ == '__main__':
    main(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
