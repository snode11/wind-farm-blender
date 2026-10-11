"""Versioned G/A evidence bundles. Saving configuration never starts rendering."""
import copy
import csv
import hashlib
import json
import math
from pathlib import Path
import struct
import zlib
import bpy
from mathutils import Vector
from .model import PROJECT
from .scene import camera_metadata
from .defect_timeline import FrameMapping
from .defect_visibility import analyze


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def create_run(editor, directory, *, mode='G', mapping=None):
    if editor.draft is not None or editor.healthy_payload is not None:
        raise ValueError('Finish or cancel editing and return from healthy comparison before exporting')
    editor.verify_cameras()
    if mode not in ('G', 'A'):
        raise ValueError('Only G and A modes implemented; S is unsupported')
    output = Path(directory).expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('旧运行不覆盖；请选择空的新目录')
    output.mkdir(parents=True, exist_ok=True)
    root = PROJECT.parents[1]
    files = sorted((PROJECT / 'gw184').glob('*.py')) + sorted((PROJECT / 'sources').glob('*.*'))
    files += [PROJECT / p for p in ('geometry/target.json', 'cameras/layout.json', 'motion/timeline.json')]
    files += [root / p for p in ('scripts/blender/gw184_defects.py',
              'blender_frontend/wfrl_blender/scene_builder.py', 'blender_frontend/wfrl_blender/camera_projection.py')]
    mapping = mapping or FrameMapping(0., 320., 30, editor.rig['motion']['fps'])
    doc = editor.store.document
    write_json(output / 'defects.json', doc)
    manifest = dict(schema='wfrl.blade_defects.run.v1', run_id=output.name, mode=mode,
        state='PREPARED', state_version=editor.store.version, blender_version=bpy.app.version_string,
        source_hashes={str(p.relative_to(root)): digest(p) for p in files},
        defects_sha256=digest(output / 'defects.json'), reference_surface=editor.surface.metadata(),
        camera_configuration=editor.rig['layout'], motion=editor.rig['motion'], geometry=editor.rig['geometry'],
        cameras=camera_metadata(editor.rig), frame_mapping=mapping.to_dict(),
        effects=dict(ideal_pinhole=True, instantaneous=True, material=mode == 'A',
                     motion_blur=False, sensor_noise=False, distortion=False, compression=False),
        disabled_instances=[dict(id=d['id'], revision=d['revision'], state='DISABLED') for d in doc['defects'] if not d['enabled']],
        measurements=editor.payload['measurements'], products={name:dict(state='NOT_GENERATED') for name in
        ('visibility', 'projection_stats', 'visible_intervals', 'closeup', 'healthy', 'camera_raw', 'camera_crop')},
        images=[], appearance_review=dict(status='NOT_REVIEWED', reviewer=None, task=None),
        limitations=['Synthetic dimensions and morphology; not a measured GW184 damage specimen.',
                      'G geometry visibility does not establish image discernibility.',
                      'Health differences may include shadow/reflection changes; not segmentation labels.'])
    write_json(output / 'run_manifest.json', manifest)
    run = dict(output=output, manifest=manifest, mapping=mapping)
    save_review(editor, run)
    return run


def resume_run(editor, directory):
    """Resume only this exact G scan signature, retaining its original job mapping."""
    if editor.draft is not None or editor.healthy_payload is not None:
        raise ValueError('Finish editing and healthy comparison before resuming')
    output = Path(directory).expanduser().resolve()
    manifest = json.loads((output/'run_manifest.json').read_text())
    if manifest['mode'] != 'G' or manifest['state'] not in ('G_SCAN_PARTIAL',):
        raise ValueError('Only an explicitly partial G scan can be resumed')
    root = PROJECT.parents[1]
    if any(not (root/p).is_file() or digest(root/p) != value for p, value in manifest['source_hashes'].items()):
        raise ValueError('STALE_RUN: source or assets changed; create a new run')
    if manifest['blender_version'] != bpy.app.version_string:
        raise ValueError('STALE_RUN: Blender version changed')
    if editor.store.document != json.loads((output/'defects.json').read_text()):
        raise ValueError('STALE_RUN: defect configuration changed')
    if digest(output/'defects.json') != manifest['defects_sha256']:
        raise ValueError('STALE_RUN: saved configuration checksum changed')
    if editor.rig['layout'] != manifest['camera_configuration'] or editor.rig['motion'] != manifest['motion']:
        raise ValueError('STALE_RUN: camera or motion changed')
    m = manifest['frame_mapping']
    mapping = FrameMapping(m['start_s'], m['duration_s'], m['export_fps'], m['blender_fps'])
    previous = json.loads((output/'visible_intervals.json').read_text())
    start_index = previous['next_frame_index']
    if type(start_index) is not int or not 0 <= start_index < mapping.frame_count:
        raise ValueError('Invalid resume index')
    rows = json.loads((output/'visibility.json').read_text())
    expected = {(support['defect_id'], support['revision'], support['support_id'], camera.name, i)
                for support in editor.payload['supports'] for camera in editor.rig['cameras'] for i in range(start_index)}
    observed = set()
    for row in rows:
        key = (row['defect_id'], row['revision'], row['support_id'], row['camera_id'], row['frame_index'])
        if key in observed or key not in expected:
            raise ValueError('Inconsistent or duplicate partial record')
        sample = mapping.sample(row['frame_index'])
        if (abs(row['time_s']-sample['time_s']) > 1e-9 or row['run_id'] != manifest['run_id']
                or row['state_version'] != manifest['state_version']):
            raise ValueError('Partial record time or provenance mismatch')
        observed.add(key)
    if observed != expected:
        raise ValueError('Partial scan has missing records; cannot claim prefix complete')
    return dict(output=output, manifest=manifest, mapping=mapping), rows, start_index


def update_run(run):
    write_json(run['output'] / 'run_manifest.json', run['manifest'])


def check_run(editor, run):
    editor.verify_cameras()
    if editor.draft is not None or editor.healthy_payload is not None:
        raise ValueError('Do not export temporary editing or healthy comparison state')
    if editor.store.version != run['manifest']['state_version']:
        raise ValueError('STALE_STATE: create a new run after editing')
    if editor.store.document != json.loads((run['output']/'defects.json').read_text()):
        raise ValueError('STALE_RUN: configuration changed')


def save_review(editor, run):
    text = ['# GW184 刚性缺陷检查', '', f"模式：{run['manifest']['mode']}；状态：{run['manifest']['state']}", '',
            '合成形态与尺寸：参见 defects.json、run_manifest.json 中的参考模型、参数来源及生成后测量值。',
            '近景外观审核：NOT_REVIEWED。固定相机外观审核：NOT_REVIEWED。',
            '当前原型支持细裂纹、开口裂缝、凹坑、局部侵蚀、涂层剥落与雷击损伤六类合成形态；柔性、传感器模式未实施。',
            '几何扫描中的候选帧只由几何指标排序，不能自动称为最易辨识帧。', '', '## 产物状态', '']
    for key, value in run['manifest']['products'].items():
        text.append(f"- {key}: {value['state']}")
    text += ['', '## 缺陷参数来源', '']
    for d in json.loads((run['output']/'defects.json').read_text())['defects']:
        text.append(f"- {d['id']} r{d['revision']}: {json.dumps(d.get('provenance', {}), ensure_ascii=False)}")
    (run['output'] / 'review.md').write_text('\n'.join(text) + '\n')


def save_records(run, records):
    write_json(run['output'] / 'visibility.json', records)
    # Preserve nested values and nulls as JSON, rather than silently flattening definitions.
    keys = sorted({key for row in records for key in row}) or ['run_id', 'defect_id', 'camera_id', 'time_s', 'state']
    with (run['output'] / 'projection_stats.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        for row in records:
            writer.writerow({k: json.dumps(v, ensure_ascii=False, allow_nan=False) if isinstance(v, (dict, list, tuple)) or v is None else v for k, v in row.items()})
    for key in ('visibility', 'projection_stats'):
        run['manifest']['products'][key] = dict(state='GENERATED', records=len(records))
    update_run(run)


def save_analysis(editor, run, time_s=None):
    check_run(editor, run)
    t = float(editor.scene['defect_time_s']) if time_s is None else time_s
    sample = run['mapping'].current(t)
    records = analyze(editor.rig, editor.payload['supports'], t, frame_index=sample.get('frame_index'),
                      run_id=run['manifest']['run_id'], state_version=editor.store.version)
    for row in records:
        row['sample_mapping'] = sample
    save_records(run, records)
    editor.records = records
    editor.scene['defect_analysis_status'] = 'EVALUATED'
    run['manifest']['state'] = 'G_COMPLETE' if run['manifest']['mode'] == 'G' else 'G_READY_A_NOT_GENERATED'
    save_review(editor, run)
    update_run(run)
    return records


def crop_png(source, destination, roi):
    """Losslessly crop RGB/RGBA 8-bit PNG scanlines; no resampling or color transform."""
    raw = Path(source).read_bytes()
    if raw[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Not PNG')
    pos, chunks, idat = 8, [], bytearray()
    while pos < len(raw):
        count = struct.unpack('>I', raw[pos:pos+4])[0]
        kind, data = raw[pos+4:pos+8], raw[pos+8:pos+8+count]
        if zlib.crc32(kind+data) & 0xffffffff != struct.unpack('>I', raw[pos+8+count:pos+12+count])[0]:
            raise ValueError('PNG CRC mismatch')
        if kind == b'IHDR':
            width, height, bits, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', data)
        elif kind == b'IDAT':
            idat.extend(data)
        elif kind not in (b'IEND',):
            chunks.append((kind, data))
        pos += count + 12
    if bits != 8 or color not in (2, 6) or compression or filtering or interlace:
        raise ValueError('Crop supports noninterlaced 8-bit RGB/RGBA renderer output only')
    x0, y0, x1, y1 = roi
    if not all(type(v) is int for v in roi) or not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError('Invalid half-open integer ROI')
    bpp = 3 if color == 2 else 4
    stride = width*bpp
    decompressed = zlib.decompress(idat)
    if len(decompressed) != (stride+1)*height:
        raise ValueError('Invalid PNG data length')
    previous = bytearray(stride)
    cropped = bytearray()
    def paeth(a, b, c):
        p = a+b-c
        pa, pb, pc = abs(p-a), abs(p-b), abs(p-c)
        return a if pa <= pb and pa <= pc else b if pb <= pc else c
    for y in range(height):
        offset = y*(stride+1)
        kind = decompressed[offset]
        row = bytearray(decompressed[offset+1:offset+1+stride])
        if kind > 4:
            raise ValueError('Unsupported PNG filter')
        for i in range(stride):
            a, b, c = row[i-bpp] if i >= bpp else 0, previous[i], previous[i-bpp] if i >= bpp else 0
            predictor = (0, a, b, (a+b)//2, paeth(a, b, c))[kind]
            row[i] = (row[i]+predictor) & 255
        if y0 <= y < y1:
            cropped.extend(b'\x00' + row[x0*bpp:x1*bpp])
        previous = row
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind+data) & 0xffffffff)
    result = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', x1-x0, y1-y0, bits, color, 0, 0, 0))
    # Color metadata retained; position-dependent ancillary metadata intentionally omitted.
    for kind, data in chunks:
        if kind in (b'gAMA', b'cHRM', b'sRGB', b'iCCP'):
            result += chunk(kind, data)
    result += chunk(b'IDAT', zlib.compress(bytes(cropped))) + chunk(b'IEND', b'')
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    Path(destination).write_bytes(result)


def export_appearance(editor, run, *, render_authorized=False, time_s=None, closeups=True, healthy=True, camera_ids=None):
    if not render_authorized:
        raise ValueError('Rendering is disabled; explicit user authorization and --render-authorized required')
    if run['manifest']['mode'] != 'A':
        raise ValueError('A image export requires an A run')
    if editor.draft is not None:
        raise ValueError('Finish editing before images')
    check_run(editor, run)
    scene = editor.scene
    playing = bool(bpy.context.screen and bpy.context.screen.is_animation_playing)
    original_time = float(scene['defect_time_s'])
    t = original_time if time_s is None else time_s
    original_camera, original_path = scene.camera, scene.render.filepath
    original_seed = scene.cycles.seed
    original_view = (scene.view_settings.view_transform, scene.view_settings.look,
                     scene.view_settings.exposure, scene.view_settings.gamma)
    temporary_payloads, temporary_objects = [], []
    committed = editor.payload
    sample = run['mapping'].current(t)
    index = sample.get('frame_index')
    suffix = f'f{index:06d}' if index is not None else 'check_t' + format(t, '.9f').replace('.', '_')
    try:
        if playing:
            bpy.ops.screen.animation_cancel(restore_frame=False)
        run['manifest']['state'] = 'RENDERING'
        run['manifest']['render_request'] = dict(time_s=t, sample_mapping=sample, instantaneous=True)
        update_run(run)
        editor.set_time(t)
        scene.cycles.seed = 17
        rows = save_analysis(editor, run, t)
        run['manifest']['image_camera_metadata'] = camera_metadata(editor.rig)
        # G analysis updates its own ready state. The encompassing A job is
        # still producing images, so persist the actual current operation.
        run['manifest']['state'] = 'RENDERING'
        update_run(run)
        def render_one(camera, relative, instances, role):
            path = run['output'] / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            scene.camera, scene.render.filepath = camera, str(path.with_suffix('.partial.png'))
            result = bpy.ops.render.render(write_still=True)
            if 'CANCELLED' in result:
                raise KeyboardInterrupt('Image render cancelled')
            Path(scene.render.filepath).replace(path)
            record = dict(file=relative, role=role, camera_id=camera.name, defect_ids=instances,
                          time_s=t, sample_mapping=sample, resolution=[scene.render.resolution_x, scene.render.resolution_y],
                          world_from_camera=[list(r) for r in camera.matrix_world], lens_mm=camera.data.lens,
                          sensor_width_mm=camera.data.sensor_width, seed=17, sha256=digest(path), overlay=False)
            record['color_management'] = dict(view_transform=scene.view_settings.view_transform,
                look=scene.view_settings.look, exposure=scene.view_settings.exposure,
                gamma=scene.view_settings.gamma)
            record['render_settings'] = dict(engine=scene.render.engine, samples=scene.cycles.samples,
                device=scene.cycles.device, denoising=scene.cycles.use_denoising)
            run['manifest']['images'].append(record)
            update_run(run)
            return path
        enabled = [d for d in editor.store.document['defects'] if d['enabled']]
        for camera in editor.rig['cameras']:
            if camera_ids is not None and camera.name not in camera_ids:
                continue
            path = render_one(camera, f'camera_raw/{camera.name}_{suffix}.png', [d['id'] for d in enabled], 'camera_raw')
            for row in rows:
                if row.get('camera_id') != camera.name or not row.get('primary', True):
                    continue
                box = row.get('projected_bbox_px')
                if box is None:
                    continue
                w, h = scene.render.resolution_x, scene.render.resolution_y
                x0, y0, x1, y1 = box
                roi = [max(0, math.floor(x0+.5)), max(0, math.floor(y0+.5)), min(w, math.ceil(x1+.5)), min(h, math.ceil(y1+.5))]
                if roi[0] < roi[2] and roi[1] < roi[3]:
                    dest = f"camera_crop/{camera.name}_{suffix}_{row['defect_id']}.png"
                    crop_png(path, run['output']/dest, roi)
                    run['manifest']['images'].append(dict(file=dest, role='camera_crop', source=str(path.relative_to(run['output'])), roi_half_open=roi, scale=1, resampled=False))
        fixed_requested = any(camera_ids is None or c.name in camera_ids for c in editor.rig['cameras'])
        run['manifest']['products']['camera_raw'] = dict(state='GENERATED' if fixed_requested else 'NOT_REQUESTED')
        run['manifest']['products']['camera_crop'] = dict(state='GENERATED' if fixed_requested else 'NOT_REQUESTED', policy='Exact pixel copy, 1:1; empty/outside ROI skipped')
        if healthy:
            # One counterfactual snapshot per affected blade; disable ALL its defects.
            for blade_id in sorted({d['blade_id'] for d in enabled}):
                doc = copy.deepcopy(editor.store.document)
                for d in doc['defects']:
                    if d['blade_id'] == blade_id:
                        d['enabled'] = False
                payload = editor.geometry.prepare(doc)
                temporary_payloads.append(payload)
                editor.geometry.activate(payload)
                for camera in editor.rig['cameras']:
                    if camera_ids is not None and camera.name not in camera_ids:
                        continue
                    render_one(camera, f'healthy/B{blade_id}_{camera.name}_{suffix}.png',
                               [d['id'] for d in enabled if d['blade_id'] == blade_id], 'healthy_target_blade')
                editor.geometry.activate(committed)
            run['manifest']['products']['healthy'] = dict(state='GENERATED', policy='All defects disabled on each target blade independently')
        if closeups:
            # Inspection views retain highlights and use a restrained fill light.
            # These settings never affect fixed-camera/healthy pairs above.
            scene.view_settings.view_transform = 'AgX'
            scene.view_settings.look = 'None'
            scene.view_settings.exposure = 0.
            scene.view_settings.gamma = 1.
            for d in enabled:
                f = editor.surface.frame(d['anchor'])
                blade = editor.rig['blades'][d['blade_id']-1]
                p = blade.matrix_world @ Vector(f['point'])
                n = (blade.matrix_world.to_3x3() @ Vector(f['normal'])).normalized()
                tangent = (blade.matrix_world.to_3x3() @ Vector(f['e_t'])).normalized()
                data = bpy.data.cameras.new('GW184.Inspection')
                cam = bpy.data.objects.new('GW184.Inspection', data)
                editor.rig['collection'].objects.link(cam)
                temporary_objects.append(cam)
                data.lens, data.sensor_width = 50., 36.
                # Explicit inspection light recorded; never used for fixed camera raw pairs.
                light_data = bpy.data.lights.new('GW184.InspectionLight', 'AREA')
                light_data.energy, light_data.size = 5., max(.2, d['shape']['length_m'])
                light = bpy.data.objects.new('GW184.InspectionLight', light_data)
                editor.rig['collection'].objects.link(light)
                temporary_objects.append(light)
                light.location = p + n * 1.2 + tangent * .5
                light.rotation_euler = (p-light.location).to_track_quat('-Z', 'Y').to_euler()
                for label, angle in [('front', 0.), ('oblique', .7)]:
                    cam.location = p + (n+tangent*angle).normalized()*max(.5, d['shape']['length_m']*2.8)
                    cam.rotation_euler = (p-cam.location).to_track_quat('-Z', 'Y').to_euler()
                    bpy.context.view_layer.update()
                    render_one(cam, f"closeup/{d['id']}_{suffix}_{label}.png", [d['id']], 'inspection_light_closeup')
                    run['manifest']['images'][-1]['inspection_light'] = dict(type='AREA', energy=light_data.energy, size=light_data.size, location=list(light.location))
                light.hide_render = True
            run['manifest']['products']['closeup'] = dict(state='GENERATED', lighting='Additional recorded inspection light')
        run['manifest']['state'] = 'A_IMAGES_GENERATED_NOT_REVIEWED'
    except BaseException as exc:
        run['manifest']['state'] = 'CANCELLED' if isinstance(exc, KeyboardInterrupt) else 'FAILED'
        run['manifest']['error'] = repr(exc)
        raise
    finally:
        editor.geometry.activate(committed)
        for payload in temporary_payloads:
            editor.geometry.dispose(payload)
        for obj in temporary_objects:
            data = obj.data
            kind = obj.type
            bpy.data.objects.remove(obj, do_unlink=True)
            (bpy.data.cameras if kind == 'CAMERA' else bpy.data.lights).remove(data)
        scene.camera, scene.render.filepath, scene.cycles.seed = original_camera, original_path, original_seed
        (scene.view_settings.view_transform, scene.view_settings.look,
         scene.view_settings.exposure, scene.view_settings.gamma) = original_view
        editor.set_time(original_time)
        if playing and bpy.context.screen and not bpy.context.screen.is_animation_playing:
            bpy.ops.screen.animation_play()
        update_run(run)
        save_review(editor, run)
