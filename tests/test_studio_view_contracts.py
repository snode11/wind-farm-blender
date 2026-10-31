"""Qt view ownership and measured-pitch geometry without a render window."""
from types import SimpleNamespace as NS
import threading
import numpy as np
import pytest
from wfrl.studio.view import SceneView
from wfrl.viz import animate
from wfrl.viz.geometry import load_turbine_geometry


@pytest.mark.parametrize('topic,index,remaining', [
    (None,1,{('yaw',0),('pitch',0),('yaw',2),('pitch',2)}),
    ('yaw',None,{('pitch',0),('pitch',1),('pitch',2)}),
    ('yaw',1,{('yaw',0),('pitch',0),('pitch',1),('yaw',2),('pitch',2)}),
    (None,None,set())])
def test_manual_lock_release_matches_each_selection(topic,index,remaining):
    view=object.__new__(SceneView)
    view._manual={(t,i):1. for i in range(3) for t in ('yaw','pitch')}
    view.clear_manual(topic,index)
    assert view.manual_locks()==remaining


def turbine():
    geo=load_turbine_geometry()
    blade=geo.blade_surface(subdiv=1)
    return dict(real=True, geo=geo, blade_base=blade.points.copy(), blade_xi=np.asarray(blade['xi']),
        blade_meshes=[blade.copy() for _ in range(3)], blade_scale=1., x=0., y=0.,
        hub_c=np.array([0,0,geo.hub_height]), M_hub=geo.rotor_assembly(0,0) @ animate._rot_y(-geo.precone),
        precone=geo.precone, nac=NS(), blades=[NS() for _ in range(3)])


@pytest.mark.parametrize('pitch',[0.,30.,-30.])
def test_background_deformation_matches_synchronous_clearance_and_display(pitch):
    background,sync=turbine(),turbine()
    view=object.__new__(SceneView)
    view._solve_stop=threading.Event(); view._solve_evt=threading.Event()
    view._solve_lock=threading.Lock(); view._wake_req=None
    mf=np.array([[5e6,6e6,7e6]]); me=np.array([[-1e6,-2e6,-3e6]])
    view._deform_req=(mf,me,np.array([pitch])); view.flex_scale=4.;view.turbines=[background]
    class Once:
        def wait(self,_): pass
        def clear(self): view._solve_stop.set()
    # Finish the current request, then end the loop at its next boundary.
    real_lock=view._solve_lock
    class CompletionLock:
        def __enter__(self): real_lock.acquire()
        def __exit__(self,*_):
            real_lock.release()
            if getattr(view,'_deform_out',None) is not None: view._solve_stop.set()
    view._solve_lock=CompletionLock();view._solve_evt.set()
    view._solve_loop()
    result=view._deform_out[0]
    animate.update_turbine(sync,15.,175.,pitch,m_flap=mf[0],m_edge=me[0],flex_scale=4.)
    np.testing.assert_allclose(result['clearance'],sync['clearance'],rtol=0,atol=1e-12)
    assert result['scale_used']==sync['flex_scale_used']
    for expected,mesh in zip(result['points'],sync['blade_meshes']): np.testing.assert_allclose(expected,mesh.points)
    expected=animate._rot_z(15.) @ sync['M_hub'] @ animate._rot_x(175.) @ animate._rot_y(sync['precone']) @ animate._rot_z(-pitch)
    np.testing.assert_allclose(sync['blades'][0].user_matrix,expected,atol=1e-12)
