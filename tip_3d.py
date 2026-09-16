"""Compatibility entry point; implementation lives in wfrl.viz.tip_geometry."""
if __name__ == '__main__':
    import runpy
    runpy.run_module('wfrl.viz.tip_geometry', run_name='__main__')
else:
    from wfrl.viz.tip_geometry import (TurbineGeometry, CameraModel, tip_3d_from_pixel,
                                       visible_arc, RpmAzimuthIntegrator)
