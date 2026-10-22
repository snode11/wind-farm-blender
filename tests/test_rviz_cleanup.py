import warnings

import numpy as np

from wfrl.viz.rviz_app import FastFarmSource, _finite_mean, _finite_min


def test_fastfarm_source_close_purges_runtime_case():
    calls = []

    class Driver:
        def close(self, **kwargs):
            calls.append(kwargs)

    source = object.__new__(FastFarmSource)
    source.drv = Driver()
    source.close()

    assert calls == [{"purge": True}]


def test_telemetry_reducers_accept_missing_values_without_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        assert np.isnan(_finite_mean([np.nan, np.nan]))
        assert np.isnan(_finite_min([np.nan, np.nan]))
        assert _finite_mean([1.0, np.nan, 3.0]) == 2.0
        assert _finite_min([1.0, np.nan, 3.0]) == 1.0
