import subprocess,os,time,signal,socket,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).parent;os.chdir(ROOT)
env=os.environ.copy();env.update(WFCRL_FASTFARM_EXECUTABLE='/opt/anaconda3/envs/wfrl-mac/bin/FAST.Farm',WFCRL_MPIEXEC='/opt/homebrew/bin/mpiexec',OMP_NUM_THREADS='2',PYTHONUNBUFFERED='1')
children=[];start=time.monotonic();result={}
try:
 log=(OUT/'backend.log').open('w');bridge=subprocess.Popen(['/opt/homebrew/bin/mpiexec','-n','1','/opt/anaconda3/envs/wfrl-mac/bin/python','-m','wfrl.blender_bridge','--port','8879'],env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True);children.append(bridge)
 for _ in range(100):
  if bridge.poll() is not None:raise RuntimeError('Bridge exited during startup')
  try:
   with socket.create_connection(('127.0.0.1',8879),.1):break
  except OSError:time.sleep(.1)
 else:raise RuntimeError('Bridge did not start')
 frontlog=(OUT/'blender.log').open('w');front=subprocess.Popen(['/Applications/Blender.app/Contents/MacOS/Blender','--factory-startup','--python-exit-code','1','--python',str(OUT/'blender_run.py')],stdout=frontlog,stderr=subprocess.STDOUT,start_new_session=True);children.append(front)
 while front.poll() is None and time.monotonic()-start<540:time.sleep(.5)
 result['frontend_exit']=front.poll();result['hard_deadline_reached']=front.poll() is None
finally:
 for process in reversed(children):
  if process.poll() is None:
   os.killpg(process.pid,signal.SIGTERM)
   try:process.wait(timeout=10)
   except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
 result['elapsed_seconds']=time.monotonic()-start;result['owned_processes_exited']=all(p.poll() is not None for p in children)
 (OUT/'supervision.json').write_text(json.dumps(result,indent=2));print(result,flush=True)
