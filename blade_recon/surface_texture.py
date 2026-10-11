"""Captured-RGB atlases on the unchanged healthy blade template.

No source mesh, repair definition, object ID, or truth loader is used. Canonical
coordinates are q=(physical normalized span, perimeter turns). An atlas texel
keeps that material coordinate across every saved Rotor state. Projecting it
again samples another observation; it never re-binds the material point.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

try:
    import cv2
except ImportError:  # Blender display evaluates bindings without OpenCV.
    cv2 = None
import numpy as np

UNKNOWN = np.array([255, 140, 26], dtype=np.uint8)
CONFLICT = np.array([166, 51, 230], dtype=np.uint8)
CHART_VERSION = 'blade-template-span-perimeter.v1'


def array_sha256(array):
    a = np.ascontiguousarray(array)
    return hashlib.sha256(str(a.dtype).encode() + str(a.shape).encode() + a.tobytes()).hexdigest()


def atlas_binding(rotor, shape=(1024, 512)):
    """Derive triangles/barycentrics from canonical q, including ring seam.

    Rows run root to tip, columns run once around the template perimeter. Texel
    centers avoid assigning both ends of the seam to different material points.
    Tip caps have no chart and remain explicitly unknown in the viewer.
    """
    h, w = map(int, shape)
    if not 8 <= h <= 4096 or not 8 <= w <= 2048 or h*w > 4_194_304:
        raise ValueError('atlas dimensions are outside the supported limits')
    span, turn = np.meshgrid((np.arange(h)+.5)/h, (np.arange(w)+.5)/w, indexing='ij')
    xi = rotor.tpl.xi
    i = np.clip(np.searchsorted(xi, span, side='right')-1, 0, len(xi)-2)
    v = (span-xi[i])/(xi[i+1]-xi[i])
    n = rotor.cfg.n_ring
    jt = turn*n
    j = np.floor(jt).astype(np.int64) % n
    u = jt-np.floor(jt)
    a = i*n+j
    b = i*n+(j+1)%n
    c = (i+1)*n+(j+1)%n
    d = (i+1)*n+j
    first = u >= v
    vertices = np.stack([a, np.where(first,b,c), np.where(first,c,d)], axis=-1)
    weights = np.stack([np.where(first,1-u,1-v), np.abs(u-v), np.minimum(u,v)], axis=-1)
    # For the second (a,c,d) triangle weights are [1-v,u,v-u].
    weights[...,1] = np.where(first,u-v,u)
    weights[...,2] = np.where(first,v,v-u)
    triangle = ((i*n+j)*2+(~first)).astype(np.int32)
    q = np.stack([span,turn],axis=-1).astype(np.float64)
    uv = q[...,::-1].copy()
    return dict(q=q, uv=uv, triangle_id=triangle,
                vertex_indices=vertices.astype(np.int32), barycentric_weights=weights)


def bound_positions(vertices, binding):
    """Evaluate fixed bindings on one deformed blade, without ray casting."""
    flat = np.asarray(vertices,float).reshape(-1,3)
    return np.sum(flat[binding['vertex_indices']]*binding['barycentric_weights'][...,None],axis=-2)


def _project(points, K, T):
    p = np.asarray(points,float) @ np.asarray(T,float)[:3,:3].T + np.asarray(T,float)[:3,3]
    z = p[...,2]
    with np.errstate(divide='ignore',invalid='ignore'):
        hom = p @ np.asarray(K,float).T
        xy = hom[...,:2]/hom[...,2,None]
    return xy,z


def rasterize_surface(rotor, state, camera):
    """Nearest reconstructed surface and perspective-correct template UV.

    Original pixels are sampled at integer centers, exactly as the K convention.
    T_model_to_cv is the direct M->C transform; no model/world transform is added.
    The depth map is model visibility only, never a claim of observed support.
    """
    w,h = int(camera['W']),int(camera['H'])
    if not 1 <= w <= 8192 or not 1 <= h <= 8192 or w*h > 33_554_432:
        raise ValueError('camera image dimensions exceed supported limits')
    K = np.asarray(camera['K'],float)
    T = np.asarray(camera['T_model_to_cv'],float)
    vertices,_ = rotor.forward(state)
    faces = rotor.tpl.faces()
    depth = np.full((h,w),np.inf,dtype=np.float64)
    blade_map = np.full((h,w),-1,dtype=np.int8)
    face_map = np.full((h,w),-1,dtype=np.int32)
    uv_map = np.full((h,w,2),-1,dtype=np.float32)
    n = rotor.cfg.n_ring
    vertex_uv = np.stack([np.tile(np.arange(n)/n,rotor.cfg.n_sections),
                          np.repeat(rotor.tpl.xi,n)],axis=-1)
    for blade in range(3):
        xy,z = _project(vertices[blade].reshape(-1,3),K,T)
        for face_id,face in enumerate(faces):
            zz = z[face]
            p = xy[face]
            # A triangle crossing the near plane is deliberately excluded and
            # logged by the extraction statistics, rather than extrapolated.
            if np.any(zz <= .05) or not np.isfinite(p).all():
                continue
            lo = np.maximum(np.ceil(p.min(axis=0)).astype(int),[0,0])
            hi = np.minimum(np.floor(p.max(axis=0)).astype(int),[w-1,h-1])
            if np.any(hi < lo):
                continue
            a,b,c = p
            den = (b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
            if abs(den) < 1e-10:
                continue
            xx,yy = np.meshgrid(np.arange(lo[0],hi[0]+1),np.arange(lo[1],hi[1]+1))
            l0 = ((b[1]-c[1])*(xx-c[0])+(c[0]-b[0])*(yy-c[1]))/den
            l1 = ((c[1]-a[1])*(xx-c[0])+(a[0]-c[0])*(yy-c[1]))/den
            l2 = 1-l0-l1
            screen = np.stack([l0,l1,l2],axis=-1)
            inside = np.min(screen,axis=-1) >= -1e-7
            perspective = screen/zz
            invz = perspective.sum(axis=-1)
            with np.errstate(divide='ignore',invalid='ignore'):
                dz = 1/invz
                weights = perspective/invz[...,None]
            sl = np.s_[lo[1]:hi[1]+1,lo[0]:hi[0]+1]
            update = inside & (dz < depth[sl]) & (dz > .05)
            if not update.any():
                continue
            chart = vertex_uv[face].copy()
            if np.ptp(chart[:,0]) > .5:
                chart[chart[:,0] < .5,0] += 1
            interpolated = weights @ chart
            interpolated[...,0] %= 1
            depth[sl][update] = dz[update]
            blade_map[sl][update] = blade
            face_map[sl][update] = face_id
            uv_map[sl][update] = interpolated[update]
    return dict(depth=depth,blade_id=blade_map,triangle_id=face_map,uv=uv_map)


def _frame_camera(frame):
    for key in ('K','T_model_to_cv','W','H'):
        if key not in frame:
            raise ValueError('explicit '+key+' is required for every texture frame')
    return {key:frame[key] for key in ('K','T_model_to_cv','W','H')}


def extract_atlas(rotor, states, frames, *, shape=(1024,512),
                  visibility_tolerance_m=1e-5, conflict_distance_rgb=60., blade_id=0):
    """Extract original RGB only inside declared image polygons.

    Frames and states are aligned and already belong to a frozen input split.
    This function does no background/color statistics outside those inputs.
    Conflict threshold is Euclidean RGB distance in uint8 units. Orange means
    no accepted image support; purple means multiple accepted samples conflict.
    """
    if cv2 is None:
        raise RuntimeError('RGB extraction requires the offline OpenCV environment')
    states = np.asarray(states,float)
    if states.shape != (len(frames),13) or not len(frames) or len(frames)>128:
        raise ValueError('aligned nonempty states/frames (at most 128) are required')
    if not 0 <= int(blade_id) < 3:
        raise ValueError('blade_id must be 0, 1, or 2')
    binding = atlas_binding(rotor,shape)
    h,w = binding['triangle_id'].shape
    samples = np.full((len(frames),h,w,3),np.nan,dtype=np.float32)
    pixels = np.full((len(frames),h,w,2),-1,dtype=np.int32)
    stats, sources = [],[]
    for f,(frame,state) in enumerate(zip(frames,states)):
        path = Path(frame['rgb_path']).expanduser().resolve()
        rgb_sha=hashlib.sha256(path.read_bytes()).hexdigest()
        if frame.get('expected_rgb_sha256') is not None and frame['expected_rgb_sha256']!=rgb_sha:
            raise ValueError('selected RGB no longer matches its reviewed manual annotation hash')
        bgr = cv2.imread(str(path),cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError('cannot read selected input RGB: '+str(path))
        rgb = cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
        camera = _frame_camera(frame)
        if rgb.shape != (camera['H'],camera['W'],3):
            raise ValueError('original RGB dimensions do not match explicit calibration')
        polygon = np.asarray(frame['patch_polygon_xy'],float)
        if polygon.ndim != 2 or polygon.shape[1]!=2 or len(polygon)<3 or not np.isfinite(polygon).all():
            raise ValueError('manual patch_polygon_xy must be finite Nx2 original pixels')
        if np.any(polygon < 0) or np.any(polygon[:,0]>camera['W']-1) or np.any(polygon[:,1]>camera['H']-1):
            raise ValueError('manual polygon is outside original RGB dimensions')
        mask = np.zeros(rgb.shape[:2],dtype=np.uint8)
        cv2.fillPoly(mask,[np.rint(polygon).astype(np.int32)],1)
        vertices,axes = rotor.forward(state)
        positions = bound_positions(vertices[int(blade_id)],binding)
        xy,z = _project(positions,camera['K'],camera['T_model_to_cv'])
        finite = np.isfinite(xy).all(axis=-1) & (z > .05)
        pix = np.where(finite[...,None],np.rint(xy),-1).astype(np.int64)
        in_image = finite & (pix[...,0]>=0) & (pix[...,0]<camera['W']) & (pix[...,1]>=0) & (pix[...,1]<camera['H'])
        xp = np.clip(pix[...,0],0,camera['W']-1)
        yp = np.clip(pix[...,1],0,camera['H']-1)
        in_region = in_image & (mask[yp,xp]>0)
        # Pixel-depth tolerance must not admit the thin back surface. Orient each
        # template triangle outward using its own model axis, then require the
        # material point to face this calibrated camera independently of depth.
        triangle_vertices=vertices[int(blade_id)].reshape(-1,3)[binding['vertex_indices']]
        normals=np.cross(triangle_vertices[...,1,:]-triangle_vertices[...,0,:],
                         triangle_vertices[...,2,:]-triangle_vertices[...,0,:])
        axis_vertices=axes[int(blade_id)][binding['vertex_indices']//rotor.cfg.n_ring]
        axis_positions=np.sum(axis_vertices*binding['barycentric_weights'][...,None],axis=-2)
        outward=np.sum(normals*(positions-axis_positions),axis=-1)
        normals=np.where((outward<0)[...,None],-normals,normals)
        T=np.asarray(camera['T_model_to_cv'],float)
        center=-np.linalg.solve(T[:3,:3],T[:3,3])
        front_facing=np.sum(normals*(center-positions),axis=-1)>1e-9
        # Check the exact material-point ray, not a rounded pixel-depth sample.
        # Only the small manually selected ROI pays this cost; no source-scene
        # object IDs or dynamic meshes participate in the model ray test.
        try:
            from .surface_geometry import ray_triangles
        except ImportError:
            from surface_geometry import ray_triangles
        triangles=np.concatenate([v.reshape(-1,3)[rotor.tpl.faces()] for v in vertices])
        depth_visible=np.zeros((h,w),dtype=bool)
        for y,x in np.argwhere(in_region & front_facing):
            delta=positions[y,x]-center
            distance=np.linalg.norm(delta)
            hits,_=ray_triangles(center,delta/distance,triangles)
            nearest=float(hits.min())
            depth_visible[y,x]=np.isfinite(nearest) and abs(nearest-distance)<=visibility_tolerance_m
        visible=front_facing & depth_visible
        accepted = in_region & visible
        samples[f][accepted] = rgb[yp[accepted],xp[accepted]]
        pixels[f][accepted] = pix[accepted]
        stats.append(dict(source_index=f,texels_total=h*w,
            negative_or_invalid_depth=int((~finite).sum()),out_of_image=int((finite & ~in_image).sum()),
            projected_inside_manual_region=int(in_region.sum()),
            candidate_backface_rejections=int((in_region & ~front_facing).sum()),
            candidate_occlusion_or_depth_rejections=int((in_region & front_facing & ~depth_visible).sum()),accepted_texels=int(accepted.sum()),
            accepted_fraction_of_region_candidates=float(accepted.sum()/max(int(in_region.sum()),1)),
            manual_region_pixels=int(mask.sum())))
        sources.append(dict(camera_name=frame['camera_name'],frame_id=int(frame['frame_id']),t=float(frame['t']),
            rgb_path=str(path),rgb_sha256=rgb_sha,
            original_size=[camera['W'],camera['H']],color_order='RGB',pixel_centers='integer_origin_top_left',
            image_transform='identity; no crop or resize',patch_polygon_xy=polygon.tolist(),
            annotation_author=frame.get('annotation_author','not supplied'),
            annotation_method=frame.get('annotation_method','manual image polygon'),
            annotation_source=frame.get('annotation_source','not supplied'),
            annotation_sha256=frame.get('annotation_sha256'),
            simulation_time_s=frame.get('sim_t'),input_split=frame.get('input_split','selected solver input'),
            K=np.asarray(camera['K']).tolist(),T_model_to_cv=np.asarray(camera['T_model_to_cv']).tolist()))
    valid = np.isfinite(samples[...,0])
    count = valid.sum(axis=0)
    # Suppress only the all-NaN warning; all such texels are kept UNKNOWN.
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore',RuntimeWarning)
        median = np.nanmedian(samples,axis=0)
    distances = np.linalg.norm(samples-median[None],axis=-1)
    distances[~valid] = -np.inf
    conflict = (count>1) & (distances.max(axis=0)>float(conflict_distance_rgb))
    status = np.zeros((3,h,w),dtype=np.uint8)
    status[int(blade_id)] = np.where(count>0,np.where(conflict,2,1),0)
    texture = np.broadcast_to(UNKNOWN,(3,h,w,3)).copy()
    chosen = np.argmin(np.where(valid,np.linalg.norm(samples-median[None],axis=-1),np.inf),axis=0)
    yy,xx = np.indices((h,w))
    has = count>0
    captured = np.zeros((3,h,w,3),dtype=np.uint8)
    captured[int(blade_id)][has] = samples[chosen[has],yy[has],xx[has]].astype(np.uint8)
    texture[int(blade_id)][has] = captured[int(blade_id)][has]
    texture[int(blade_id)][conflict] = CONFLICT
    source_index = np.full((3,h,w),-1,dtype=np.int32)
    source_index[int(blade_id)][has] = chosen[has]
    source_xy = np.full((3,h,w,2),-1,dtype=np.int32)
    source_xy[int(blade_id)][has] = pixels[chosen[has],yy[has],xx[has]]
    sample_count = np.zeros((3,h,w),dtype=np.uint16)
    sample_count[int(blade_id)] = count
    healthy,_ = rotor.forward(np.zeros(13))
    fv = healthy[int(blade_id)].reshape(-1,3)[rotor.tpl.faces()]
    areas = np.linalg.norm(np.cross(fv[:,1]-fv[:,0],fv[:,2]-fv[:,0]),axis=-1)/2
    per_triangle = np.bincount(binding['triangle_id'].ravel(),minlength=len(areas))
    weights = areas[binding['triangle_id']]/np.maximum(per_triangle[binding['triangle_id']],1)
    summary = dict(appearance_kind='captured_rgb',chart_version=CHART_VERSION,
        texture_sampling='nearest original integer RGB pixel; representative sample nearest RGB median',
        visibility_method='outward-facing triangle plus exact ray against all three reconstructed blades',
        visibility_tolerance_m=float(visibility_tolerance_m),conflict_distance_rgb=float(conflict_distance_rgb),
        selected_blade_id=int(blade_id),source_frame_count=len(frames),source_statistics=stats,
        texels_captured=int((status==1).sum()),texels_conflict=int((status==2).sum()),texels_unknown=int((status==0).sum()),
        texels_single_input_only=int((sample_count==1).sum()),
        texels_multiple_input_support=int((sample_count>1).sum()),
        temporal_support_interpretation='union of accepted input observations at fixed q; repeated gray color alone does not prove correct material localization',
        template_lateral_area_per_blade_m2=float(weights.sum()),template_tip_cap_area_per_blade_m2=float(areas[(rotor.cfg.n_sections-1)*rotor.cfg.n_ring*2:].sum()),
        template_captured_area_m2=float(weights[status[int(blade_id)]==1].sum()),
        template_conflict_area_m2=float(weights[status[int(blade_id)]==2].sum()),
        template_unknown_lateral_area_selected_blade_m2=float(weights[status[int(blade_id)]==0].sum()),
        template_unknown_lateral_area_m2=float(2*weights.sum()+weights[status[int(blade_id)]==0].sum()),
        template_unknown_tip_cap_area_m2=float(3*areas[(rotor.cfg.n_sections-1)*rotor.cfg.n_ring*2:].sum()),
        visibility_interpretation='candidate rear-surface rejection is not a confirmed-material contradiction; confirmed point conflicts belong to geometry solver',
        status='REVIEW_ONLY',geometry_claim='appearance binding only; no geometry refinement or accuracy acceptance')
    arrays = dict(**binding,texture_rgb=texture,captured_rgb=captured,support_status=status,
                  source_index=source_index,source_xy=source_xy,sample_count=sample_count,template_area_weights_m2=weights)
    return arrays,sources,summary


def project_atlas(rotor,state,camera,arrays):
    """Render support/color in another calibrated view for independent scoring."""
    raster = rasterize_surface(rotor,state,camera)
    uv = raster['uv']
    h,w = arrays['support_status'].shape[1:]
    tx = np.minimum(np.maximum((uv[...,0]*w).astype(int),0),w-1)
    ty = np.minimum(np.maximum((uv[...,1]*h).astype(int),0),h-1)
    b = np.maximum(raster['blade_id'],0)
    status = arrays['support_status'][b,ty,tx].copy()
    color = arrays['texture_rgb'][b,ty,tx].copy()
    status[raster['blade_id']<0] = 0
    side_faces=(rotor.cfg.n_sections-1)*rotor.cfg.n_ring*2
    status[raster['triangle_id']>=side_faces] = 0
    color[raster['triangle_id']>=side_faces] = UNKNOWN
    color[raster['blade_id']<0] = 0
    return dict(color_rgb=color,support_status=status,depth=raster['depth'],blade_id=raster['blade_id'])
