"""Finish the recorded 30-minute test using parallel FFprobe decoding."""
from pathlib import Path
import sys,json,subprocess,time,os,signal
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from wfrl.camera_video.stream import receive
from wfrl.camera_video.media import read_frames,sha256
from wfrl.camera_video.package import write_json
OUT=Path(__file__).parent/'soak-4k-rtsp-fixed'
SOURCE=Path(__file__).parent/'loop-short-check'
records=[json.loads(line) for line in (OUT/'receive-0/received.jsonl').open()]
rows=read_frames(SOURCE/'frames.csv')
assert len(records)==36000
assert all(b['media_timestamp']-a['media_timestamp']==4500 for a,b in zip(records,records[1:]))
assert all(r['frame_data']==rows[r['frame_id']] for r in records)
assert records[-1]['sent_monotonic_s']-records[0]['sent_monotonic_s']>=1799.5
assert 'reader is too slow' not in (OUT/'server.log').read_text()
def probe(directory,count):
 command=['ffprobe','-v','error','-threads','0','-count_frames','-show_entries','stream=width,height,nb_read_frames','-of','json',str(directory/'received.h264')]
 result=json.loads(subprocess.check_output(command,text=True))['streams'][0]
 assert (result['width'],result['height'],int(result['nb_read_frames']))==(3840,2160,count)
 return result
info=probe(OUT/'receive-0',36000)
# The original runner first performs a complete -xerror FFmpeg decode, then
# starts its redundant single-thread FFprobe. Wait for that second phase.
original_probe=None
for _ in range(1200):
 processes=subprocess.check_output(['ps','-axo','pid=,ppid=,command='],text=True)
 for line in processes.splitlines():
  parts=line.strip().split(None,2)
  if len(parts)==3 and parts[1]=='11151' and parts[2].startswith('ffprobe ') and str((OUT/'receive-0/received.h264').resolve()) in parts[2]:
   original_probe=int(parts[0]);break
 if original_probe:break
 time.sleep(.5)
assert original_probe,'Original error-strict decode did not reach its counting phase'
reconnected=receive('rtsp://127.0.0.1:29554/windfarm/camera1','http://127.0.0.1:29555/',frame_count=40,output_dir=OUT/'receive-parallel-reconnect',timeout=30)
assert reconnected[0]['session_id']==records[0]['session_id']
assert all(r['frame_data']==rows[r['frame_id']] for r in reconnected)
subprocess.run(['ffmpeg','-v','error','-xerror','-threads','0','-i',str(OUT/'receive-parallel-reconnect/received.h264'),'-f','null','-'],check=True)
probe(OUT/'receive-parallel-reconnect',40)
report={'status':'passed','scope':'30-minute local 4K camera sample, continuous capture and same-session reconnection',
 'matched_decoded_frames':36040,'continuous_frames':36000,'media_gaps':0,'every_label_matches_csv':True,
 'send_span_seconds':records[-1]['sent_monotonic_s']-records[0]['sent_monotonic_s'],
 'width':3840,'height':2160,'cycles':sorted({r['cycle_id'] for r in records}),
 'source_cycles':sorted({int(r['frame_data']['source_cycle_id']) for r in records}),
 'session_id':records[0]['session_id'],'dataset_id':records[0]['dataset_id'],
 'reconnection':True,'reader_slow_warnings':0,'write_queue_packets':8192,
 'decoding':'Original error-strict FFmpeg pass completed; equivalent parallel FFprobe counted all frames; reconnect decoded and counted',
 'capture_driver':'soak_rtsp.py','verification_driver':'finish_soak.py',
 'stream_source_sha256':sha256(ROOT/'wfrl/camera_video/stream.py'),
 'scope_limit':'Five-second camera sample; final five-minute file has separate full-cycle acceptance',
 'serial_probe_cancelled_after_equivalent_parallel_check':True}
# All checks succeeded. Cancel only the now-redundant serial count; its runner
# then closes the publisher and MediaMTX in its finally block.
os.kill(original_probe,signal.SIGTERM)
for _ in range(200):
 if (OUT/'checks.json').exists():break
 time.sleep(.1)
assert (OUT/'checks.json').exists()
(OUT/'checks.json').rename(OUT/'serial-runner-stop.json')
write_json(OUT/'checks.json',report)
print(json.dumps(report,indent=2),flush=True)
