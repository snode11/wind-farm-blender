"""Offline ideal rays and independent clearance geometry (metres, global z-up).
The estimator deliberately accepts only a slant range and fixed calibration.
OpenFAST VTP is solver-generated AeroDyn section geometry, not the frontend mesh.
"""
from dataclasses import dataclass
from pathlib import Path
import math
import xml.etree.ElementTree as ET
import numpy as np

@dataclass(frozen=True)
class Calibration:
    origin_m: tuple = (-2.,0.,87.6)
    angles_deg: tuple = (6.45,8.5,10.54)
    y_lidar_m: float = 2.
    r_tip_m: float = 2.67
    min_range_m: float = 5.
    max_range_m: float = 100.
    def directions(self):
        return [np.array([-math.sin(math.radians(a)),0.,-math.cos(math.radians(a))]) for a in self.angles_deg]

def simplified_estimate(slant_range_m, valid, theta_deg, y_lidar_m, r_tip_m):
    """MolasCL V3.0 §3.5.3; theta from downward vertical toward rotor.
    Y_lidar is positive upstream tower-axis offset; R_TIP fixed calibration.
    No geometry/truth/real-time tip data enter this function.
    """
    if not valid: return None
    values=[slant_range_m,theta_deg,y_lidar_m,r_tip_m]
    if not all(v is not None and math.isfinite(v) for v in values) or slant_range_m<0: raise ValueError('invalid calibration/range')
    return slant_range_m*math.sin(math.radians(theta_deg))+y_lidar_m-r_tip_m

def read_surface(path):
    root=ET.parse(path).getroot()
    points=np.fromstring(root.find('.//Points/DataArray').text,sep=' ').reshape(-1,3)
    arrays={a.attrib['Name']:np.fromstring(a.text,sep=' ',dtype=int) for a in root.findall('.//Polys/DataArray')}
    triangles=[];start=0
    if np.all(np.diff(np.r_[0,arrays['offsets']])==4):
        quads=arrays['connectivity'].reshape(-1,4)
        triangles=np.concatenate([quads[:,[0,1,2]],quads[:,[0,2,3]]])
        if not np.isfinite(points).all():raise ValueError(f'invalid surface {path}')
        return points,triangles
    for end in arrays['offsets']:
        poly=arrays['connectivity'][start:end];start=end
        for j in range(1,len(poly)-1): triangles.append([poly[0],poly[j],poly[j+1]])
    if not np.isfinite(points).all() or not triangles: raise ValueError(f'invalid surface {path}')
    return points,np.asarray(triangles,dtype=int)

def first_hit(origin, direction, points, triangles):
    """Two-sided Moller-Trumbore, first positive surface hit; no back-face culling."""
    origin=np.asarray(origin,dtype=float);direction=np.asarray(direction,dtype=float)
    if not np.isclose(np.linalg.norm(direction),1,atol=1e-10): raise ValueError('direction must be normalized')
    tri=points[triangles];e1=tri[:,1]-tri[:,0];e2=tri[:,2]-tri[:,0]
    p=np.cross(np.broadcast_to(direction,e2.shape),e2);det=np.einsum('ij,ij->i',e1,p)
    safe=np.abs(det)>1e-11;inv=np.zeros_like(det);inv[safe]=1/det[safe]
    t=origin-tri[:,0];u=np.einsum('ij,ij->i',t,p)*inv;q=np.cross(t,e1)
    v=q@direction*inv;distance=np.einsum('ij,ij->i',e2,q)*inv
    valid=safe&(u>=-1e-9)&(v>=-1e-9)&(u+v<=1+1e-9)&(distance>1e-8)
    if not valid.any(): return None
    i=int(np.argmin(np.where(valid,distance,np.inf)));d=float(distance[i])
    return d,(origin+d*direction).tolist(),i

def tower_radius(z):
    if not 0<=z<=87.6: raise ValueError('tip outside rigid tower height')
    return 3.+(1.935-3.)*z/87.6

def truth_clearance(tip):
    """Independent point-to-horizontal circle distance for documented rigid NREL tower."""
    tip=np.asarray(tip,dtype=float);r=tower_radius(float(tip[2]));rho=float(np.linalg.norm(tip[:2]))
    if rho==0: return -r,[r,0.,float(tip[2])]
    wall=[float(tip[0]*r/rho),float(tip[1]*r/rho),float(tip[2])]
    return rho-r,wall

def tip_reference(points, sections):
    """Perimeter-coordinate centroid at outermost solver AeroDyn section.
    This explicit reference is NOT whole-blade minimum collision clearance.
    """
    if len(points)%sections: raise ValueError('surface section topology mismatch')
    return points[-len(points)//sections:].mean(axis=0)

def collision_excluded(points,triangles):
    """Conservatively separate every triangle from the rigid tapered tower.

    Use the largest tower radius over each triangle's z range. The cheap
    upstream half-space test handles most surfaces. For remaining triangles,
    distance from the origin to the entire XY projection must exceed that
    radius: the tower is enclosed by that cylinder, so separation is sufficient.
    False still means unresolved, not proof of an actual collision.
    """
    tri=np.asarray(points,dtype=float)[triangles]
    if not np.isfinite(tri).all():
        return False
    low=tri[:,:,2].min(1);high=tri[:,:,2].max(1)
    overlap=(low<=87.6)&(high>=0)
    tri=tri[overlap]
    radii=3.+(1.935-3.)*np.clip(low[overlap],0,87.6)/87.6
    pending=tri[:,:,0].max(1)>=-radii
    if not pending.any():
        return True
    xy=tri[pending,:,:2];radii=radii[pending]
    end=np.roll(xy,-1,axis=1);edge=end-xy
    lengths=np.sum(edge*edge,axis=2)
    alpha=np.divide(-np.sum(xy*edge,axis=2),lengths,
                    out=np.zeros_like(lengths),where=lengths>0)
    nearest=xy+np.clip(alpha,0,1)[:,:,None]*edge
    d2=np.min(np.sum(nearest*nearest,axis=2),axis=1)
    # A projected triangle enclosing the origin has distance zero even when
    # all three edges are outside the cylinder. Degenerate projections are
    # conservatively treated as enclosing when all cross products vanish.
    cross=edge[:,:,0]*(-xy[:,:,1])-edge[:,:,1]*(-xy[:,:,0])
    inside=np.all(cross>=0,axis=1)|np.all(cross<=0,axis=1)
    d2[inside]=0
    return bool(np.all(d2>(radii+1e-9)**2))

def background_first_hit(origin,direction):
    """First intersection with rigid tapered tower or ground plane (ideal opaque)."""
    o=np.asarray(origin,float);d=np.asarray(direction,float);candidates=[]
    if d[2]<0 and o[2]>=0:
        t=-o[2]/d[2]
        if t>1e-8:candidates.append((float(t),(o+t*d).tolist(),'ground'))
    slope=(1.935-3.)/87.6;r0=3+slope*o[2];rd=slope*d[2]
    a=d[0]**2+d[1]**2-rd**2;b=2*(o[0]*d[0]+o[1]*d[1]-r0*rd);c=o[0]**2+o[1]**2-r0**2
    roots=np.roots([a,b,c]) if abs(a)>1e-15 else ([-c/b] if abs(b)>1e-15 else [])
    for root in roots:
        if abs(np.imag(root))<1e-10:
            t=float(np.real(root));point=o+t*d
            if t>1e-8 and 0<=point[2]<=87.6:candidates.append((t,point.tolist(),'tower'))
    return min(candidates,key=lambda h:h[0]) if candidates else None
