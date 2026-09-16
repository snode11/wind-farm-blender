"""Random gust events remain reproducible, smooth, bounded and nonperiodic."""
import importlib.util
from pathlib import Path
import sys
import numpy as np

SCRIPTS=Path(__file__).resolve().parents[2]/'scripts/lidar'
sys.path.insert(0,str(SCRIPTS))
spec=importlib.util.spec_from_file_location('random_gust',SCRIPTS/'run_random_gust_preview.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_seeded_events():
    t=np.arange(0,80,.001)
    y,events=module.pulses(t,20260916,80)
    again,copy=module.pulses(t,20260916,80)
    assert events==copy and np.array_equal(y,again)
    assert not np.array_equal(y,module.pulses(t,20260917,80)[0])
    assert np.all(y[t<19]==0) and 0<=y.min() and y.max()<=6
    assert len(events)>=4
    assert len({round(e['duration_s'],3) for e in events})==len(events)
    for e in events:
        assert 2<=e['duration_s']<=5 and 4<=e['increment_mps']<=6
        # The half-cosine pulse meets the background with zero value/slope.
        edges=np.array([e['start_s'],e['start_s']+e['duration_s']])
        values,_=module.pulses(edges,20260916,80)
        assert np.allclose(values,0,atol=1e-12)
    assert np.abs(np.diff(y)).max()<.01
