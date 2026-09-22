"""Run the accepted single-turbine render, file verification and transport check."""
from pathlib import Path
from datetime import datetime,timezone
import subprocess,json,time,os,sys,traceback
ROOT=Path(__file__).resolve().parents[2]
E=Path(__file__).parent
OUT=ROOT/'results/camera-video/t1-single-4k-20fps-20260919'
FINAL=ROOT/'results/camera-video/t1-single-5min-4k-20fps-20260919'
STATUS=E/'full-status.json'
record={'status':'starting','pid':os.getpid(),'process_group_id':os.getpgrp(),'output':str(OUT),'expected_frames':1200,'final_output':str(FINAL),'final_expected_frames':6000,'final_duration_s':300,'started_utc':datetime.now(timezone.utc).isoformat()}
started=time.monotonic()
def save(stage):
 record.update(status=stage,updated_utc=datetime.now(timezone.utc).isoformat(),elapsed_seconds=time.monotonic()-started)
 tmp=STATUS.with_suffix('.tmp');tmp.write_text(json.dumps(record,indent=2)+'\n');tmp.replace(STATUS)
def main():
 try:
  save('rendering')
  command=[sys.executable,str(ROOT/'scripts/camera_video.py'),'build','--output',str(OUT),'--resolution','4k','--fps','20','--engine','eevee','--scene-scope','single','--samples','16','--exposure-samples','2','--exr-codec','ZIP']
  record['command']=command;save('rendering')
  subprocess.run(command,cwd=ROOT,check=True)
  save('verifying')
  subprocess.run([sys.executable,str(ROOT/'scripts/camera_video.py'),'verify',str(OUT)],cwd=ROOT,check=True)
  record['verification']=json.loads((OUT/'checks/package-verification.json').read_text())
  save('building_five_minute_delivery')
  subprocess.run([sys.executable,str(ROOT/'scripts/camera_video.py'),'loop',str(OUT),'--output',str(FINAL),'--repeats','5'],cwd=ROOT,check=True)
  record['final_verification']=json.loads((FINAL/'checks/package-verification.json').read_text())
  assert record['final_verification']['frame_count']==6000 and record['final_verification']['duration_s']==300
  save('five_minute_rtsp_check')
  subprocess.run([sys.executable,str(E/'check_rtsp.py'),str(FINAL),str(E/'five-minute-rtsp')],cwd=ROOT,check=True)
  record['rtsp']=json.loads((E/'five-minute-rtsp/checks.json').read_text())
  assert record['rtsp']['status']=='passed' and record['rtsp']['cycle_boundary']
  save('complete')
 except BaseException:
  record['error']=traceback.format_exc();save('failed');raise
if __name__=='__main__':main()
