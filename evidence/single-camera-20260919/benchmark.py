"""Sequential 4K comparisons; never share the GPU between benchmark runs."""
from pathlib import Path
import json, subprocess, time
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).parent
configs=[('eevee-16x2-none',['--engine','eevee','--samples','16','--exposure-samples','2','--exr-codec','NONE']),
('eevee-32x8-none',['--engine','eevee','--samples','32','--exposure-samples','8','--exr-codec','NONE']),
('cycles-8x2-none',['--engine','cycles','--device','metal','--samples','8','--exposure-samples','2','--exr-codec','NONE']),
('eevee-16x2-zip',['--engine','eevee','--samples','16','--exposure-samples','2','--exr-codec','ZIP']),
('farm-eevee-16x2-none',['--scene-scope','farm','--engine','eevee','--samples','16','--exposure-samples','2','--exr-codec','NONE'])]
results=[]
for name,opts in configs:
    for start in (117.7,143.2,176.7):
        dest=OUT/(name+'-'+str(start))
        command=['/Applications/Blender.app/Contents/MacOS/Blender','--background','--factory-startup','--python-exit-code','1','--python',str(ROOT/'scripts/blender/render_camera_video.py'),'--','--output',str(dest),'--start-s',str(start),'--end-s',str(start+.05)]+opts
        begun=time.monotonic()
        with dest.with_suffix('.log').open('w') as log:
            subprocess.run(command,cwd=ROOT,stdout=log,stderr=log,check=True)
        record=json.loads((dest/'checks/render_frames.json').read_text())['0']
        results.append(dict(configuration=name,start_s=start,directory=str(dest.resolve()),process_seconds=time.monotonic()-begun,**record))
        (OUT/'benchmark.json').write_text(json.dumps(results,indent=2)+'\n')
        print(name,start,record['seconds'],flush=True)
