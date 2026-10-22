"""Synthetic-only renderer audit: no video replay, truth, or source geometry."""
from pathlib import Path
import hashlib
import json

import numpy as np
import torch

from wfrl.nrel_reconstruction.renderer import soft_silhouette, silhouette_loss
from wfrl.nrel_reconstruction.diagnostics import hard_silhouette


def square_grid(n):
    vertices = [[16.+32.*x/n,16.+32.*y/n,1.] for y in range(n+1) for x in range(n+1)]
    faces=[]
    for y in range(n):
        for x in range(n):
            a=y*(n+1)+x;b=a+1;c=a+n+1;d=c+1
            faces.extend([[a,b,d],[a,d,c]])
    return torch.tensor(vertices,dtype=torch.float64),torch.tensor(faces,dtype=torch.long)


def measurements(vertices,faces,labels):
    eye=torch.eye(4,dtype=torch.float64);k=torch.eye(3,dtype=torch.float64)
    with torch.no_grad():
        prediction,distance=soft_silhouette(vertices,faces,k,eye,eye,(64,64),return_distance=True)
        loss,terms=silhouette_loss(prediction,labels)
    hard=hard_silhouette(vertices.numpy(),faces.numpy(),k.numpy(),eye.numpy(),eye.numpy(),(64,64))
    fg=labels.numpy()==1; certain=labels.numpy()!=2;union=(hard|fg)&certain
    threshold=prediction.numpy()>=.5;soft_union=(threshold|fg)&certain
    inner=np.zeros((64,64),bool);inner[22:43,22:43]=True
    xx,yy=np.meshgrid(np.arange(64),np.arange(64));outside=~hard
    euclidean=np.sqrt(np.maximum(np.maximum(16-xx,xx-48),0)**2+np.maximum(np.maximum(16-yy,yy-48),0)**2)
    return {'faces':len(faces),'hard_masked_iou':float((hard&fg).sum()/union.sum()),
            'soft_threshold_masked_iou':float((threshold&fg).sum()/soft_union.sum()),
            'soft_loss':float(loss),'foreground_loss':float(terms['foreground']),
            'background_loss':float(terms['background']),
            'centre_occupancy':float(prediction[32,32]),'one_pixel_outside_occupancy':float(prediction[32,49]),
            'interior_occupancy_min':float(prediction[inner].min()),
            'interior_mean_missing_occupancy':float((1-prediction[inner]).mean()),
            'covered_foreground_distance_attraction':float(torch.relu(-distance[torch.as_tensor(fg)]).mean()),
            'interior_distance_zero_fraction':float((distance[inner].abs()<1e-9).double().mean()),
            'outside_distance_rmse_against_euclidean':float(np.sqrt(np.mean((-distance.numpy()[outside]-euclidean[outside])**2)))}


def audit_renderer():
    torch.set_num_threads(2)
    labels=torch.zeros((64,64),dtype=torch.uint8);labels[16:49,16:49]=1
    grids=[]
    for n in [1,2,4,8]:
        v,f=square_grid(n);grids.append({'subdivisions_per_side':n,**measurements(v,f,labels)})
    v,f=square_grid(1)
    duplicates=[{'face_copy_count':n,**measurements(v,f.repeat(n,1),labels)} for n in [1,2,4,8]]
    eye=torch.eye(4,dtype=torch.float64);k=torch.eye(3,dtype=torch.float64)
    fan=[]
    for x,y in [(32.,32.),(28.,37.),(27.,37.),(29.,37.)]:
        centre=torch.tensor([x,y],dtype=torch.float64,requires_grad=True)
        vertices=torch.cat([torch.tensor([[16.,16.,1.],[48.,16.,1.],[48.,48.,1.],[16.,48.,1.]],dtype=torch.float64),
                            torch.cat([centre,centre.new_ones(1)])[None]])
        faces=torch.tensor([[0,1,4],[1,2,4],[2,3,4],[3,0,4]])
        pred,distance=soft_silhouette(vertices,faces,k,eye,eye,(64,64),return_distance=True)
        loss,_=silhouette_loss(pred,labels);loss.backward()
        hard=hard_silhouette(vertices.detach().numpy(),faces.numpy(),k.numpy(),eye.numpy(),eye.numpy(),(64,64))
        fan.append({'interior_vertex_xy':[x,y],'loss':float(loss.detach()),
                    'interior_vertex_loss_gradient':centre.grad.tolist(),
                    'hard_masked_iou':float((hard&(labels.numpy()==1)).sum()/(hard|(labels.numpy()==1)).sum()),
                    'foreground_distance_attraction':float(torch.relu(-distance[labels==1]).mean().detach())})
    import wfrl.nrel_reconstruction.renderer as renderer
    return {'scope':'synthetic planar square only; no evaluation-only, replay, source geometry or truth read',
            'renderer_file':renderer.__file__,'renderer_sha256':hashlib.sha256(Path(renderer.__file__).read_bytes()).hexdigest(),
            'torch_version':torch.__version__,'resolution':[64,64],'sigma_px':.65,
            'same_external_silhouette_different_tessellation':grids,
            'same_external_silhouette_face_multiplicity':duplicates,
            'interior_vertex_motion_external_boundary_fixed':fan,
            'findings':[
                'Probabilistic triangle union depends on subdivision, face multiplicity and internal edges even when the hard union silhouette is identical.',
                'max(min signed edge distances) is zero on shared internal edges; relu(-distance) is zero for covered foreground, so this is not an inside-edge attraction penalty.',
                'Triangle half-plane distance outside corners is not Euclidean distance to the union boundary.',
                'These tests establish nonphysical optimization pressure from rasterization; they do not by themselves attribute a real centreline wiggle to this backend.'],
            'minimal_correction':'Use projected exterior/visible silhouette edges with bidirectional robust RGB contour distance; detach topology and visibility choices. Keep the old renderer available as a recorded baseline. Replacing union by max(sigmoid) alone does not remove internal-edge seams.'}


def test_same_hard_silhouette_is_not_same_soft_loss():
    report=audit_renderer()
    grids=report['same_external_silhouette_different_tessellation']
    assert all(row['hard_masked_iou']==1. for row in grids)
    assert max(row['soft_loss'] for row in grids)-min(row['soft_loss'] for row in grids)>.001
    assert all(row['covered_foreground_distance_attraction']<1e-9 for row in grids)
    assert np.linalg.norm(report['interior_vertex_motion_external_boundary_fixed'][1]['interior_vertex_loss_gradient'])>1e-6


if __name__=='__main__':
    output=Path('outputs/nrel-video-single-blade/stages/02-second-run/renderer-audit.json')
    output.parent.mkdir(parents=True,exist_ok=True)
    report=audit_renderer();output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
