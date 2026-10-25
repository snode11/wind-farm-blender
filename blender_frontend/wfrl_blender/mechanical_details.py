"""Presentation-only service details, parented to the existing motion rig.

These fittings are illustrative, not additional NREL engineering dimensions.
"""
import math


def blade_root_fitting_mesh(role, model):
    """Build local presentation fittings; NREL dimensions keep their own contract.

    The synthetic 2.30 m root uses a short neck seated inside the hub. Its
    flange and seal follow that neck, independently of the NREL pitch bearing.
    All parts remain circular below the pitching blade's first source section.
    """
    from .scene_builder import _primitive_mesh
    import bpy
    if role == 'RootFairing' and model == 'nrel5mw':
        mesh = _primitive_mesh('Manual.RootFairing', 'cone', segments=96,
                               radius1=1.79, radius2=1.771, depth=.18)
    else:
        vertices, faces = [], []
        if role == 'RootFairing':
            profile = ((1.30, 1.12), (1.30, 1.22), (1.285, 1.30),
                       (1.23, 1.38), (1.185, 1.46), (1.172, 1.53))
            n = 96
            for radius, z in profile:
                vertices.extend((radius*math.cos(math.tau*i/n), radius*math.sin(math.tau*i/n),
                                 z - 1.62) for i in range(n))
            faces = [(k*n+i, k*n+(i+1)%n, (k+1)*n+(i+1)%n, (k+1)*n+i)
                     for k in range(len(profile)-1) for i in range(n)]
            faces.extend((tuple(reversed(range(n))),
                          tuple((len(profile)-1)*n+i for i in range(n))))
        else:
            dimensions = ({'PitchSeal': (1.17, .025, 1.49), 'RootFlange': (1.29, .055, 1.265)}
                          if model == 'rootkink' else
                          {'PitchSeal': (1.79, .045, 1.69), 'RootFlange': (1.84, .075, 1.53)})
            radius, thickness, z = dimensions[role]
            n, m = 64, 8
            for i in range(n):
                a = math.tau*i/n
                for j in range(m):
                    b = math.tau*j/m
                    r = radius + thickness*math.cos(b)
                    vertices.append((r*math.cos(a), r*math.sin(a), z+thickness*math.sin(b)))
            faces = [(i*m+j, ((i+1)%n)*m+j, ((i+1)%n)*m+(j+1)%m, i*m+(j+1)%m)
                     for i in range(n) for j in range(m)]
        mesh = bpy.data.meshes.new('Manual.' + role)
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
    for face in mesh.polygons:
        face.use_smooth = len(face.vertices) <= 4
    return mesh


def recess_yaw_seal(obj, tower_height):
    """Keep the illustrative seal inside the 1.90 m pedestal envelope."""
    if obj.get('wfrl_seal_geometry_revision', 0) >= 1:
        return
    n, m = 64, 8
    if len(obj.data.vertices) != n * m:
        raise ValueError('Unexpected yaw seal mesh: ' + obj.name)
    for i in range(n):
        a = math.tau * i / n
        for j in range(m):
            b = math.tau * j / m
            radius = 1.89 + .008 * math.cos(b)
            obj.data.vertices[i * m + j].co = (
                radius * math.cos(a), radius * math.sin(a),
                tower_height + .13 + .008 * math.sin(b))
    obj.data.update()
    obj['wfrl_seal_geometry_revision'] = 1


def add_mechanical_details(collection, root, yaw, rotor, prefix, scalars, shell, *, material_factory=None):
    from .scene_builder import _mesh_object, _finish
    from .materials import get_material

    material_for = material_factory or get_material

    def part(name, vertices, faces, parent, material='hub', bevel=0):
        obj = _mesh_object(collection, prefix + '.' + name, vertices, faces, material_for(material))
        obj.parent = parent
        obj['geometry_source'] = 'Illustrative service fitting; presentation only'
        _finish(obj, bevel=bevel)
        return obj

    def box(name, center, half, parent, material='hub', bevel=.025):
        x,y,z=center; a,b,c=half
        vertices=[(x+sx*a,y+sy*b,z+sz*c) for sx,sy,sz in
                  ((-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),
                   (-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1))]
        return part(name,vertices,[(3,2,1,0),(4,5,6,7),(0,1,5,4),
                                  (1,2,6,5),(2,3,7,6),(3,0,4,7)],parent,material,bevel)

    def ring(name, radius, thickness, z, parent, material='hub'):
        vertices=[];faces=[];n=64;m=8
        for i in range(n):
            a=math.tau*i/n
            for j in range(m):
                b=math.tau*j/m;r=radius+thickness*math.cos(b)
                vertices.append((r*math.cos(a),r*math.sin(a),z+thickness*math.sin(b)))
        for i in range(n):
            for j in range(m):
                faces.append((i*m+j,((i+1)%n)*m+j,((i+1)%n)*m+(j+1)%m,i*m+(j+1)%m))
        obj=part(name,vertices,faces,parent,material)
        _finish(obj,smooth=True)
        return obj

    # Access opening is on the road-facing side. A thin reveal separates the
    # door from the curved tower; steps descend onto the concrete plinth.
    box('ServiceDoor.Reveal',(0,-2.985,2.13),(.64,.045,1.20),root,'graphite',.08)
    box('ServiceDoor.Panel',(0,-3.04,2.13),(.57,.035,1.12),root,'tower',.065)
    box('ServiceDoor.Handle',(.41,-3.115,2.12),(.035,.04,.15),root,'metal',.018)
    for index,z in enumerate((1.4,2.9)):
        box(f'ServiceDoor.Hinge{index}',(-.55,-3.095,z),(.055,.035,.12),root,'metal')
    for index in range(3):
        top=1.03-index*.105
        box(f'AccessStep{index}',(0,-3.42-index*.34,(.78+top)/2),(.79,.19,(top-.78)/2),root,'foundation',.025)
    for side in (-1,1):
        for j,y in enumerate((-3.32,-4.08)):
            box(f'AccessRail.Post{side}.{j}',(side*.76,y,1.39),(.027,.027,.48),root,'metal',.01)
        box(f'AccessRail.Top{side}',(side*.76,-3.7,1.87),(.03,.43,.03),root,'metal',.012)
    # Circumferential welds stay subtle at close range.
    from .turbine_geometry import geometry_data
    stations=geometry_data()['tower_stations']
    for z in (28.,56.):
        i=next(i for i in range(len(stations)-1) if stations[i][0]<=z<=stations[i+1][0])
        za,da=stations[i];zb,db=stations[i+1]
        ring(f'TowerWeld{int(z)}',.5*(da+(db-da)*(z-za)/(zb-za)),.018,z,root,'tower')
    seal = ring('YawSeal',1.89,.008,scalars['TowerHt']+.13,root,'rubber')
    recess_yaw_seal(seal, scalars['TowerHt'])
    ring('YawSkirt',1.90,.025,scalars['Twr2Shft']+.15-shell['height']/2,yaw,'rubber')

    # Roof cover perimeter and side louvres reveal shell thickness without
    # changing the nacelle envelope or adding speculative internal machinery.
    box('RoofGasket',(.65,0,shell['height']/2+.045),(1.22,.84,.012),yaw,'rubber',.035)
    # Fine longitudinal shell join at the widest section. Dimensions are
    # decorative only; the original engineering envelope stays unchanged.
    shell_x=shell['nacelle_x_bias']*scalars['OverHang']
    for side in (-1,1):
        box(f'ShellJoin{side}',(shell_x,side*(shell['width']/2+.003),.15),
            (shell['length']*.35,.006,.007),yaw,'graphite',.003)
    for side in (-1,1):
        box(f'Vent.Recess{side}',(.85,side*1.55,.90),(.82,.045,.29),yaw,'graphite',.04)
        for j in range(6):
            box(f'Vent.Louvre{side}.{j}',(.85,side*1.615,.675+j*.09),(.76,.035,.018),yaw,'metal',.01)
    # Blade pitch-bearing rings share precone/azimuth, while the circular
    # fittings remain invariant under individual pitch rotation.
    import bpy
    for index in range(1,4):
        parent=bpy.data.objects[prefix+f'.Blade{index}.PitchRoot']
        ring(f'Blade{index}.PitchSeal',1.79,.045,1.69,parent,'rubber')
        ring(f'Blade{index}.RootFlange',1.84,.075,1.53,parent,'metal')
