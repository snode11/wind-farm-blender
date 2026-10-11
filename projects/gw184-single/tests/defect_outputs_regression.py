"""A-export plumbing contract using manufactured PNG fixtures, without rendering.

An earlier dynamic bpy.ops mock failed and caused a real temporary scene render.
Use the result of this script's current run, not its existence, as evidence.
This contract cannot establish rendered appearance or image discernibility.

The output module receives a hard bpy proxy for the entire test. It writes a known
full-resolution RGB fixture using stdlib bytes/zlib. Every fixture and manifest
is contained in TemporaryDirectory and removed before reporting. No scene image,
material appearance, lighting result or perceptual evidence is generated.
"""
from copy import deepcopy
import json
import hashlib
from pathlib import Path
import struct
import sys
import tempfile
from unittest.mock import patch
from types import SimpleNamespace
import zlib

import bpy
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'projects/gw184-single'), str(ROOT/'blender_frontend')]
from gw184.defect_editor import EditorSession, register
from gw184.defect_schema import new_defect, PRESETS
from gw184.defect_outputs import create_run, export_appearance
import gw184.defect_outputs as output_module
from gw184.defect_timeline import FrameMapping


def chunk(kind, data):
    return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)


def fixture_png(width, height):
    # Pixel value is analytically recoverable; rows differ and cannot be confused
    # with the synthetic wind-turbine scene or a blank output success marker.
    rows = [b'\x00'+bytes(component for x in range(width)
        for component in (x%251,y%241,(x+y)%239)) for y in range(height)]
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+
        chunk(b'IDAT',zlib.compress(b''.join(rows),1))+chunk(b'IEND',b''))


def read_fixture_crop(path):
    data=Path(path).read_bytes(); offset=8; packed=bytearray(); dimensions=None
    while offset<len(data):
        size=struct.unpack('>I',data[offset:offset+4])[0]
        kind,contents=data[offset+4:offset+8],data[offset+8:offset+8+size]
        if kind==b'IHDR': dimensions=struct.unpack('>II',contents[:8])
        elif kind==b'IDAT': packed.extend(contents)
        offset+=size+12
    width,height=dimensions
    raw=zlib.decompress(packed)
    assert all(raw[y*(width*3+1)]==0 for y in range(height)), 'crop must use filter 0'
    return width,height,[raw[y*(width*3+1)+1:(y+1)*(width*3+1)] for y in range(height)]


def snapshot(editor):
    scene=editor.scene
    return dict(document=editor.store.document, payload=editor.payload, version=editor.store.version,
        data=[blade.data for blade in editor.rig['blades']],
        time=float(scene['defect_time_s']), frame=scene.frame_current, subframe=scene.frame_subframe,
        camera=scene.camera, filepath=scene.render.filepath, seed=scene.cycles.seed,
        color_management=(scene.view_settings.view_transform, scene.view_settings.look,
                          scene.view_settings.exposure, scene.view_settings.gamma),
        camera_signature=editor.fixed_camera_signature(),
        resources=(len(bpy.data.objects),len(bpy.data.meshes),len(bpy.data.materials),
                   len(bpy.data.cameras),len(bpy.data.lights),len(bpy.data.node_groups)))


def assert_restored(editor, before):
    after=snapshot(editor)
    for key in ('document','version','data','frame','camera','filepath','seed','color_management','camera_signature','resources'):
        assert after[key]==before[key], f'{key} was not restored'
    assert abs(after['time']-before['time'])<1e-10
    assert abs(after['subframe']-before['subframe'])<1e-6
    assert after['payload'] is before['payload'] and editor.geometry.active is before['payload']
    assert editor.healthy_payload is None


def reject(call, message):
    try: call()
    except ValueError as exc: assert message in str(exc), str(exc)
    else: raise AssertionError('Expected contract rejection: '+message)


def main():
    register()
    editor=EditorSession()
    doc=editor.store.document
    examples=[new_defect(editor.surface,kind,id='FIXTURE_'+kind.upper()) for kind in PRESETS]
    fine=examples[0]
    other=new_defect(editor.surface,'open_crack',id='FIXTURE_OTHER',blade_id=2)
    doc['defects']=[*examples,other]
    editor.commit_document(doc)
    scene=editor.scene
    editor.set_time(99.+1/120)
    scene.camera=editor.rig['cameras'][2]
    scene.render.filepath='/tmp/gw184-output-contract-sentinel-not-written.png'
    scene.cycles.seed=313
    before=snapshot(editor)
    width,height=scene.render.resolution_x,scene.render.resolution_y
    assert (width,height)==(3840,2160), 'fixture must retain configured full camera resolution'
    png=fixture_png(width,height)
    calls=[]; fail_healthy=[False]; cancel_closeup=[False]
    mapping=FrameMapping(100.,.1,30,30)
    t=mapping.sample(1)['time_s']
    with tempfile.TemporaryDirectory(prefix='gw184-no-render-output-contract-') as temporary:
        folder=Path(temporary).resolve()
        def fake_render(*args,**kwargs):
            assert not args and kwargs=={'write_still':True}
            path=Path(scene.render.filepath)
            assert path.is_relative_to(folder), 'fixture must stay inside disposable directory'
            manifest=json.loads((folder/path.relative_to(folder).parts[0]/'run_manifest.json').read_text())
            assert manifest['state']=='RENDERING', 'G analysis must not overwrite the live A job state'
            active=deepcopy(editor.geometry.active['document'])
            role=path.relative_to(folder).parts[-2]
            entry=dict(file=str(path),role=role,active=active,camera=scene.camera.name,
                matrix=tuple(tuple(row) for row in scene.camera.matrix_world),
                time=float(scene['defect_time_s']),frame=scene.frame_current,subframe=scene.frame_subframe,
                seed=scene.cycles.seed,lens=scene.camera.data.lens,
                view_transform=scene.view_settings.view_transform,
                live_document=editor.store.document,live_version=editor.store.version,
                visible_inspection_lights=[obj.name for obj in scene.objects
                    if obj.type=='LIGHT' and obj.name.startswith('GW184.InspectionLight') and not obj.hide_render])
            calls.append(entry)
            assert entry['live_document']==before['document'] and entry['live_version']==before['version']
            assert abs(entry['time']-t)<1e-10 and entry['seed']==17
            if role=='healthy' and fail_healthy[0]:
                raise KeyboardInterrupt('injected fixture cancellation with healthy mesh active')
            if role=='closeup' and cancel_closeup[0]:
                return {'CANCELLED'}
            path.write_bytes(png)
            return {'FINISHED'}

        # Analysis is a deliberately known crop-ROI fixture. Projection geometry is
        # tested by separate G regressions, not inferred from this fake image.
        def fake_analysis(rig,supports,time_s,**kwargs):
            return [dict(defect_id=fine['id'],revision=1,primary=True,camera_id='C2',
                projected_bbox_px=[100.5,90.5,105.5,93.5],time_s=time_s,
                support_id='fixture-roi',status='NOT_EVALUATED',
                reasons=['OUTPUT_CONTRACT_FIXTURE_NOT_GEOMETRY_EVIDENCE'])]

        # bpy.ops synthesizes operator attributes dynamically; patching
        # bpy.ops.render.render is NOT a reliable interception boundary. Replace
        # this module's entire bpy binding so renderer dispatch has no route to
        # the original dynamic operator namespace.
        proxy = SimpleNamespace(data=bpy.data,context=bpy.context,app=bpy.app,
            ops=SimpleNamespace(render=SimpleNamespace(render=fake_render),screen=bpy.ops.screen))
        with patch.object(output_module,'bpy',proxy), patch('gw184.defect_outputs.analyze',side_effect=fake_analysis):
            assert export_appearance.__globals__['bpy'] is proxy
            assert output_module.bpy.ops.render.render is fake_render
            run=create_run(editor,folder/'success',mode='A',mapping=mapping)
            reject(lambda:export_appearance(editor,run,time_s=t),'Rendering is disabled')
            assert not calls and not list(folder.rglob('*.png'))
            g_run=create_run(editor,folder/'wrong-mode',mode='G',mapping=mapping)
            reject(lambda:export_appearance(editor,g_run,render_authorized=True,time_s=t),'requires an A run')
            assert not calls
            export_appearance(editor,run,render_authorized=True,time_s=t,camera_ids=['C2'])
            assert_restored(editor,before)
            assert run['manifest']['state']=='A_IMAGES_GENERATED_NOT_REVIEWED'
            assert run['manifest']['appearance_review']['status']=='NOT_REVIEWED'
            raw=[row for row in calls if row['role']=='camera_raw']
            healthy=[row for row in calls if row['role']=='healthy']
            closeup=[row for row in calls if row['role']=='closeup']
            assert len(raw)==1 and len(healthy)==2 and len(closeup)==2*len(doc['defects'])
            assert raw[0]['active']==before['document']
            for row in healthy:
                disabled={d['blade_id'] for d in row['active']['defects'] if not d['enabled']}
                assert len(disabled)==1
                blade=next(iter(disabled))
                assert all(d['enabled']==(d['blade_id']!=blade) for d in row['active']['defects'])
                for field in ('camera','matrix','time','frame','subframe','seed','lens'):
                    assert row[field]==raw[0][field], f'healthy pair differs in {field}'
                assert row['visible_inspection_lights']==raw[0]['visible_inspection_lights']==[]
            assert {d['blade_id'] for row in healthy for d in row['active']['defects'] if not d['enabled']}=={1,2}
            assert all(len(row['visible_inspection_lights'])==1 for row in closeup)
            assert all(row['view_transform']=='AgX' for row in closeup)
            assert all(row['view_transform']==before['color_management'][0] for row in raw)
            cropped=[row for row in run['manifest']['images'] if row['role']=='camera_crop']
            assert len(cropped)==1
            crop=cropped[0]
            assert crop['scale']==1 and crop['resampled'] is False
            x0,y0,x1,y1=crop['roi_half_open']
            cw,ch,rows=read_fixture_crop(run['output']/crop['file'])
            assert (cw,ch)==(x1-x0,y1-y0)
            for index,pixels in enumerate(rows):
                y=y0+index
                expected=bytes(v for x in range(x0,x1) for v in (x%251,y%241,(x+y)%239))
                assert pixels==expected, 'crop changed fixture pixel values'
            assert not list(folder.rglob('*.partial.png'))
            # Cancellation while a counterfactual healthy mesh is active must
            # restore committed data and dispose every temporary resource.
            cancelled=create_run(editor,folder/'cancelled',mode='A',mapping=mapping)
            fail_healthy[0]=True
            try:
                export_appearance(editor,cancelled,render_authorized=True,time_s=t,
                    camera_ids=['C2'],closeups=False)
            except KeyboardInterrupt as exc:
                assert 'fixture cancellation' in str(exc)
            else: raise AssertionError('Expected fixture cancellation')
            finally: fail_healthy[0]=False
            assert cancelled['manifest']['state']=='CANCELLED'
            assert_restored(editor,before)
            # Blender can return CANCELLED instead of raising. Restore color
            # management and temporary inspection objects even before a PNG exists.
            cancelled_closeup=create_run(editor,folder/'cancelled-closeup',mode='A',mapping=mapping)
            count=len(calls);cancel_closeup[0]=True
            try:
                export_appearance(editor,cancelled_closeup,render_authorized=True,time_s=t,
                    camera_ids=[],healthy=False)
            except KeyboardInterrupt as exc:
                assert 'Image render cancelled' in str(exc)
            else: raise AssertionError('Expected Blender cancellation result')
            finally: cancel_closeup[0]=False
            assert len(calls)==count+1 and calls[-1]['role']=='closeup'
            assert cancelled_closeup['manifest']['state']=='CANCELLED'
            assert cancelled_closeup['manifest']['products']['camera_raw']['state']=='NOT_REQUESTED'
            assert_restored(editor,before)
            # Reject temporary healthy state before output or renderer dispatch.
            healthy_doc=editor.store.document
            for defect in healthy_doc['defects']: defect['enabled']=False
            payload=editor.geometry.prepare(healthy_doc)
            editor.healthy_payload=payload;editor.geometry.activate(payload)
            count=len(calls)
            try:
                reject(lambda:create_run(editor,folder/'forbidden-healthy',mode='A'),'healthy comparison')
                reject(lambda:export_appearance(editor,run,render_authorized=True,time_s=t),'temporary editing or healthy')
                assert not (folder/'forbidden-healthy').exists() and len(calls)==count
            finally:
                editor.geometry.activate(before['payload']);editor.geometry.dispose(payload);editor.healthy_payload=None
            assert_restored(editor,before)
            # A real edit makes an existing run unusable, before any image write.
            stale=create_run(editor,folder/'stale',mode='A',mapping=mapping)
            editor.mutate('TOGGLE',0)
            count=len(calls)
            reject(lambda:export_appearance(editor,stale,render_authorized=True,time_s=t),'STALE_STATE')
            assert len(calls)==count and not list(stale['output'].rglob('*.png'))
        writes=len(list(folder.rglob('*.png')))
        assert writes>0
    assert not folder.exists(), 'fixture directory must be removed'
    sources={name:Path(sys.modules[name].__file__).resolve() for name in (
        'gw184.defect_outputs','gw184.defect_editor','gw184.defect_geometry','gw184.defect_schema')}
    print(json.dumps(dict(result='PASS',renderer_invocations=0,appearance_evidence=False,
        blender_version=bpy.app.version_string,defect_count=len(doc['defects']),
        morphologies=sorted({d['morphology'] for d in doc['defects']}),
        source_paths={name:str(path) for name,path in sources.items()},
        source_sha256={name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in sources.items()},
        render_guard_scope='gw184.defect_outputs bpy binding; not process-wide',
        fixture_image_writes=writes,fixture_directory_removed=True,
        checks=['authorization and mode guards','A job remains RENDERING after G analysis','full resolution manufactured PNG fixture',
            'fixed and healthy pairs match camera/time/seed','all target blade defects disabled; others preserved',
            'live configuration unchanged','frame/subframe/time/camera/path/seed restored',
            'temporary mesh/material/camera/light cleanup','exact crop pixels',
            'healthy cancellation restores committed state','temporary healthy state rejected','stale run rejected']),
        ensure_ascii=False))

# The production entry point has no access to real render operators even before
# the fixture proxy is installed. Other bpy facilities remain real for state and
# cleanup assertions; appearance itself remains completely untested.
def forbidden_render(*args, **kwargs):
    raise AssertionError('Real rendering forbidden in contract regression')

_guard = SimpleNamespace(data=bpy.data,context=bpy.context,app=bpy.app,
    ops=SimpleNamespace(render=SimpleNamespace(render=forbidden_render),screen=bpy.ops.screen))
with patch.object(output_module,'bpy',_guard):
    main()
