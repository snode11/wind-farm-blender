"""RGB local-background/subpixel surface-region observation v3.

Developed on old synthetic RGB/metric-control diagnostics. Refinement reads
RGB only. Binding uses an independently reconstructed mesh. This observation
module does not add residuals to the geometry objective or fusion pipeline.
RGB refinement retains v3. Binding uses the exact declared camera inverse and
the first hit over all estimated surfaces. Historical evaluation artifacts
remain frozen separately and must not be replaced when comparing candidates.
"""
import cv2
import numpy as np
from raster import vertex_frames
PARAMS={'background_ring_expand_px':40,'feature_exclusion_dilate_px':15,
        'background_polynomial_degree':2,'relative_component_contrast_level':.5,
        'minimum_feature_relative_contrast':.04,'boundary':'linear subpixel iso crossings on original pixel grid',
        'coarse_detector':'frozen RGB-v2 fixed sensor ROI; threshold15; missing-only 201px local median'}

def refine(rgb,mask,detail):
 if not mask.any():return mask,detail
 gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY).astype(np.float32);blur=cv2.GaussianBlur(gray,(0,0),1.)
 ys,xs=np.nonzero(mask);h,w=mask.shape;margin=PARAMS['background_ring_expand_px'];x0=max(0,int(xs.min())-margin);x1=min(w,int(xs.max())+margin+1);y0=max(0,int(ys.min())-margin);y1=min(h,int(ys.max())+margin+1)
 yy,xx=np.mgrid[y0:y1,x0:x1];cx,cy=xs.mean(),ys.mean();X=(xx-cx)/max(x1-x0,1);Y=(yy-cy)/max(y1-y0,1);A=np.stack([np.ones_like(X),X,Y,X*X,X*Y,Y*Y],-1)
 # Fit only surrounding RGB, excluding the feature plus its blur halo. Robust
 # clipping stops residual background edges/noise from determining the plane.
 exclude=cv2.dilate(mask.astype(np.uint8),np.ones((31,31),np.uint8))>0;keep=~exclude[y0:y1,x0:x1];g=blur[y0:y1,x0:x1];initial=int(keep.sum())
 if initial<200:return mask,{**detail,'v3_status':'INSUFFICIENT_RGB_BACKGROUND'}
 for _ in range(3):
  coef=np.linalg.lstsq(A[keep],g[keep],rcond=None)[0];residual=g-A@coef;med=np.median(residual[keep]);mad=np.median(abs(residual[keep]-med));next_keep=keep&(abs(residual-med)<=max(3.,4*1.4826*mad))
  if next_keep.sum()<200:break
  keep=next_keep
 background=np.maximum(A@coef,1.);sign=-1 if detail['polarity']=='dark' else 1;response=sign*(g/background-1.)
 local_mask=mask[y0:y1,x0:x1];core=float(np.percentile(response[local_mask],90));threshold=.5*core
 if core<PARAMS['minimum_feature_relative_contrast']:return mask,{**detail,'v3_status':'INSUFFICIENT_RGB_CONTRAST'}
 candidate=(response>=threshold).astype(np.uint8);n,labels,stats,_=cv2.connectedComponentsWithStats(candidate,8);overlap=[int(((labels==i)&local_mask).sum()) for i in range(1,n)]
 if not overlap or max(overlap)==0:return mask,{**detail,'v3_status':'NO_CONNECTED_REFINED_RGB_REGION'}
 refined=labels==(1+int(np.argmax(overlap)));points=[]
 # Horizontal and vertical neighboring pixels straddle the same component's
 # half-contrast contour. Linear interpolation retains original pixel centers.
 for axis in (0,1):
  if axis==0:
   diff=refined[:-1]!=refined[1:];iy,ix=np.nonzero(diff);a=response[iy,ix];b=response[iy+1,ix];fraction=np.clip((threshold-a)/(b-a),0,1);pts=np.column_stack([ix+x0,iy+y0+fraction])
  else:
   diff=refined[:,:-1]!=refined[:,1:];iy,ix=np.nonzero(diff);a=response[iy,ix];b=response[iy,ix+1];fraction=np.clip((threshold-a)/(b-a),0,1);pts=np.column_stack([ix+x0+fraction,iy+y0])
  points.extend(pts.tolist())
 out=np.zeros(mask.shape,bool);out[y0:y1,x0:x1]=refined
 d={**detail,'v3_status':'REFINED_RGB_REGION','method':'RGB-only local quadratic background normalization and 50% local component contrast subpixel boundary','feature_pixels':int(out.sum()),'boundary_uv':points,'boundary_coordinate_type':'subpixel_original_pixel_centers','centroid_uv':np.asarray(points).mean(0).tolist(),'local_relative_contrast':core,'local_relative_half_contrast_threshold':threshold,'background_fit_initial_pixels':initial,'background_fit_used_pixels':int(keep.sum()),'local_RGB_box_xyxy':[x0,y0,x1,y1]}
 return out,d

def bind_points(cam,rotor,state,points):
 """Exact camera-ray intersection of RGB subpixel samples with estimated mesh."""
 V,_=rotor.forward(np.asarray(state,float));faces=rotor.tpl.faces();alltri=V.reshape(3,-1,3)[:,faces].reshape(-1,3,3);normal=np.stack([vertex_frames(v)[0] for v in V]).reshape(3,-1,3)[:,faces].mean(2).reshape(-1,3)
 center=np.linalg.solve(cam.R,-cam.t)
 front=np.einsum('fc,fc->f',center-alltri.mean(1),normal)>0
 # Back faces and other blades must block the ray too. Selecting front faces
 # before intersection can see through an occluder to a farther surface.
 ids=np.arange(len(alltri));tri=alltri;a=tri[:,0];e1=tri[:,1]-a;e2=tri[:,2]-a;tv=center-a;qv=np.cross(tv,e1);numerator=np.einsum('fc,fc->f',e2,qv);uv=np.asarray(points,float).reshape(-1,2)
 directions=np.linalg.solve(cam.R,np.linalg.solve(cam.K,np.column_stack([uv,np.ones(len(uv))]).T)).T
 hit=[];bary=[];anchor=[];ambiguous_count=0
 for first in range(0,len(uv),32):
  d=directions[first:first+32];pv=np.cross(d[:,None],e2[None]);det=np.einsum('fc,bfc->bf',e1,pv);inv=np.divide(1.,det,out=np.zeros_like(det),where=abs(det)>1e-12);u=np.einsum('fc,bfc->bf',tv,pv)*inv;v=np.einsum('bc,fc->bf',d,qv)*inv;depth=numerator[None]*inv;ok=(abs(det)>1e-12)&(u>=-1e-9)&(v>=-1e-9)&(u+v<=1+1e-9)&(depth>.1);depth=np.where(ok,depth,np.inf);idx=np.argmin(depth,axis=1)
  best=depth[np.arange(len(idx)),idx]
  tolerance=np.maximum(1e-8,np.abs(best)*1e-9)
  # Equal-depth surfaces from different blade instances have no unique
  # material identity. Keep these pixels in the coverage denominator.
  finite=np.isfinite(best)
  tied=np.zeros(len(idx),bool)
  if finite.any():
   tied[finite]=((np.abs(depth[finite]-best[finite,None])<=tolerance[finite,None])&(ids[None]//len(faces)!=ids[idx[finite],None]//len(faces))).any(1)
  ambiguous_count+=int(tied.sum())
  for k,j in enumerate(idx):
   if not np.isfinite(depth[k,j]) or not front[j] or tied[k]:continue
   hit.append(ids[j]);bary.append([1-u[k,j]-v[k,j],u[k,j],v[k,j]]);anchor.append(uv[first+k])
 expected=len(uv)
 if not hit:return {'status':'NO_VISIBLE_RECONSTRUCTED_SURFACE','coverage':0.,'boundary_pixels':expected,'ambiguous_other_blade_boundary_pixels':ambiguous_count}
 hit=np.asarray(hit);blades,count=np.unique(hit//len(faces),return_counts=True);blade=int(blades[np.argmax(count)]);selected=hit//len(faces)==blade
 return {'status':'CANDIDATE_FIXED_TRIANGLE_BINDING','blade_0based':blade,'coverage':float(selected.sum()/expected),'boundary_pixels':expected,'bound_pixels':int(selected.sum()),'ambiguous_other_blade_boundary_pixels':ambiguous_count,'face':(hit[selected]%len(faces)).tolist(),'barycentric':np.asarray(bary)[selected].tolist(),'anchor_uv':np.asarray(anchor)[selected].tolist(),'binding_method':'exact declared camera inverse; nearest all-surface hit with front-face and unique blade checks; no source points'}
