"""Permanent recessed blade defects, cut once before animation. No rendering."""
import json
import math
import bpy
import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from .model import PROJECT


def read_defects():
    return json.loads((PROJECT/'geometry/defects.json').read_text())


def cut_defects(blades, collection, material):
    records=[]
    for spec in read_defects()['defects']:
        blade=blades[spec['blade']-1]
        mesh=blade.data
        mesh.calc_loop_triangles()
        tree=BVHTree.FromPolygons([v.co for v in mesh.vertices],
            [tuple(t.vertices) for t in mesh.loop_triangles],all_triangles=True)
        centre,normal,_,_=tree.ray_cast(Vector((20,0,spec['span_m'])),Vector((-1,0,0)),40)
        if centre is None or normal.x<.5:
            raise ValueError('Defect placement did not hit the downstream blade surface')
        tangent=Vector((0,1,0))
        tangent=(tangent-normal*tangent.dot(normal)).normalized()
        radial=normal.cross(tangent).normalized()
        def point(u,v,w):
            return centre+tangent*u+radial*v+normal*w
        verts,faces=[],[]
        if spec['kind']=='crack':
            # Jagged narrow V groove, tapered ends; four vertices per section.
            rows=[(-.5,0,.025),(-.40,-.12,.55),(-.22,.10,.82),
                  (0,0,1),(.19,.13,.75),(.38,-.08,.50),(.5,0,.025)]
            for x,offset,width in rows:
                mid=offset*(1-width)*spec['max_width_m']
                half=width*spec['max_width_m']/2
                depth=spec['depth_m']
                # Width is specified at the surface, not at the cutter's top.
                # Extrapolate the V walls upward so a 50-mm overhang does not
                # accidentally shrink an 80-mm opening to about 20 mm.
                top_half=half*(.05+depth)/depth
                u=x*spec['length_m']
                verts.extend([point(u,mid-top_half,.05),point(u,mid+top_half,.05),
                              point(u,mid+.0001,-depth),point(u,mid-.0001,-depth)])
            for k in range(len(rows)-1):
                for j in range(4):
                    a=k*4+j;b=k*4+(j+1)%4
                    faces.append((a,b,b+4,a+4))
            faces.extend([(3,2,1,0),tuple(range(len(verts)-4,len(verts)))])
        elif spec['kind']=='pit':
            radius=spec['diameter_m']/2;depth=spec['depth_m']
            sphere_radius=(radius*radius+depth*depth)/(2*depth)
            offset=sphere_radius-depth
            # Dense spherical cap intersections remain geometric at 4K.
            segments,rings=96,64
            verts.append(point(0,0,offset+sphere_radius))
            for i in range(1,rings):
                theta=math.pi*i/rings
                for j in range(segments):
                    phi=math.tau*j/segments
                    verts.append(point(sphere_radius*math.sin(theta)*math.cos(phi),
                        sphere_radius*math.sin(theta)*math.sin(phi),offset+sphere_radius*math.cos(theta)))
            bottom=len(verts);verts.append(point(0,0,-depth))
            for j in range(segments):
                faces.append((0,1+j,1+(j+1)%segments))
            for i in range(rings-2):
                for j in range(segments):
                    a=1+i*segments+j;b=1+i*segments+(j+1)%segments
                    faces.append((a,b,b+segments,a+segments))
            last=1+(rings-2)*segments
            for j in range(segments):
                faces.append((last+j,bottom,last+(j+1)%segments))
        else:
            raise ValueError(f'Unknown defect {spec["kind"]}')
        cutter_mesh=bpy.data.meshes.new('GW184.DefectCutter')
        cutter_mesh.from_pydata(verts,[],faces)
        bm=bmesh.new();bm.from_mesh(cutter_mesh)
        bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces))
        bm.to_mesh(cutter_mesh);bm.free()
        cutter=bpy.data.objects.new('GW184.DefectCutter',cutter_mesh)
        collection.objects.link(cutter)
        cutter.parent=blade.parent
        cutter_mesh.materials.append(blade.data.materials[0])
        cutter_mesh.materials.append(material)
        for poly in cutter_mesh.polygons:
            poly.material_index=1
            poly.use_smooth=spec['kind']=='pit'
        blade.data.materials.append(material)
        bpy.context.view_layer.update()
        before=len(mesh.vertices)
        modifier=blade.modifiers.new(spec['id'],'BOOLEAN')
        modifier.operation='DIFFERENCE';modifier.solver='EXACT';modifier.object=cutter
        try:
            with bpy.context.temp_override(object=blade,active_object=blade):
                bpy.ops.object.modifier_apply(modifier=modifier.name)
        finally:
            bpy.data.objects.remove(cutter,do_unlink=True)
            bpy.data.meshes.remove(cutter_mesh)
        interior=[p for p in blade.data.polygons if p.material_index==1]
        if not interior or len(blade.data.vertices)<=before:
            raise RuntimeError('Boolean failed to create a recessed defect')
        blade['defect']=json.dumps(spec)
        records.append(dict(spec=spec,object=blade.name,centre_local_m=list(centre),
            normal_local=list(normal),tangent_local=list(tangent),radial_local=list(radial),
            interior_faces=len(interior)))
    return records
