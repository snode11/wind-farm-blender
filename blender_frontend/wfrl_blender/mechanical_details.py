"""Presentation-only service details, parented to the existing motion rig.

These fittings are illustrative, not additional NREL engineering dimensions.
"""
import math


def add_mechanical_details(collection, root, yaw, rotor, prefix, scalars, shell):
    from .scene_builder import _mesh_object, _finish
    from .materials import get_material

    def part(name, vertices, faces, parent, material='hub', bevel=0):
        obj = _mesh_object(collection, prefix + '.' + name, vertices, faces, get_material(material))
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
    box('ServiceDoor.Handle',(.41,-3.115,2.12),(.035,.04,.15),root,'hub',.018)
    for index,z in enumerate((1.4,2.9)):
        box(f'ServiceDoor.Hinge{index}',(-.55,-3.095,z),(.055,.035,.12),root)
    for index in range(3):
        top=1.03-index*.105
        box(f'AccessStep{index}',(0,-3.42-index*.34,(.78+top)/2),(.79,.19,(top-.78)/2),root,'foundation',.025)
    for side in (-1,1):
        for j,y in enumerate((-3.32,-4.08)):
            box(f'AccessRail.Post{side}.{j}',(side*.76,y,1.39),(.027,.027,.48),root,'hub',.01)
        box(f'AccessRail.Top{side}',(side*.76,-3.7,1.87),(.03,.43,.03),root,'hub',.012)
    # Circumferential welds stay subtle at close range.
    from .turbine_geometry import geometry_data
    stations=geometry_data()['tower_stations']
    for z in (28.,56.):
        i=next(i for i in range(len(stations)-1) if stations[i][0]<=z<=stations[i+1][0])
        za,da=stations[i];zb,db=stations[i+1]
        ring(f'TowerWeld{int(z)}',.5*(da+(db-da)*(z-za)/(zb-za)),.018,z,root,'tower')
    ring('YawSeal',1.965,.018,scalars['TowerHt']+.13,root,'graphite')
    ring('YawSkirt',1.90,.025,scalars['Twr2Shft']+.15-shell['height']/2,yaw)

    # Roof cover perimeter and side louvres reveal shell thickness without
    # changing the nacelle envelope or adding speculative internal machinery.
    box('RoofGasket',(.65,0,shell['height']/2+.045),(1.22,.84,.012),yaw,'graphite',.035)
    # Fine longitudinal shell join at the widest section. Dimensions are
    # decorative only; the original engineering envelope stays unchanged.
    shell_x=shell['nacelle_x_bias']*scalars['OverHang']
    for side in (-1,1):
        box(f'ShellJoin{side}',(shell_x,side*(shell['width']/2+.003),.15),
            (shell['length']*.35,.006,.007),yaw,'graphite',.003)
    for side in (-1,1):
        box(f'Vent.Recess{side}',(.85,side*1.55,.90),(.82,.045,.29),yaw,'graphite',.04)
        for j in range(6):
            box(f'Vent.Louvre{side}.{j}',(.85,side*1.615,.675+j*.09),(.76,.035,.018),yaw,'hub',.01)
    # Blade pitch-bearing rings share precone/azimuth, while the circular
    # fittings remain invariant under individual pitch rotation.
    import bpy
    for index in range(1,4):
        parent=bpy.data.objects[prefix+f'.Blade{index}.PitchRoot']
        ring(f'Blade{index}.PitchSeal',1.79,.045,1.69,parent,'graphite')
        ring(f'Blade{index}.RootFlange',1.84,.075,1.53,parent)
