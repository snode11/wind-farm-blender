"""RGB-only masks; no MAPPO geometry, poses, or evaluation labels read here."""
import hashlib,json,sys,time
from pathlib import Path
import cv2,numpy as np
sys.path.insert(0,'/Users/eason/Desktop/wfcrl/blade_recon')
import recon

P=Path(__file__).resolve().parents[1];I=P/'input';O=I/'masks'
for name in ('T1Down','NacelleT1'):
    (O/name).mkdir(parents=True,exist_ok=True)
    files=sorted((I/name).glob('*.png'))
    assert len(files)==601
    background_files=files[::10]
    background=np.median(np.stack([cv2.imread(str(f)) for f in background_files]),axis=0).astype(np.uint8)
    cv2.imwrite(str(I/(name+'_median_background.png')),background)
    stats=[]
    writer=cv2.VideoWriter(str(I/(name+'.mp4')),cv2.VideoWriter_fourcc(*'mp4v'),10,(960,540))
    assert writer.isOpened()
    start=time.time()
    for f in files:
        img=cv2.imread(str(f));writer.write(img)
        channel_range=img.max(2).astype(float)-img.min(2)
        difference=np.max(np.abs(img.astype(float)-background),axis=2)
        neutral=(channel_range<10)&(img.max(2)>25)
        # The existing blade-tip paint is red. Include it from RGB so the
        # striped tip is not cut off from the observed silhouette.
        red=(img[:,:,2]>45)&(img[:,:,2]>1.4*img[:,:,1])&(img[:,:,2]>1.4*img[:,:,0])
        m=((neutral|red)&(difference>18)).astype(np.uint8)*255
        n,lab,cc,_=cv2.connectedComponentsWithStats(m)
        keep=np.zeros(n,bool);keep[1:]=cc[1:,cv2.CC_STAT_AREA]>100
        m=(keep[lab]*255).astype(np.uint8)
        cv2.imwrite(str(O/name/f.name),m)
        o=recon.Obs(m)
        stats.append(dict(frame=int(f.stem),pixels=int((m>0).sum()),contour_points=len(o.pts),empty=not bool(o.has),
                          border_pixels=int(np.count_nonzero(np.r_[m[0],m[-1],m[:,0],m[:,-1]])),sha256=hashlib.sha256((O/name/f.name).read_bytes()).hexdigest()))
    writer.release()
    manifest=dict(method='RGB temporal median background subtraction + neutral paint',
                  uses=['RGB frames only'],does_not_read=['evaluation','object IDs','MAPPO poses','MAPPO flexible geometry'],
                  background_frames=[int(f.stem) for f in background_files],
                  neutral_range_lt=10,difference_gt=18,value_gt=25,connected_area_gt=100,
                  red_paint='R>45 and R>1.4*G and R>1.4*B, with the same motion difference gate',
                  morphology='none',note='Preserves foreground truncation and all occlusion. No hand-drawn spatial ROI.',
                  resolution=[960,540],fps=10,frames=stats,elapsed_s=time.time()-start)
    (I/(name+'_mask_manifest.json')).write_text(json.dumps(manifest,indent=1))
    print('MASKS_READY',name,len(stats),'empty',sum(s['empty'] for s in stats),'range',min(s['pixels'] for s in stats),max(s['pixels'] for s in stats),flush=True)
