"""Detection parameters fitted to a measured CTSAI-X100 vendor PLY sequence.

Reference sequence: CTSAI-X100 8T8R, drone at 0 deg out to 320 m, 1832 RawPoints PLY frames at 33 Hz
(frame numbers 4163-5994). The fit is reproduced by scripts/radar_x100/calibrate_from_ply.py.

Fitted from the vendor output:
  - range / azimuth / elevation error: median jitter of 33 persistent static scatterers;
  - merge widths: datasheet widths x 2.5, matching ~16 reported points per extended object
    (1.96 m x 5.35 deg x 0.73 deg median extent); Swerling 1 fluctuation;
  - per-cell false-alarm probability: 5.7 sporadic near-range points per frame over the search grid,
    an upper bound because sporadic returns also include unmodelled transient clutter;
  - processing gain: drone detection rate 0.54 / 0.52 / 0.22 / 0.09 at 172 / 225 / 277 / 313 m
    with the drone's median vendor RCS (-3.1 dBsm);
  - vendor snr offset, mag = slope * snr + intercept + N(0, std): far-target snr median and the
    mag-snr regression of all points.
Not fitted: surface RCS density and incidence exponent of blade surfaces (no blade measurement exists),
the ms (moving/static) rule (taken as |v| >= merge velocity width) and ground or tower clutter.
"""
from dataclasses import replace

from .detection import DetectionConfig

REFERENCE = ('CTSAI-X100 8T8R drone 0 deg 320 m PLY dataset, dataset_ply_2026-03-11 11-00-29-311, '
             '1832 frames at 33 Hz; fitted by scripts/radar_x100/calibrate_from_ply.py')
FRAME_RATE_HZ = 33.
BASIS = ('Measured vendor PLY fit: jitter, extended-object point count, sporadic-point rate, far-target '
         'detection rate versus range, vendor snr/mag. Surface RCS density, ms rule and clutter are not fitted.')
PARAMETERS = dict(
    fluctuation_model='SWERLING_1',
    range_std_m=.0129, azimuth_std_deg=.0694, elevation_std_deg=.0502,
    merge_range_m=.5, merge_azimuth_deg=2.5, merge_elevation_deg=5., merge_velocity_m_s=.25,
    false_alarm_probability_per_cell=3.0e-8,
    processing_gain_db=43.5,
    ms_velocity_threshold_m_s=.25,
    vendor_snr_offset_db=-11.55, vendor_mag_slope=1.1696, vendor_mag_intercept=61.868, vendor_mag_std=2.795,
)
MEASURED = dict(points_per_frame_mean=64.0, persistent_scatterers=33, extended_object_points_median=16,
                sporadic_points_per_frame=5.74, vendor_snr_range=(1.41, 43.8),
                far_target_detection_rate=((172., .539), (225., .524), (277., .223), (313., .085)))


def calibrated_config(*, azimuth_limit_deg=None, elevation_limit_deg=None, **overrides):
    """DetectionConfig with the fitted parameters; field-of-view limits and other fields may be overridden."""
    values = dict(PARAMETERS, calibration_status='USER_CALIBRATED', calibration_reference=REFERENCE,
                  parameter_basis=BASIS)
    if azimuth_limit_deg is not None:
        values['azimuth_limit_deg'] = float(azimuth_limit_deg)
    if elevation_limit_deg is not None:
        values['elevation_limit_deg'] = float(elevation_limit_deg)
    return replace(DetectionConfig(), **(values | overrides))
