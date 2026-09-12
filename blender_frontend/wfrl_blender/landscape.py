"""Meter-scale procedural landscape for presentation only; no backend terrain changes."""
import math
import random


TURBINE_X = (0.0, 504.0, 1008.0)


def road_y(x):
    """Centerline used by both the road mesh and vegetation clearance."""
    return -31.0 + 5.0 * math.sin(x * .004)


def _smoothstep(edge0, edge1, value):
    if edge0 == edge1:
        return float(value >= edge1)
    t = min(1.0, max(0.0, (value - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


def _distance_to_pad(x, y, center_x):
    """Distance to a maintenance-pad rectangle, zero when inside it."""
    dx = max(abs(x - center_x) - 15.0, 0.0)
    dy = max(abs(y + 11.5) - 24.5, 0.0)
    return math.hypot(dx, dy)


def road_clearance(x, y):
    """Distance to the service road or a turbine maintenance pad."""
    return min(abs(y - road_y(x)), *(_distance_to_pad(x, y, tx) for tx in TURBINE_X))


def lowland_factor(z):
    """Return a smooth 0..1 moisture proxy based on presentation terrain height."""
    return 1.0 - _smoothstep(-3.0, 11.0, z)


def vegetation_factor(x, y, z):
    """Road-clearance and elevation rule shared by all ground-cover layers."""
    road_factor = _smoothstep(10.0, 58.0, road_clearance(x, y))
    return road_factor * (.34 + .66 * lowland_factor(z))


def height(x, y):
    from mathutils import Vector, noise
    # Flat turbine corridor transitions into rolling ground and distant ridges.
    corridor = min(1.0, max(0.0, (abs(y) - 24) / 230))
    detail = noise.fractal(Vector((x * .003, y * .003, 1.7)), 1.0, 2.0, 4)
    rolling = 8 * math.sin(x * .003 + y * .004) + 12 * detail
    ridge = max(0.0, (y - 650) / 1800)
    return -.25 + corridor * (rolling + ridge * (70 + 45 * math.sin(x * .0017) + 22 * detail))


def terrain_shader(material):
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    out = nodes.new('ShaderNodeOutputMaterial')
    bsdf = nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.inputs['Roughness'].default_value = .94
    coord = nodes.new('ShaderNodeTexCoord')
    def noise_node(scale, detail):
        node = nodes.new('ShaderNodeTexNoise')
        node.inputs['Scale'].default_value = scale
        node.inputs['Detail'].default_value = detail
        node.inputs['Roughness'].default_value = .72
        links.new(coord.outputs['Object'], node.inputs['Vector'])
        return node
    broad = noise_node(.0018, 2)
    middle = noise_node(.012, 2)
    habitat = nodes.new('ShaderNodeMixRGB')
    habitat.name = 'WFRL.Terrain.Habitat'
    habitat.inputs[0].default_value = .28
    links.new(broad.outputs['Fac'], habitat.inputs[1])
    links.new(middle.outputs['Fac'], habitat.inputs[2])
    ramp = nodes.new('ShaderNodeValToRGB')
    stops = [(.22, (.035,.12,.012,1)), (.42,(.12,.32,.025,1)),
             (.49,(.32,.38,.10,1)), (.56,(.72,.49,.21,1)),
             (.65,(.32,.16,.065,1)), (.76,(.65,.64,.51,1))]
    for elem in list(ramp.color_ramp.elements)[2:]:
        ramp.color_ramp.elements.remove(elem)
    for i,(position,color) in enumerate(stops):
        elem = ramp.color_ramp.elements[i] if i < 2 else ramp.color_ramp.elements.new(position)
        elem.position, elem.color = position,color
    ramp.color_ramp.interpolation = 'EASE'
    links.new(habitat.outputs[0], ramp.inputs[0])
    fine = noise_node(.7, 3)
    mix = nodes.new('ShaderNodeMixRGB'); mix.blend_type = 'MULTIPLY'; mix.inputs[0].default_value=.30
    links.new(ramp.outputs['Color'], mix.inputs[1]); links.new(fine.outputs['Fac'], mix.inputs[2])
    # Photographic color/roughness/normal maps, tiled in meters with macro variation.
    import bpy
    from pathlib import Path
    asset = Path(__file__).parent / 'assets' / 'landscape'
    mapping = nodes.new('ShaderNodeVectorMath'); mapping.operation = 'SCALE'
    mapping.inputs[3].default_value = 1 / 38
    links.new(coord.outputs['Object'],mapping.inputs[0])
    def image_node(suffix, noncolor=False):
        tex = nodes.new('ShaderNodeTexImage')
        tex.image = bpy.data.images.load(str(asset / ('rocky_terrain_02_' + suffix + '_2k.jpg')), check_existing=True)
        if noncolor: tex.image.colorspace_settings.name = 'Non-Color'
        tex.projection = 'BOX'; tex.projection_blend = .3
        links.new(mapping.outputs[0],tex.inputs['Vector'])
        return tex
    diffuse = image_node('diff')
    tint = nodes.new('ShaderNodeMixRGB'); tint.blend_type = 'MULTIPLY'; tint.inputs[0].default_value=.78
    links.new(diffuse.outputs['Color'],tint.inputs[1]); links.new(ramp.outputs['Color'],tint.inputs[2])
    camera = nodes.new('ShaderNodeCameraData')
    depth = nodes.new('ShaderNodeMapRange')
    depth.name = 'WFRL.Terrain.DistanceHaze'
    depth.interpolation_type = 'SMOOTHSTEP'
    depth.inputs['From Min'].default_value=1400; depth.inputs['From Max'].default_value=9500
    depth.inputs['To Min'].default_value=0; depth.inputs['To Max'].default_value=.50
    links.new(camera.outputs['View Distance'],depth.inputs['Value'])
    haze=nodes.new('ShaderNodeMixRGB'); haze.inputs[2].default_value=(.16,.21,.24,1)
    links.new(depth.outputs[0],haze.inputs[0]); links.new(tint.outputs[0],haze.inputs[1])
    links.new(haze.outputs[0],bsdf.inputs['Base Color'])
    rough=image_node('rough',True); links.new(rough.outputs[0],bsdf.inputs['Roughness'])
    normal=image_node('nor_gl',True)
    norm=nodes.new('ShaderNodeBump'); norm.inputs['Distance'].default_value=.3; norm.inputs['Strength'].default_value=.5
    links.new(rough.outputs[0],norm.inputs['Height']); links.new(norm.outputs[0],bsdf.inputs['Normal'])
    links.new(bsdf.outputs[0],out.inputs['Surface'])


def terrain_sampler(terrain):
    """Snapshot the immutable terrain mesh once; avoid per-query depsgraph work."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    data = terrain.data
    data.calc_loop_triangles()
    tree = BVHTree.FromPolygons([vertex.co.copy() for vertex in data.vertices],
                               [tuple(triangle.vertices) for triangle in data.loop_triangles],
                               all_triangles=True)
    down = Vector((0, 0, -1))
    def surface(x, y):
        point, _, _, _ = tree.ray_cast(Vector((x, y, 1500)), down)
        return point.z if point is not None else height(x, y)
    return surface


def build_landscape(collection):
    import bpy
    from .materials import get_material
    rng = random.Random(4207)
    def mesh(name, verts, faces, material, *, smooth=True):
        data=bpy.data.meshes.new(name+'.Data'); data.from_pydata(verts,[],faces); data.update()
        obj=bpy.data.objects.new(name,data); collection.objects.link(obj); data.materials.append(material)
        obj['fidelity']='SYNTH'
        for face in data.polygons: face.use_smooth=smooth
        return obj
    def material(name,color):
        mat=bpy.data.materials.get(name) or bpy.data.materials.new(name)
        mat.diffuse_color=(*color,1); mat.use_nodes=True
        nodes,links=mat.node_tree.nodes,mat.node_tree.links;nodes.clear()
        out=nodes.new('ShaderNodeOutputMaterial');shader=nodes.new('ShaderNodeBsdfPrincipled')
        shader.inputs['Base Color'].default_value=(*color,1);shader.inputs['Roughness'].default_value=.95
        links.new(shader.outputs[0],out.inputs['Surface'])
        return mat
    def patch_material(name,color):
        """Terrain-cover material with meter-scale variation and feathered edges."""
        mat=bpy.data.materials.get(name) or bpy.data.materials.new(name)
        mat.diffuse_color=(*color,1);mat.use_nodes=True
        nodes,links=mat.node_tree.nodes,mat.node_tree.links;nodes.clear()
        out=nodes.new('ShaderNodeOutputMaterial')
        shader=nodes.new('ShaderNodeBsdfPrincipled');shader.inputs['Roughness'].default_value=.97
        coord=nodes.new('ShaderNodeTexCoord');noise=nodes.new('ShaderNodeTexNoise')
        noise.inputs['Scale'].default_value=.055;noise.inputs['Detail'].default_value=3
        noise.inputs['Roughness'].default_value=.72;links.new(coord.outputs['Object'],noise.inputs['Vector'])
        ramp=nodes.new('ShaderNodeValToRGB')
        ramp.color_ramp.elements[0].position=.28
        ramp.color_ramp.elements[0].color=(*(component*.62 for component in color),1)
        ramp.color_ramp.elements[1].position=.74
        ramp.color_ramp.elements[1].color=(*(min(1,component*1.22+.004) for component in color),1)
        links.new(noise.outputs['Fac'],ramp.inputs[0]);links.new(ramp.outputs['Color'],shader.inputs['Base Color'])
        bump=nodes.new('ShaderNodeBump');bump.inputs['Strength'].default_value=.16;bump.inputs['Distance'].default_value=.09
        links.new(noise.outputs['Fac'],bump.inputs['Height']);links.new(bump.outputs[0],shader.inputs['Normal'])
        transparent=nodes.new('ShaderNodeBsdfTransparent')
        weight=nodes.new('ShaderNodeVertexColor');weight.layer_name='WFRL.PatchWeight'
        mix_shader=nodes.new('ShaderNodeMixShader')
        links.new(weight.outputs['Color'],mix_shader.inputs[0])
        links.new(transparent.outputs[0],mix_shader.inputs[1]);links.new(shader.outputs[0],mix_shader.inputs[2])
        links.new(mix_shader.outputs[0],out.inputs['Surface'])
        mat.surface_render_method='DITHERED'
        return mat
    # Broad enough to remove the visible rectangular edge from ground-level views.
    nx,ny=360,300
    verts=[];faces=[]
    for j in range(ny+1):
        y=-2100+j*6500/ny
        for i in range(nx+1):
            x=-3500+i*8500/nx; verts.append((x,y,height(x,y)))
    for j in range(ny):
        for i in range(nx):
            k=j*(nx+1)+i;faces.append((k,k+1,k+nx+2,k+nx+1))
    terrain=mesh('WFRL.Terrain',verts,faces,get_material('terrain'))
    terrain['provenance']='Procedural grassland and ridges, presentation only'
    gravel=material('WFRL.Landscape.Gravel',(.28,.235,.17))
    # Roughness and gravel-scale normals avoid a perfectly flat road ribbon.
    nodes,links=gravel.node_tree.nodes,gravel.node_tree.links
    tex=nodes.new('ShaderNodeTexNoise');tex.inputs['Scale'].default_value=3
    coord=nodes.new('ShaderNodeTexCoord');links.new(coord.outputs['Object'],tex.inputs['Vector'])
    bump=nodes.new('ShaderNodeBump');bump.inputs['Distance'].default_value=.12
    links.new(tex.outputs['Fac'],bump.inputs['Height']);links.new(bump.outputs[0],nodes.get('Principled BSDF').inputs['Normal'])
    surface = terrain_sampler(terrain)
    shoulder=material('WFRL.Landscape.RoadShoulder',(.14,.12,.075))
    track=material('WFRL.Landscape.CompactedGravel',(.22,.19,.14))
    # Gravel color varies in world metres; road edges are geometry, not a
    # bright uniform ribbon. Camber and compacted tracks share one mesh.
    ramp=nodes.new('ShaderNodeValToRGB')
    ramp.color_ramp.elements[0].color=(.14,.12,.085,1)
    ramp.color_ramp.elements[1].color=(.31,.27,.20,1)
    links.new(tex.outputs['Fac'],ramp.inputs[0])
    links.new(ramp.outputs[0],nodes.get('Principled BSDF').inputs['Base Color'])
    v=[];f=[];bands=(-5.6,-3.5,-2.0,-1.25,0,1.25,2.0,3.5,5.6)
    centers=[(x,road_y(x)) for x in range(-900,2000,4)]
    for x,y in centers:
        for j,dy in enumerate(bands):
            edge=abs(dy)>5
            offset=dy+(.28*math.sin(x*.19)+.17*math.sin(x*.47) if edge else 0)
            elevation=.018 if edge else .09+.06*(1-abs(dy)/3.5)
            v.append((x,y+offset,surface(x,y+offset)+elevation))
    for i in range(len(centers)-1):
        for j in range(len(bands)-1):
            k=i*len(bands)+j;f.append((k,k+1,k+1+len(bands),k+len(bands)))
    road=mesh('WFRL.Landscape.ServiceRoad',v,f,gravel)
    road.data.materials.append(shoulder);road.data.materials.append(track)
    for i,face in enumerate(road.data.polygons):
        band=i%8;face.material_index=1 if band in (0,7) else (2 if band in (2,5) else 0)
    for tx in TURBINE_X:
        # Bevelled platform outline with a graded gravel apron. All vertices
        # sample the actual terrain, including the descent to the access road.
        outline=((-13,-24.5),(13,-24.5),(15,-22.5),(15,22.5),
                 (13,24.5),(-13,24.5),(-15,22.5),(-15,-22.5))
        v=[(tx,-11.5,surface(tx,-11.5)+.22)];f=[]
        for scale,lift in ((.94,.22),(1,.19),(1.14,.018)):
            for dx,dy in outline:
                x,y=tx+dx*scale,-11.5+dy*scale
                v.append((x,y,surface(x,y)+lift))
        for i in range(8):f.append((0,1+i,1+(i+1)%8))
        for ring in range(2):
            for i in range(8):
                k=1+ring*8;f.append((k+i,k+8+i,k+8+(i+1)%8,k+(i+1)%8))
        pad=mesh(f'WFRL.Landscape.Pad{tx}',v,f,gravel)
        pad.data.materials.append(shoulder)
        for face in list(pad.data.polygons)[16:]:face.material_index=1
    def cluster_centers(count, *, prefer_low, min_separation):
        """Pick visibly separated ecological centers instead of uniform scatter."""
        centers=[]
        for _ in range(count * 140):
            if len(centers) >= count:break
            candidates=[]
            for _ in range(18):
                x=rng.uniform(-900,1950);y=rng.uniform(-820,1050);z=surface(x,y)
                clearance=road_clearance(x,y)
                if clearance < 42:continue
                moisture=lowland_factor(z)
                habitat=moisture if prefer_low else 1.0-moisture
                score=(.18+.82*habitat)*_smoothstep(32,105,clearance)*rng.uniform(.82,1.18)
                candidates.append((score,x,y,z))
            if not candidates:continue
            _,x,y,z=max(candidates)
            if any(math.hypot(x-cx,y-cy)<min_separation for cx,cy,_,_ in centers):continue
            centers.append((x,y,z,rng.uniform(18,44) if prefer_low else rng.uniform(16,38)))
        return centers

    lush_centers=cluster_centers(24,prefer_low=True,min_separation=82)
    dry_centers=cluster_centers(15,prefer_low=False,min_separation=105)

    # Broad, irregular cover patches make the ecological pattern readable in
    # the overview camera; the fine tufts below add the close-range silhouette.
    dark_grass=material('WFRL.Landscape.Tuft.Dark',(.018,.055,.007))
    lush_grass=material('WFRL.Landscape.Tuft.Lush',(.035,.095,.012))
    meadow_grass=material('WFRL.Landscape.Tuft.Meadow',(.07,.105,.02))
    dry_grass=material('WFRL.Landscape.Tuft.Dry',(.14,.085,.022))
    cover_materials=(
        patch_material('WFRL.Landscape.Cover.Dark',(.018,.052,.007)),
        patch_material('WFRL.Landscape.Cover.Lush',(.038,.082,.01)),
        patch_material('WFRL.Landscape.Cover.Meadow',(.07,.082,.018)),
        patch_material('WFRL.Landscape.Cover.Dry',(.10,.06,.014)),
    )
    bare_soil=patch_material('WFRL.Landscape.BareSoil',(.072,.027,.006))

    def patch_mesh(name, specs, materials):
        verts=[];faces=[];material_indices=[];weights=[]
        for patch_index,(cx,cy,base_radius,mat_index) in enumerate(specs):
            rotation=rng.random()*math.tau
            # Keep the entire patch outside the managed road/pad verge.  This
            # is deliberately more conservative than the individual-tuft rule
            # because a broad color patch crossing the road reads as paving.
            safe_radius=max(5.0,(road_clearance(cx,cy)-16.0)/1.55)
            radius=min(base_radius,safe_radius)
            rx=radius*rng.uniform(.85,1.35)
            ry=radius*rng.uniform(.45,.82)
            segments=64;rings=12
            center_index=len(verts)
            layer_offset=.06+.001*patch_index
            verts.append((cx,cy,surface(cx,cy)+layer_offset));weights.append(1.0)
            phase=rng.random()*math.tau
            for ring in range(1,rings+1):
                fraction=ring/rings
                ring_weight=1.0-_smoothstep(0.0,1.0,fraction)
                for step in range(segments):
                    angle=math.tau*step/segments
                    edge=1+.13*math.sin(3*angle+phase)+.065*math.sin(7*angle-phase)
                    px=rx*edge*fraction*math.cos(angle);py=ry*edge*fraction*math.sin(angle)
                    x=cx+px*math.cos(rotation)-py*math.sin(rotation)
                    y=cy+px*math.sin(rotation)+py*math.cos(rotation)
                    verts.append((x,y,surface(x,y)+layer_offset));weights.append(ring_weight)
            for step in range(segments):
                faces.append((center_index,center_index+1+step,center_index+1+(step+1)%segments))
                material_indices.append(mat_index)
            for ring in range(1,rings):
                inner=center_index+1+(ring-1)*segments;outer=inner+segments
                for step in range(segments):
                    faces.append((inner+step,outer+step,
                                  outer+(step+1)%segments,inner+(step+1)%segments))
                    material_indices.append(mat_index)
        obj=mesh(name,verts,faces,materials[0],smooth=True)
        for mat in materials[1:]:obj.data.materials.append(mat)
        for poly,index in zip(obj.data.polygons,material_indices):poly.material_index=index
        colors=obj.data.color_attributes.new(name='WFRL.PatchWeight',type='FLOAT_COLOR',domain='POINT')
        for item,value in zip(colors.data,weights):item.color=(value,value,value,1)
        obj.visible_shadow=False
        obj['distribution_rule']='irregular clusters; road clearance; elevation-weighted'
        obj['cluster_count']=len(specs)
        return obj

    cover_specs=[]
    for index,(x,y,z,spread) in enumerate(lush_centers):
        # The lowest pockets read darker, while adjoining meadow breaks the edge.
        mat_index=0 if lowland_factor(z)>.72 else (1 if index%3 else 2)
        cover_specs.append((x,y,rng.uniform(15,32),mat_index))
        if index%2==0:
            angle=rng.random()*math.tau;distance=rng.uniform(18,36)
            sx=x+math.cos(angle)*distance;sy=y+math.sin(angle)*distance
            if road_clearance(sx,sy)>28:
                cover_specs.append((sx,sy,rng.uniform(7,16),min(2,mat_index+1)))
    for index,(x,y,z,spread) in enumerate(dry_centers):
        if index%2==0:cover_specs.append((x,y,rng.uniform(10,23),3))
    patch_mesh('WFRL.Landscape.GrassCoverPatches',cover_specs,cover_materials)

    soil_specs=[]
    for index,(x,y,z,spread) in enumerate(dry_centers):
        # Offset soil from the dry-grass center so the two layers interlock
        # instead of forming concentric artificial discs.
        angle=rng.random()*math.tau;distance=rng.uniform(12,36)
        sx=x+math.cos(angle)*distance;sy=y+math.sin(angle)*distance
        if road_clearance(sx,sy)>30:soil_specs.append((sx,sy,rng.uniform(7,17),0))
    soil=patch_mesh('WFRL.Landscape.BareSoilPatches',soil_specs,(bare_soil,))
    soil['provenance']='Procedural exposed-soil clusters; presentation only'

    # Low shrubs use three shared meshes/materials, so color varies by habitat
    # without creating a unique mesh datablock for every plant.
    shrub_materials=(
        material('WFRL.Landscape.Shrub.Dark',(.022,.052,.009)),
        material('WFRL.Landscape.Shrub.Mid',(.055,.095,.018)),
        material('WFRL.Landscape.Shrub.Dry',(.155,.13,.052)),
    )
    rockmat=material('WFRL.Landscape.Rock',(.19,.17,.125))
    def instance_data(mat):
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1,radius=1)
        source=bpy.context.object;data=source.data;data.materials.append(mat)
        bpy.data.objects.remove(source,do_unlink=True)
        return data
    bark=material('WFRL.Landscape.Shrub.Stem',(.07,.045,.02))
    def shrub_prototype(mat, seed):
        """Open clusters of curved leaves, shared by all instances of a species."""
        local=random.Random(seed);v=[];f=[];stem_faces=[]
        # Leaves follow several diverging stems instead of a closed polyhedron.
        for branch in range(9):
            angle=branch*math.tau/9+local.uniform(-.3,.3)
            reach=local.uniform(.35,.85);top=local.uniform(.65,1.25)
            start=len(v)
            for t in (0.,.5,1.):
                for side in range(6):
                    a=side*math.tau/6;r=.016*(1-.8*t)
                    v.append((math.cos(angle)*reach*t+r*math.cos(a),
                              math.sin(angle)*reach*t+r*math.sin(a),top*t))
            for segment in range(2):
                for side in range(6):
                    k=start+segment*6;following=(side+1)%6
                    stem_faces.append(len(f))
                    f.append((k+side,k+following,k+6+following,k+6+side))
            for leaf in range(14):
                t=.18+.82*leaf/13
                cx=math.cos(angle)*reach*t;cy=math.sin(angle)*reach*t;cz=top*t
                a=angle+local.uniform(-2.0,2.0);length=local.uniform(.13,.27)
                dx,dy=math.cos(a)*length,math.sin(a)*length
                k=len(v)
                v.extend(((cx,cy,cz),(cx+dx*.5-dy*.38,cy+dy*.5+dx*.38,cz+.025),
                          (cx+dx,cy+dy,cz+.06),(cx+dx*.5+dy*.38,cy+dy*.5-dx*.38,cz+.025),
                          (cx+dx*.5,cy+dy*.5,cz+.08)))
                f.extend(((k+4,k+1,k),(k+4,k+2,k+1),(k+4,k+3,k+2),(k+4,k,k+3)))
        obj=mesh('WFRL.Landscape.ShrubPrototype',v,f,mat,smooth=False)
        data=obj.data;data.materials.append(bark)
        for index in stem_faces:data.polygons[index].material_index=1
        bpy.data.objects.remove(obj,do_unlink=True)
        return data
    shrub_data=tuple(shrub_prototype(mat,140+i) for i,mat in enumerate(shrub_materials))
    rock_data=instance_data(rockmat)
    shrub_count=0
    for patch_index,(cx,cy,cz,spread) in enumerate(lush_centers):
        attempts=72 if lowland_factor(cz)>.65 else 48
        for _ in range(attempts):
            x=rng.gauss(cx,spread*.62);y=rng.gauss(cy,spread*.62);z=surface(x,y)
            if rng.random()>vegetation_factor(x,y,z):continue
            palette=0 if lowland_factor(z)>.7 and rng.random()<.7 else 1
            data=shrub_data[palette]
            obj=bpy.data.objects.new(f'WFRL.Landscape.Shrub{shrub_count}',data);collection.objects.link(obj)
            r=rng.uniform(.65,2.4);obj.location=(x,y,z-.04);obj.scale=(r,r*rng.uniform(.65,.95),r*rng.uniform(.45,.72));obj.rotation_euler[2]=rng.random()*math.tau
            obj['cluster']=patch_index;shrub_count+=1
    for patch_index,(cx,cy,cz,spread) in enumerate(dry_centers):
        for _ in range(22):
            x=rng.gauss(cx,spread*.7);y=rng.gauss(cy,spread*.7);z=surface(x,y)
            if road_clearance(x,y)<18 or rng.random()>.58:continue
            obj=bpy.data.objects.new(f'WFRL.Landscape.Shrub{shrub_count}',shrub_data[2]);collection.objects.link(obj)
            r=rng.uniform(.45,1.55);obj.location=(x,y,z-.03);obj.scale=(r,r*rng.uniform(.55,.9),r*rng.uniform(.35,.58));obj.rotation_euler[2]=rng.random()*math.tau
            obj['cluster']=len(lush_centers)+patch_index;shrub_count+=1

    rock_count=0
    for patch_index,(cx,cy,_,spread) in enumerate(dry_centers):
        for _ in range(24):
            x=rng.gauss(cx,spread*.72);y=rng.gauss(cy,spread*.72)
            if road_clearance(x,y)<16:continue
            z=surface(x,y);obj=bpy.data.objects.new(f'WFRL.Landscape.Rock{rock_count}',rock_data);collection.objects.link(obj)
            r=rng.uniform(.25,1.25);obj.location=(x,y,z+r*.2);obj.scale=(r,r*rng.uniform(.65,1.15),r*rng.uniform(.38,.7));obj.rotation_euler[2]=rng.random()*math.tau
            obj['cluster']=patch_index;rock_count+=1

    # Curved blade clusters are batched into one mesh. Gaussian placement
    # produces dense cores and soft edges instead of uniformly sprinkled dots.
    grass_verts=[];grass_faces=[];grass_material_indices=[]
    tuft_count=0
    def add_tuft(x,y,z,height_m,width,material_index):
        nonlocal tuft_count
        tuft_count+=1
        angle=rng.random()*math.tau
        # Three bent ribbons, each with one broad base quad and a tapered
        # tip triangle. No duplicated zero-width tip or invisible extra rings.
        for blade in range(3):
            a=angle+blade*2.399;h=height_m*rng.uniform(.55,1.0)
            lean=h*rng.uniform(.25,.6);w=width*rng.uniform(.28,.48)
            ox,oy=math.cos(a)*width*.5,math.sin(a)*width*.5
            k=len(grass_verts)
            for t in (0., .5):
                bend=lean*t*t;half=w*(1-t)*.5
                cx=x+ox+math.cos(a)*bend;cy=y+oy+math.sin(a)*bend
                grass_verts.extend(((cx-math.sin(a)*half,cy+math.cos(a)*half,z+h*t),
                                    (cx+math.sin(a)*half,cy-math.cos(a)*half,z+h*t)))
            grass_verts.append((x+ox+math.cos(a)*lean,y+oy+math.sin(a)*lean,z+h))
            grass_faces.extend(((k,k+1,k+3,k+2),(k+2,k+3,k+4)))
            grass_material_indices.extend((material_index,material_index))
    for patch_index,(cx,cy,cz,spread) in enumerate(lush_centers):
        density=380 if lowland_factor(cz)>.65 else 260
        for _ in range(density):
            x=rng.gauss(cx,spread*.72);y=rng.gauss(cy,spread*.72);z=surface(x,y)
            factor=vegetation_factor(x,y,z)
            if rng.random()>factor:continue
            moisture=lowland_factor(z)
            mat_index=0 if moisture>.72 and rng.random()<.56 else (1 if rng.random()<.72 else 2)
            add_tuft(x,y,z+.04,rng.uniform(.38,1.18)*(1+.22*moisture),rng.uniform(.07,.18),mat_index)
    for cx,cy,cz,spread in dry_centers:
        for _ in range(170):
            x=rng.gauss(cx,spread*.82);y=rng.gauss(cy,spread*.82);z=surface(x,y)
            if road_clearance(x,y)<15 or rng.random()>.74:continue
            add_tuft(x,y,z+.04,rng.uniform(.42,1.32),rng.uniform(.055,.14),3)
    grass=mesh('WFRL.Landscape.GrassTufts',grass_verts,grass_faces,dark_grass,smooth=False)
    for mat in (lush_grass,meadow_grass,dry_grass):grass.data.materials.append(mat)
    for poly,index in zip(grass.data.polygons,grass_material_indices):poly.material_index=index
    grass['distribution_rule']='Gaussian clusters; sparse within 58m of road; denser below 11m elevation'
    grass['cluster_count']=len(lush_centers)+len(dry_centers)
    grass['tuft_count']=tuft_count
    # Continuous distant ground closes gaps between the local terrain and ridges.
    mesh('WFRL.Landscape.FarGround',
         [(-40000,-40000,-35),(40000,-40000,-35),(40000,40000,-35),(-40000,40000,-35)],
         [(0,1,2,3)],get_material('terrain'))
    # One continuous terrain palette and camera-distance haze across foothills
    # and ridges avoids the previous three solid-color silhouette bands.
    mat = terrain.data.materials[0]
    for layer,(distance,base_height) in enumerate((
            (3700,180), (5200,340), (7000,530)),start=1):
        v=[];f=[]
        nx,ncross=1000,32
        for j in range(ncross+1):
            cross=j/ncross
            y=distance+(cross-.5)*1800
            for i in range(nx+1):
                x=-26000+i*56000/nx
                profile=base_height+55*math.sin(x*.0013+layer*1.8)+25*math.sin(x*.003+layer)
                profile+=(12/layer)*math.sin(x*.008+layer*2)
                # Smooth foothills meet the far ground with zero slope.
                envelope=math.sin(math.pi*cross)**2
                z=-35+envelope*profile
                v.append((x,y,z))
        for j in range(ncross):
            for i in range(nx):
                k=j*(nx+1)+i;f.append((k,k+1,k+nx+2,k+nx+1))
        ridge=mesh(f'WFRL.Landscape.DistantRidge{layer}',v,f,mat)
        ridge['provenance']='Presentation-only distant ridge; excluded from simulation terrain'
    optimize_shrubs(collection)
    return terrain


def optimize_shrubs(collection):
    """Shared reduced meshes outside turbine inspection zones; no per-frame work.

    Nearby shrubs keep their full leaf geometry. Far shrubs retain placement,
    materials and silhouette using one cached reduction per original mesh.
    """
    import bpy
    shrubs = [o for o in collection.objects if o.type == 'MESH'
              and o.name.startswith('WFRL.Landscape.Shrub')]
    sources = {}
    before = sum(len(o.data.vertices) for o in shrubs)
    for obj in shrubs:
        if obj.get('wfrl_shrub_optimized'):
            continue
        near = min(math.hypot(obj.location.x - x, obj.location.y) for x in TURBINE_X) < 180
        if near:
            continue
        key = obj.data.as_pointer()
        if key not in sources:
            temp = bpy.data.objects.new('WFRL.ShrubReduction', obj.data.copy())
            collection.objects.link(temp)
            original = temp.data
            modifier = temp.modifiers.new('Distant leaf reduction', 'DECIMATE')
            modifier.ratio = .18
            graph = bpy.context.evaluated_depsgraph_get()
            reduced = bpy.data.meshes.new_from_object(temp.evaluated_get(graph), depsgraph=graph)
            reduced.name = obj.data.name + '.Distant'
            sources[key] = reduced
            bpy.data.objects.remove(temp, do_unlink=True)
            if original.users == 0:
                bpy.data.meshes.remove(original)
        obj.data = sources[key]
        obj['wfrl_shrub_optimized'] = True
    after = sum(len(o.data.vertices) for o in shrubs)
    return {'before': before, 'after': after, 'objects': len(shrubs)}
