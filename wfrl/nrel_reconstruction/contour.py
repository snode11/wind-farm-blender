"""Differentiable projected exterior contour distances, independent of truth.

Adjacent front/back face signs select silhouette edges. Topology, hard triangle
union visibility probes, image/mask validity and sampling decisions are detached;
gradients flow through the selected projected points and segments. The intended
domain is an oriented closed convex or mildly bent blade near the reference pose.
This is a local, piecewise differentiable contour fit, not a global renderer.

An independent reviewed candidate_valid_mask overrides FBU's uncertain contour
ring. Without it candidates must lie in F/B. uncertainty_px is in *this* image's
pixels: callers must scale the RGB annotation uncertainty when resizing.
"""
from __future__ import annotations

import math
import torch
from torch.nn import functional as F

from .renderer import _clip_near


def _silhouette_edges(camera, faces):
    triangles = camera[faces]
    normals = torch.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0], dim=-1)
    facing = (normals*triangles.mean(dim=1)).sum(dim=-1).detach()
    valid = (normals.square().sum(dim=-1).detach()>1e-18)
    adjacency = {}
    for i, face in enumerate(faces.detach().cpu().tolist()):
        if not bool(valid[i]):
            continue
        for j in range(3):
            edge = tuple(sorted((face[j],face[(j+1)%3])))
            adjacency.setdefault(edge, []).append(i)
    selected = []
    for edge, neighbors in adjacency.items():
        signs = [bool(facing[i]<0) for i in neighbors]
        # Open boundary edges are supported, but closed oriented meshes remain
        # the intended input. Nonmanifold/misoriented meshes are not certified.
        if len(signs)==1 or (any(signs) and not all(signs)):
            selected.append(edge)
    return selected


def _clip_projected_segment(segment, width, height):
    """Liang-Barsky image clipping; retain derivatives within the chosen case."""
    a,b=segment
    lo,hi=a.new_zeros(()),a.new_ones(())
    for axis,bound in [(0,width-1),(1,height-1)]:
        delta=b[axis]-a[axis]
        if abs(float(delta.detach()))<1e-12:
            if float(a[axis].detach())<0 or float(a[axis].detach())>bound:
                return None
            continue
        t0,t1=-a[axis]/delta,(bound-a[axis])/delta
        if float(t0.detach())>float(t1.detach()):
            t0,t1=t1,t0
        if float(t0.detach())>float(lo.detach()):lo=t0
        if float(t1.detach())<float(hi.detach()):hi=t1
    if float(hi.detach())<float(lo.detach()):return None
    return torch.stack((a+(b-a)*lo,a+(b-a)*hi))


def _hard_union_at_points(points, triangles, point_chunk=256, face_chunk=96):
    """Detached exact projected triangle union at arbitrary subpixel probes."""
    covered=[]
    with torch.no_grad():
        triangles=triangles.detach();points=points.detach()
        for locations in points.split(point_chunk):
            occupied=torch.zeros(len(locations),device=points.device,dtype=torch.bool)
            for chunk in triangles.split(face_chunk):
                edge=torch.roll(chunk,shifts=-1,dims=1)-chunk
                area=edge[:,0,0]*(-edge[:,2,1])-edge[:,0,1]*(-edge[:,2,0])
                orientation=torch.where(area>=0,1.,-1.)
                relative=locations[None,None]-chunk[:,:,None]
                signed=(edge[:,:,0,None]*relative[...,1]-edge[:,:,1,None]*relative[...,0])*orientation[:,None,None]
                hit=(signed>=-1e-8).all(dim=1)&(area.abs()>1e-10)[:,None]
                occupied|=hit.any(dim=0)
            covered.append(occupied)
    return torch.cat(covered) if covered else torch.zeros(0,device=points.device,dtype=torch.bool)


def _mask_valid(points, validity, width, height):
    with torch.no_grad():
        points=points.detach()
        inside=((points[:,0]>=0)&(points[:,0]<=width-1)&(points[:,1]>=0)&(points[:,1]<=height-1)
                &torch.isfinite(points).all(dim=-1))
        x=points[:,0].round().long().clamp(0,width-1)
        y=points[:,1].round().long().clamp(0,height-1)
        return inside&validity[y,x]


def _limit(values, count):
    if len(values)<=count:return values
    indices=torch.linspace(0,len(values)-1,count,device=values.device).round().long()
    return values[indices]


def projected_contour_candidates(vertices, faces, K, T_camera_cv_from_world,
                                 T_world_from_blade_root, image_size, labels,
                                 candidate_valid_mask=None, max_candidates=300,
                                 sample_spacing_px=1.0, near_m=.01):
    """Return differentiable points, visible segment pieces, and scalar counts.

Self-hidden silhouette edges are filtered by union probes on opposite sides at
0.35, 0.75 and 1.5 pixels. Thin subpixel structures and abrupt visibility changes
remain limitations. Segments are retained only across contiguous valid samples,
so a segment never bridges an explicit U/occlusion-mask gap.
"""
    width,height=map(int,image_size)
    if width<=1 or height<=1 or max_candidates<1 or sample_spacing_px<=0:
        raise ValueError('Positive contour sampling budget and image dimensions required')
    if vertices.ndim!=2 or vertices.shape[1]!=3 or not vertices.is_floating_point():
        raise ValueError('vertices must be floating Nx3')
    faces=torch.as_tensor(faces,device=vertices.device,dtype=torch.long)
    K=torch.as_tensor(K,device=vertices.device,dtype=vertices.dtype)
    camera_transform=torch.as_tensor(T_camera_cv_from_world,device=vertices.device,dtype=vertices.dtype)
    root_transform=torch.as_tensor(T_world_from_blade_root,device=vertices.device,dtype=vertices.dtype)
    labels=torch.as_tensor(labels,device=vertices.device)
    if labels.shape!=(height,width) or not bool(((labels>=0)&(labels<=2)).all()):
        raise ValueError('labels must be HxW B=0/F=1/U=2')
    if candidate_valid_mask is None:
        validity=labels!=2
    else:
        validity=torch.as_tensor(candidate_valid_mask,device=vertices.device,dtype=torch.bool)
        if validity.shape!=(height,width):raise ValueError('candidate_valid_mask must match image')
    transform=camera_transform@root_transform
    camera=vertices@transform[:3,:3].T+transform[:3,3]
    edges=_silhouette_edges(camera,faces)
    with torch.no_grad():
        clipped_triangles=_clip_near(camera.detach()[faces],near_m)
        projected_triangles=clipped_triangles@K.T
        projected_triangles=projected_triangles[:,:,:2]/projected_triangles[:,:,2:3]
    points=[];segments=[];before_mask=0;after_mask=0;after_visibility=0
    for edge in edges:
        endpoints=camera[list(edge)]
        depth=endpoints[:,2].detach()
        if bool((depth<=near_m).all()):continue
        if not bool((depth>near_m).all()):
            front=0 if bool(depth[0]>near_m) else 1
            back=1-front
            fraction=(near_m-endpoints[back,2])/(endpoints[front,2]-endpoints[back,2])
            crossing=endpoints[back]+fraction*(endpoints[front]-endpoints[back])
            endpoints=torch.stack((endpoints[front],crossing))
        projected=endpoints@K.T
        projected=projected[:,:2]/projected[:,2:3]
        clipped=_clip_projected_segment(projected,width,height)
        if clipped is None:continue
        delta=clipped[1]-clipped[0]
        length=torch.linalg.vector_norm(delta)
        if float(length.detach())<1e-8:continue
        n=max(2,min(512,math.ceil(float(length.detach())/sample_spacing_px)+1))
        fraction=torch.linspace(0.,1.,n,device=vertices.device,dtype=vertices.dtype)
        locations=clipped[0]+fraction[:,None]*delta
        normal=torch.stack((-delta[1],delta[0]))/length
        valid=_mask_valid(locations,validity,width,height)
        before_mask+=n;after_mask+=int(valid.sum())
        probe_points=[]
        for radius in (.35,.75,1.5):
            probe_points.extend([locations+radius*normal,locations-radius*normal])
        occupancy=_hard_union_at_points(torch.cat(probe_points),projected_triangles).reshape(6,n)
        boundary=(occupancy[0]^occupancy[1])|(occupancy[2]^occupancy[3])|(occupancy[4]^occupancy[5])
        valid=valid&boundary
        after_visibility+=int(valid.sum())
        points.append(locations[valid])
        flags=valid.detach().cpu().tolist()
        begin=None
        for i,flag in enumerate(flags+[False]):
            if flag and begin is None:begin=i
            if not flag and begin is not None:
                segments.append(torch.stack((locations[begin],locations[i-1])))
                begin=None
    point_cloud=torch.cat(points) if points else vertices.new_empty((0,2))
    pieces=torch.stack(segments) if segments else vertices.new_empty((0,2,2))
    unbounded_points=len(point_cloud);unbounded_segments=len(pieces)
    point_cloud=_limit(point_cloud,max_candidates)
    pieces=_limit(pieces,max_candidates)
    counts={'silhouette_edge_count':len(edges),'candidate_samples_before_mask':before_mask,
            'candidate_samples_after_mask':after_mask,'candidate_samples_after_visibility':after_visibility,
            'candidate_count':len(point_cloud),'candidate_segment_count':len(pieces),
            'candidate_mask_rejected':before_mask-after_mask,
            'candidate_self_hidden_rejected':after_mask-after_visibility,
            'candidate_sampling_truncated':int(unbounded_points>max_candidates),
            'candidate_segments_truncated':int(unbounded_segments>max_candidates),
            'independent_candidate_mask':int(candidate_valid_mask is not None)}
    return point_cloud,pieces,counts


def _point_to_segments(points, segments):
    origin=segments[:,0]
    delta=segments[:,1]-origin
    relative=points[:,None]-origin[None]
    fraction=(relative*delta[None]).sum(dim=-1)/delta.square().sum(dim=-1).clamp_min(1e-16)[None]
    foot=origin[None]+fraction.clamp(0.,1.)[:,:,None]*delta[None]
    return torch.linalg.vector_norm(points[:,None]-foot,dim=-1).min(dim=1).values


def projected_contour_loss(vertices, faces, K, T_camera_cv_from_world,
                           T_world_from_blade_root, image_size, labels, observed_points,
                           candidate_valid_mask=None, uncertainty_px=0.0,
                           max_candidates=300, max_observed=600,
                           sample_spacing_px=1.0, near_m=.01,
                           robust_delta_px=2.0, distance_scale_px=10.0):
    """Mean bidirectional robust pixel distance to trusted RGB outline points.

Candidate->RGB uses sampled visible candidate points, and RGB->candidate uses
continuous segment pieces. The deadband is max(distance-uncertainty_px,0).
Each direction is normalized separately; U and outside-image are unconstrained.
Empty candidates/observations return a differentiable zero plus explicit counts;
the caller must treat these as missing image terms, not successful fitting.
All returned terms are scalar tensors, compatible with the existing optimizer's
JSON conversion. This does not read files, calibrated truth or replay geometry.
"""
    if uncertainty_px<0 or robust_delta_px<=0 or distance_scale_px<=0 or max_observed<1:
        raise ValueError('Nonnegative uncertainty and positive contour loss scales required')
    candidates,segments,counts=projected_contour_candidates(
        vertices,faces,K,T_camera_cv_from_world,T_world_from_blade_root,image_size,labels,
        candidate_valid_mask,max_candidates,sample_spacing_px,near_m)
    observed=torch.as_tensor(observed_points,device=vertices.device,dtype=vertices.dtype)
    if observed.ndim!=2 or observed.shape[1]!=2:raise ValueError('observed_points must be Nx2 trusted RGB coordinates')
    width,height=map(int,image_size)
    inside=(torch.isfinite(observed).all(dim=-1)&(observed[:,0]>=0)&(observed[:,0]<=width-1)
            &(observed[:,1]>=0)&(observed[:,1]<=height-1))
    observed=_limit(observed[inside],max_observed)
    zero=vertices.sum()*0
    terms={name:vertices.new_tensor(float(value)) for name,value in counts.items()}
    terms.update({'observed_count':vertices.new_tensor(float(len(observed))),
                  'candidate_to_observed':zero,'observed_to_candidate':zero,
                  'candidate_to_observed_mean_px':zero,'observed_to_candidate_mean_px':zero,
                  'contour_terms_available':vertices.new_tensor(float(bool(len(candidates) and len(segments) and len(observed))))})
    if not len(candidates) or not len(segments) or not len(observed):return zero,terms
    c2o=torch.linalg.vector_norm(candidates[:,None]-observed[None],dim=-1).min(dim=1).values
    o2c=_point_to_segments(observed,segments)
    def robust(distance):
        residual=torch.relu(distance-uncertainty_px)/distance_scale_px
        return F.smooth_l1_loss(residual,torch.zeros_like(residual),beta=robust_delta_px/distance_scale_px)
    terms['candidate_to_observed']=robust(c2o)
    terms['observed_to_candidate']=robust(o2c)
    terms['candidate_to_observed_mean_px']=c2o.mean()
    terms['observed_to_candidate_mean_px']=o2c.mean()
    return .5*(terms['candidate_to_observed']+terms['observed_to_candidate']),terms


def contour_backend_check():
    """Actual finite difference away from topology/visibility/sampling changes."""
    dtype=torch.float64
    base=torch.tensor([[-1.,-1.,4.],[1.,-1.,4.],[1.,1.,4.],[-1.,1.,4.],
                       [-1.,-1.,6.],[1.,-1.,6.],[1.,1.,6.],[-1.,1.,6.]],dtype=dtype)
    faces=torch.tensor([[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],
                        [1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]])
    k=torch.tensor([[40.,0.,31.5],[0.,40.,31.5],[0.,0.,1.]],dtype=dtype)
    eye=torch.eye(4,dtype=dtype)
    a=torch.linspace(21.5,41.5,50,dtype=dtype)
    observed=torch.cat([torch.stack((a,torch.full_like(a,21.5)),dim=-1),
                        torch.stack((a,torch.full_like(a,41.5)),dim=-1),
                        torch.stack((torch.full_like(a,21.5),a),dim=-1),
                        torch.stack((torch.full_like(a,41.5),a),dim=-1)])
    labels=torch.zeros((64,64),dtype=torch.uint8)
    validity=torch.ones_like(labels,dtype=torch.bool)
    def objective(x):
        offset=torch.stack((x,x.new_tensor(.067),x.new_zeros(())))
        return projected_contour_loss(base+offset,faces,k,eye,eye,(64,64),labels,observed,
                                      validity,uncertainty_px=.05,sample_spacing_px=1.3)
    parameter=torch.tensor(.123,dtype=dtype,requires_grad=True)
    loss,terms=objective(parameter);loss.backward()
    analytic=float(parameter.grad)
    epsilon=1e-5
    positive,_=objective(parameter.detach()+epsilon)
    negative,_=objective(parameter.detach()-epsilon)
    finite=float((positive-negative)/(2*epsilon))
    relative=abs(analytic-finite)/max(abs(analytic),abs(finite),1e-8)
    return {'backend':'torch_cpu_visible_projected_contour_point_segment',
            'torch_version':torch.__version__,'actual_backward':True,
            'finite_difference_epsilon':epsilon,'analytic_gradient':analytic,
            'finite_difference_gradient':finite,'relative_error':relative,
            'candidate_count':int(terms['candidate_count']),
            'candidate_segment_count':int(terms['candidate_segment_count']),
            'passed':bool(torch.isfinite(parameter.grad)) and abs(analytic)>1e-6 and relative<.005,
            'scope':'synthetic closed convex cube; away from topology/visibility/sampling case changes'}
