from io import BytesIO

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pytest

from scripts.figures import plot_wake_steering as script


def test_saved_masks_produce_plot_gaps_and_counted_statistics(capsys):
    yaw = np.array([[10., 0., 0.], [np.nan, 0., 0.], [999., 0., 0.], [30., 0., 0.]])
    power = np.array([[1., 2., 3.], [2., np.nan, 4.], [2., 99., 4.], [2., 3., 4.]])
    yaw_valid = np.ones(yaw.shape, bool)
    yaw_valid[2, 0] = False
    power_valid = np.ones(power.shape, bool)
    power_valid[2, 1] = False
    archive = BytesIO()
    np.savez(archive, yaw=yaw, power=power, yaw_valid=yaw_valid, power_valid=power_valid)
    archive.seek(0)
    with np.load(archive) as data:
        fig = script.build_figure(data)
    try:
        np.testing.assert_equal(fig.axes[0].lines[0].get_ydata(), [10., np.nan, np.nan, 30.])
        np.testing.assert_equal(fig.axes[1].lines[1].get_ydata(), [2., np.nan, np.nan, 3.])
        np.testing.assert_equal(fig.axes[2].lines[0].get_ydata(), [6., np.nan, np.nan, 9.])
        output = capsys.readouterr().out
        assert '20.000 deg（有效样本数=2）' in output
        assert '7.500 MW（有效样本数=2）' in output
        assert 'nan' not in output.lower()
        assert '+5.3%' not in fig.axes[2].get_title()
    finally:
        plt.close(fig)


def test_legacy_archive_all_nan_has_gaps_and_no_zero_summary(capsys):
    data = {key: np.full((3, 3), np.nan) for key in ('yaw', 'power')}
    fig = script.build_figure(data)
    try:
        assert np.isnan(fig.axes[2].lines[0].get_ydata()).all()
        assert '无有效数据（有效样本数=0）' in fig.axes[2].get_title()
        output = capsys.readouterr().out
        assert 'Gain: 无有效数据' in output
        assert '0.000 MW' not in output
        assert 'nan' not in output.lower()
    finally:
        plt.close(fig)


def test_plot_rejects_misaligned_saved_mask():
    with pytest.raises(ValueError, match='yaw_valid'):
        script.masked_data(dict(yaw=np.full((3, 3), np.nan), yaw_valid=[True] * 3), 'yaw')
