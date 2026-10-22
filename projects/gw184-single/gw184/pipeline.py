"""Offline synchronized renderer with resumable, configuration-locked outputs."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import time
import bpy
from .model import PROJECT, read_config, boundaries, frame_times, motion_at
from .scene import build, pose, at_time, camera_metadata, bake
from .coverage import audit_pose, sweep


def write_json(path, value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    tmp.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def signature(args, configs):
    root=PROJECT.parents[1]
    files=sorted((PROJECT/'gw184').glob('*.py'))+sorted((PROJECT/'sources').glob('*.*'))
    files += sorted((PROJECT/'geometry').glob('*.json'))
    files += [root/'scripts/blender/gw184_single.py',
              root/'blender_frontend/wfrl_blender/scene_builder.py',
              root/'blender_frontend/wfrl_blender/camera_projection.py']
    data=dict(configs=list(configs),code_and_sources={str(p.relative_to(root)):digest(p) for p in files},
              blender=bpy.app.version_string,mode=args.mode,width=args.width,samples=args.samples,
              engine=args.engine,start_s=args.start_s,duration_s=args.duration_s)
    return data


def render(rig,camera,path):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        # Only reuse complete PNGs written via atomic rename by this locked job.
        return
    rig['scene'].camera=camera
    temporary=path.with_name(path.stem+'.partial.png')
    rig['scene'].render.filepath=str(temporary)
    bpy.ops.render.render(write_still=True)
    temporary.replace(path)


def diagnostics(rig,result,folder):
    """SVG overlay kept separate from clean algorithm input images."""
    colors={'visible':'#42d98b','overlap':'#36bbff','occluded':'#ff565f'}
    for record in result.pop('diagnostic_points',[]):
        name=record['camera']
        svg=['<svg xmlns="http://www.w3.org/2000/svg" width="960" height="600" viewBox="0 0 960 600">',
             '<rect width="960" height="600" fill="#15202a"/>',
             f'<image href="{name}.png" x="0" y="0" width="960" height="540"/>']
        for p in record['points']:
            x=(p['u']+1)*480;y=(1-p['v'])*270
            svg.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="1.5" fill="{colors[p["status"]]}"/>')
        svg.append(f'<text x="16" y="565" fill="white" font-family="sans-serif" font-size="16">{name} | angle {result["azimuth_deg"]} deg | pitch {result["pitch_deg"]} deg</text>')
        svg.append('<text x="16" y="588" fill="white" font-family="sans-serif" font-size="14">Green: visible | Blue: common visible surface | Red: blocked | sample test only</text></svg>')
        (folder/(name+'-diagnostic.svg')).write_text('\n'.join(svg))
    write_json(folder/'coverage.json',result)


def encode(folder,video,fps,count,width):
    ffmpeg=shutil.which('ffmpeg')
    ffprobe=shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise RuntimeError('ffmpeg and ffprobe are required for verified video output')
    video.parent.mkdir(parents=True,exist_ok=True)
    tmp=video.with_name(video.stem+'.partial.mp4')
    command=[ffmpeg,'-hide_banner','-loglevel','error','-y','-framerate',str(fps),
             '-start_number','0','-i',str(folder/'%06d.png'),'-frames:v',str(count),
             '-an','-c:v','libx264','-preset','medium','-crf','15','-pix_fmt','yuv420p',
             '-movflags','+faststart',str(tmp)]
    subprocess.run(command,check=True)
    data=json.loads(subprocess.check_output([ffprobe,'-v','error','-select_streams','v:0',
          '-count_frames','-show_streams','-show_format','-of','json',str(tmp)],text=True))
    stream=data['streams'][0]
    expected_height=round(width*9/16)
    assert int(stream['nb_read_frames'])==count, 'Encoded frame count mismatch'
    assert (stream['width'],stream['height'])==(width,expected_height), 'Resolution mismatch'
    num,den=map(int,stream['avg_frame_rate'].split('/'))
    assert abs(num/den-fps)<1e-9, 'FPS mismatch'
    assert abs(float(stream['duration'])-count/fps)<1/fps, 'Duration mismatch'
    tmp.replace(video)
    return dict(file=video.name,sha256=digest(video),ffprobe=data,ffmpeg_command=command)


def export_frames_csv(path,times,m):
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['frame_index','time_s','rpm','pitch_deg','azimuth_deg','phase'])
        writer.writeheader()
        for i,t in enumerate(times):
            writer.writerow(dict(frame_index=i,**motion_at(t,m)))


def main(argv):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=['prepare','preflight','audit','stills','clip','full'],default='audit')
    p.add_argument('--output',required=True)
    p.add_argument('--width',type=int,default=3840)
    p.add_argument('--samples',type=int,default=16,help='Cycles samples; Eevee uses fixed 64 temporal samples')
    p.add_argument('--engine',choices=['eevee','cycles'],default='eevee')
    p.add_argument('--start-s',type=float,default=100.)
    p.add_argument('--duration-s',type=float,default=2.5)
    p.add_argument('--max-frames',type=int,help='Explicit partial batch; no MP4/completion claim until all frames exist')
    p.add_argument('--preview-approved',action='store_true',help='Use only after user/friend approves trial inputs')
    p.add_argument('--render-authorized',action='store_true',help='Explicit operator confirmation that the user has authorized new image/video rendering')
    args=p.parse_args(argv)
    if not bpy.app.background:
        raise RuntimeError('Use a separate background Blender process to protect open scenes')
    if args.mode in ('stills','clip','full') and not args.render_authorized:
        raise ValueError('Rendering is disabled. New images/video require explicit user authorization and --render-authorized')
    if args.width<64 or args.width%32 or args.samples<1:
        raise ValueError('Width must be >=64 and divisible by 32; samples must be positive')
    if args.max_frames is not None and args.max_frames<1:
        raise ValueError('max-frames must be positive')
    if args.mode=='full' and not args.preview_approved:
        raise ValueError('Plan stage E requires review of stage C; supply --preview-approved only after review')
    output=Path(args.output).resolve()
    output.mkdir(parents=True,exist_ok=True)
    g,m,c=read_config()
    if args.mode=='full':
        args.start_s,args.duration_s=0.,boundaries(m)[-1]
    times=frame_times(args.start_s,args.duration_s,m['fps'])
    if times[-1]>boundaries(m)[-1] or args.start_s+args.duration_s>boundaries(m)[-1]+1e-9:
        raise ValueError('Requested clip exceeds timeline')
    sig=signature(args,(g,m,c))
    lock=output/'job.json'
    if lock.exists() and json.loads(lock.read_text())!=sig:
        raise ValueError('Output belongs to another configuration/code version; choose a new directory')
    if not lock.exists() and any(output.iterdir()):
        raise ValueError('Output directory is not empty and has no job lock')
    write_json(lock,sig)
    started=time.monotonic()
    write_json(output/'status.json',dict(state='running',mode=args.mode))
    try:
        rig=build(g,m,c,args.width,args.samples)
        if args.engine=='eevee':
            try:
                rig['scene'].render.engine='BLENDER_EEVEE_NEXT'
            except TypeError:
                rig['scene'].render.engine='BLENDER_EEVEE'
        write_json(output/'metadata/cameras.json',camera_metadata(rig))
        write_json(output/'metadata/geometry.json',dict(config=g,stations=rig['stations']))
        write_json(output/'metadata/defects.json',rig['defects'])
        write_json(output/'metadata/motion.json',m)
        write_json(output/'metadata/assumptions.json',dict(geometry=g['assumptions'],cameras=c['assumptions'],
            surface='ordinary blade material with requested recessed Blade 1 crack and Blade 2 pit; Blade 3 intact; no labels/overlays',
            source='DTU scalar/prebend shape reference, pinned FFA contours with root/tip adaptations, GW184 target dimensions; synthetic rigid animation'))
        (output/'metadata').mkdir(exist_ok=True)
        export_frames_csv(output/'metadata/full-timeline.csv',frame_times(0,boundaries(m)[-1],m['fps']),m)
        if args.mode=='preflight':
            from .preflight import run
            run(rig,output,write_json)
        elif args.mode=='prepare':
            bake(rig)
            bpy.ops.wm.save_as_mainfile(filepath=str(output/'GW184-single.blend'))
        elif args.mode=='audit':
            report=sweep(rig)
            write_json(output/'coverage.json',report)
            pose(rig,0,0)
            bpy.ops.wm.save_as_mainfile(filepath=str(output/'GW184-reference.blend'))
        elif args.mode=='stills':
            for pitch in (0,45,90):
                for angle in (-10,0,10):
                    folder=output/'preview'/f'pitch-{pitch}_angle-{angle}'
                    result=audit_pose(rig,angle,pitch,True)
                    for camera in rig['cameras']:
                        render(rig,camera,folder/(camera.name+'.png'))
                    diagnostics(rig,result,folder)
        else:
            export_frames_csv(output/'metadata/frames.csv',times,m)
            limit=args.max_frames or len(times)
            rendered=0
            for i,t in enumerate(times):
                files=[output/'frames'/camera.name/f'{i:06d}.png' for camera in rig['cameras']]
                if all(path.exists() for path in files):
                    continue
                if rendered>=limit:
                    break
                at_time(rig,t)
                for camera,path in zip(rig['cameras'],files):
                    render(rig,camera,path)
                rendered+=1
                if i%10==0:
                    write_json(output/'status.json',dict(state='rendering',last_frame_index=i,total_frames=len(times)))
                    print(f'GW184 synchronized frame {i+1}/{len(times)}',flush=True)
            counts={camera.name:sum((output/'frames'/camera.name/f'{i:06d}.png').exists() for i in range(len(times)))
                    for camera in rig['cameras']}
            if any(count!=len(times) for count in counts.values()):
                write_json(output/'status.json',dict(state='partial',counts=counts,total_frames=len(times)))
                return
            videos=[encode(output/'frames'/cam.name,output/'videos'/(cam.name+'.mp4'),m['fps'],len(times),args.width)
                    for cam in rig['cameras']]
            write_json(output/'metadata/video-verification.json',dict(videos=videos,
                       timing='All camera images at each index rendered from one shared deterministic pose; no per-camera clock',
                       frame_count=len(times),fps=m['fps'],duration_s=len(times)/m['fps'],
                       resolution=[args.width,round(args.width*9/16)],full_resolution=args.width==3840))
        write_json(output/'status.json',dict(state='complete',mode=args.mode,elapsed_s=time.monotonic()-started,
            input_acceptance='UNREVIEWED; geometric/video checks do not establish stitching success'))
    except BaseException as exc:
        write_json(output/'status.json',dict(state='failed',mode=args.mode,error=repr(exc)))
        raise
