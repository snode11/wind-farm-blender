from io import BytesIO

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
import numpy as np

from scripts.figures import make_level_vs_ref_figure as script


def test_saved_missing_samples_are_gaps_and_excluded_from_boxplots(monkeypatch, capsys):
    yaw = np.array([[10., np.nan, 0.], [np.nan, np.nan, 0.],
                    [999., 99., 0.], [30., np.nan, 0.]])
    flags = np.ones(yaw.shape, bool)
    flags[2, :2] = False
    power = np.array([[1., 2., 3.], [2., np.nan, 4.],
                      [2., 99., 4.], [2., 3., 4.]])
    power_flags = np.ones(power.shape, bool)
    power_flags[2, 1] = False
    archive = BytesIO()
    np.savez(archive, yaw=yaw, yaw_valid=flags, power=power, power_valid=power_flags)
    archive.seek(0)
    samples = []
    original = Axes.boxplot

    def record_boxplot(self, values, *args, **kwargs):
        for values_for_turbine in values:
            assert len(values_for_turbine) > 0
            assert np.isfinite(values_for_turbine).all()
            samples.append(list(values_for_turbine))
        return original(self, values, *args, **kwargs)

    monkeypatch.setattr(Axes, 'boxplot', record_boxplot)
    with np.load(archive) as data:
        fig = script.build_figure(data, data)
    try:
        assert samples == [[10., 30.], [0.] * 4, [10., 30.], [0.] * 4]
        np.testing.assert_equal(fig.axes[0].lines[0].get_ydata(), [10., np.nan, np.nan, 30.])
        np.testing.assert_equal(fig.axes[1].lines[0].get_ydata(), [6., np.nan, np.nan, 9.])
        assert any(text.get_text() == '20.0°' for text in fig.axes[2].texts)
        output = capsys.readouterr().out
        assert '20.000 °（有效样本数=2）; median=20.00°, range=[10.00, 30.00]°' in output
        assert 'T2: 无有效数据（有效样本数=0）' in output
        assert '7.500 MW（有效样本数=2）' in output
        assert 'nan' not in output.lower()
    finally:
        plt.close(fig)


def test_empty_comparison_reports_unavailable_without_boxplots(capsys):
    data = {key: np.full((3, 3), np.nan) for key in ('yaw', 'power')}
    fig = script.build_figure(data, data)
    try:
        assert not fig.axes[2].patches
        assert np.isnan(fig.axes[1].lines[0].get_ydata()).all()
        assert '无有效数据（有效样本数=0）' in fig.axes[1].get_title()
        output = capsys.readouterr().out
        assert '增益: 无有效数据' in output
        assert '0.000 MW' not in output
        assert 'nan' not in output.lower()
    finally:
        plt.close(fig)
