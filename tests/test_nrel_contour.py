"""Synthetic contour backend tests. Never read replay, source mesh or truth."""
import numpy as np
import torch

from wfrl.nrel_reconstruction.contour import (
    contour_backend_check,projected_contour_candidates,projected_contour_loss)
from wfrl.nrel_reconstruction.diagnostics import hard_silhouette,_boundary


def cube():
    vertices=torch.tensor([[-1.,-1.,4.],[1.,-1.,4.],[1.,1.,4.],[-1.,1.,4.],
                           [-1.,-1.,6.],[1.,-1.,6.],[1.,1.,6.],[-1.,1.,6.]],dtype=torch.float64)
    faces=torch.tensor([[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],
                        [1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]])
    return vertices,faces


def inputs():
    k=torch.tensor([[40.,0.,31.5],[0.,40.,31.5],[0.,0.,1.]],dtype=torch.float64)
    eye=torch.eye(4,dtype=torch.float64)
    labels=torch.zeros((64,64),dtype=torch.uint8)
    a=torch.linspace(21.5,41.5,50,dtype=torch.float64)
    points=torch.cat([torch.stack((a,torch.full_like(a,21.5)),dim=-1),
                      torch.stack((a,torch.full_like(a,41.5)),dim=-1),
                      torch.stack((torch.full_like(a,21.5),a),dim=-1),
                      torch.stack((torch.full_like(a,41.5),a),dim=-1)])
    return k,eye,labels,points


def test_contour_actual_backward_matches_finite_difference():
    torch.set_num_threads(2)
    check=contour_backend_check()
    assert check['passed'],check
    assert check['relative_error']<.005


def test_interior_triangulation_has_no_contour_gradient():
    v,f=cube();k,eye,labels,observed=inputs()
    centre=torch.tensor([.27,-.31,4.],dtype=torch.float64,requires_grad=True)
    fan_v=torch.cat((v,centre[None]))
    fan_f=torch.cat((torch.tensor([[0,8,1],[1,8,2],[2,8,3],[3,8,0]]),f[2:]))
    loss,terms=projected_contour_loss(fan_v,fan_f,k,eye,eye,(64,64),labels,observed)
    loss.backward()
    assert torch.equal(centre.grad,torch.zeros_like(centre))
    loss0,terms0=projected_contour_loss(v,f,k,eye,eye,(64,64),labels,observed)
    assert torch.allclose(loss,loss0,atol=1e-12)
    assert int(terms['silhouette_edge_count'])==int(terms0['silhouette_edge_count'])==4


def test_independent_mask_preserves_uncertain_contour_ring():
    v,f=cube();k,eye,labels,observed=inputs();labels.fill_(2)
    zero,missing=projected_contour_loss(v,f,k,eye,eye,(64,64),labels,observed)
    assert int(missing['contour_terms_available'])==0 and float(zero)==0.
    loss,present=projected_contour_loss(v,f,k,eye,eye,(64,64),labels,observed,
                                      torch.ones_like(labels,dtype=torch.bool))
    assert int(present['contour_terms_available'])==1 and torch.isfinite(loss)


def test_occlusion_mask_segments_do_not_bridge_invalid_gap():
    v,f=cube();k,eye,labels,_=inputs();valid=torch.ones_like(labels,dtype=torch.bool);valid[:,30:34]=False
    points,segments,counts=projected_contour_candidates(v,f,k,eye,eye,(64,64),labels,valid)
    assert counts['candidate_mask_rejected']>0 and len(points)>0
    for segment in segments.detach():
        midpoint=segment.mean(dim=0).round().long()
        assert bool(valid[midpoint[1],midpoint[0]])


def test_self_hidden_inner_cube_contours_are_rejected():
    v,f=cube();k,eye,labels,observed=inputs()
    inner=v.clone();inner[:,:2]*=.25;inner[:,2]=(inner[:,2]-4)*.25+4.5
    combined=torch.cat((v,inner));faces=torch.cat((f,f+8))
    points,segments,counts=projected_contour_candidates(combined,faces,k,eye,eye,(64,64),labels)
    original,_,base=projected_contour_candidates(v,f,k,eye,eye,(64,64),labels)
    assert counts['candidate_self_hidden_rejected']>0
    assert len(points)==len(original) and len(segments)==4
    loss,_=projected_contour_loss(combined,faces,k,eye,eye,(64,64),labels,observed)
    base_loss,_=projected_contour_loss(v,f,k,eye,eye,(64,64),labels,observed)
    assert torch.allclose(loss,base_loss,atol=1e-12)


def test_uncertainty_deadband_and_missing_behind_near_are_explicit():
    v,f=cube();k,eye,labels,observed=inputs()
    shifted=v+torch.tensor([.02,.01,0.],dtype=v.dtype)
    loss,terms=projected_contour_loss(shifted,f,k,eye,eye,(64,64),labels,observed,uncertainty_px=1.)
    assert float(loss)==0.
    behind=v.clone();behind[:,2]*=-1
    missing,terms=projected_contour_loss(behind,f,k,eye,eye,(64,64),labels,observed)
    assert float(missing)==0. and int(terms['contour_terms_available'])==0


def test_mildly_bent_closed_beam_matches_hard_boundary():
    vertices=[];faces=[];rings=10;around=16
    for i,z in enumerate(np.linspace(0,5,rings)):
        for a in np.linspace(0,2*np.pi,around,endpoint=False):
            vertices.append([.25*np.cos(a)+.05*(z/5)**2,.5*np.sin(a)+.15*(z/5)**2,z])
    for i in range(rings-1):
        for j in range(around):
            a=i*around+j;b=i*around+(j+1)%around;c=a+around;d=b+around
            faces.extend([[a,b,d],[a,d,c]])
    for offset,reverse in [(0,True),((rings-1)*around,False)]:
        for j in range(1,around-1):
            face=[offset,offset+j,offset+j+1];faces.append(face[::-1] if reverse else face)
    v=torch.tensor(vertices,dtype=torch.float64);f=torch.tensor(faces)
    camera=torch.tensor([[0.,0.,1.,-2.5],[0.,1.,0.,0.],[-1.,0.,0.,8.],[0.,0.,0.,1.]],dtype=torch.float64)
    k=torch.tensor([[120.,0.,63.5],[0.,120.,63.5],[0.,0.,1.]],dtype=torch.float64);eye=torch.eye(4,dtype=torch.float64)
    hard=hard_silhouette(v.numpy(),f.numpy(),k.numpy(),camera.numpy(),eye.numpy(),(128,128))
    y,x=np.where(_boundary(hard));observed=torch.tensor(np.stack((x,y),axis=-1),dtype=v.dtype)
    loss,terms=projected_contour_loss(v,f,k,camera,eye,(128,128),torch.tensor(hard.astype(np.uint8)),observed,
                                      uncertainty_px=1.)
    assert int(terms['candidate_count'])>20 and torch.isfinite(loss)
    assert float(terms['observed_to_candidate_mean_px'])<1.5
