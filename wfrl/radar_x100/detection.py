"""Uncalibrated engineering detections from incoherent expected return power.

Surface area is an integration measure, not RCS. A declared effective RCS
density and incidence law convert reference-area weights to synthetic RCS.
Power adds before noise and detection. No phase interference, polarization,
near-field EM solution, multipath, waveform processing or vendor classifier is
represented. Fixed RCS point targets share the monostatic far-field link budget.
"""
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from itertools import product
import copy
import math
import numpy as np
from .schema import (MAX_POINTS, PROFILE, POINT_DTYPE, RadarFrame, ModelDiagnostics,
                     MODEL_DIAGNOSTIC_DTYPE)

MODEL_ID = 'x100-incoherent-detection.v2'
# Search trials are counted analytically; no dense range/angle/Doppler cube is allocated.
MAX_RESOLUTION_CELLS = 1_000_000_000_000
# Neighbor-list budget for one frame's clustering; denser frames use the per-seed lookup.
MAX_NEIGHBOR_PAIRS = 1_000_000
BOLTZMANN_J_K = 1.380649e-23
LIGHT_SPEED_M_S = 299_792_458.
DATASHEET = 'CTSAI X100 data sheet CN V1.0 (2026), pp. 3-5'
DEFAULT_SENSITIVITY_ANCHOR = dict(
    source='ASSUMPTION; not a manufacturer RCS/detection-probability specification',
    basis=DATASHEET + '; 396 m is a range limit, with no specified reference RCS, Pd or Pfa. '
          'The Mavic 2 Pro example at 320 m also omits RCS, Pd and Pfa.',
    scope='Default parameters only; Pd is before measurement/profile and output-cap filters. '
          'Parameter overrides do not refit processing gain.',
    range_m=396., rcs_m2=10., azimuth_deg=0., elevation_deg=0.,
    fluctuation_model='SWERLING_0', probability_detection=.9,
    false_alarm_probability_per_cell=1e-12, required_snr_linear=37.436754719339696,
    processing_gain_db=43.1220604659924)
PARAMETER_SOURCES = dict(
    carrier_frequency_hz='77 GHz chosen inside the datasheet 76-79 GHz band; wavelength = c/f.',
    antenna='Assumed full-sphere separable Gaussian power pattern, 45/15 deg half-power offsets; '
            'peak gain = efficiency * 4*pi / integral(pattern dOmega), efficiency 0.7 per antenna.',
    transmit_power_w='Assumed 0.1 W; default peak EIRP is below the datasheet 39 dBm ceiling.',
    thermal_noise='k*T0*B*10^(NF_dB/10); assumed T0=290 K, receiver equivalent noise bandwidth '
                  'B=1 MHz, NF=6 dB. B is not the FMCW sweep bandwidth or ADC data rate.',
    processing_gain_db='Equivalent coherent processing gain fitted only to the declared default '
                       'assumed anchor. 20521.36 linear; equivalent to 128*256 independent samples '
                       'with efficiency 0.626262, not known hardware chirp/ADC settings.',
    resolution=DATASHEET + '; range 0.2 m, boresight azimuth/elevation 1/2 deg, velocity 0.1 m/s.',
    angular_resolution='Assumed constant direction-cosine resolution: local angular width '
                       'delta_theta = boresight_width / cos(theta), independently on each axis.',
    measurement_error=DATASHEET + '; +/-0.05 m, +/-0.1/0.2 deg, +/-0.05 m/s. '
                      'Assume each quoted +/- accuracy covers +/-2 Gaussian standard deviations; '
                      'the datasheet does not specify a confidence level or error distribution.',
    velocity_search=DATASHEET + '; -400 to +200 km/h converted to m/s; independent Doppler trials.',
    surface_response='Assumed effective RCS density 0.1 m^2/m^2 and abs(normal dot LOS)^1; '
                     'not a conducting-plate EM model or measured blade material response.',
    other='Pfa=1e-12, Swerling 0, loss=1, excess attenuation=0 and extra dropout=0 are assumptions. '
          'Caller overrides are research inputs, not automatically manufacturer specifications.')
ASSUMPTIONS = (
    'Independent effective scatterers; reference-area-weighted incoherent expected power, no phase sum.',
    'Surface RCS = reference area * effective RCS density * abs(normal dot LOS)^incidence exponent.',
    'Monostatic far-field free-space radar equation with two-way excess attenuation; near-field validity unverified.',
    'Separable one-way power pattern over the full sphere, not clipped to FOV; identical tx/rx shapes, passive efficiencies <= 1.',
    'Deterministic complete-link box clusters in range, sin(azimuth), sin(elevation) and radial velocity; each normalized span <= 1.',
    'Clusters use lexicographically ordered seeds and local minima; no transitive chaining. This is a resolution approximation, not waveform processing.',
    'Reported location and radial speed are power-weighted continuous centroids, not cell centers or quantized coordinates.',
    'Swerling 0: deterministic aggregate signal magnitude plus complex Gaussian noise; Pd is Marcum Q1. Swerling 1: exponential total power.',
    'Signal-normalized coherent processing preserves received signal power and reduces k*T0*B*NF noise by the separate processing gain.',
    'Threshold/noise ratio = -log(Pfa per search cell); independent extra target dropout follows detection.',
    'Independent noise trials cover range, direction cosines and Doppler; ceil widths include partial edge cells as full trials.',
    'Noise trial bins only count/place false alarms; target resolution uses continuous distances. Bins containing positive scatterers are excluded from noise-only trials.',
    'False-alarm flags denote noise-only cells; target-associated detections can be noise dominated.',
    'False alarms are uniform within their search bins in range, direction cosines and radial velocity.',
    'Strongest total-power detections are capped before independent Gaussian measurement errors; out-of-profile estimates are discarded without replacement.',
    'Independent Gaussian measurement errors assume datasheet +/- accuracy means +/-2 sigma; confidence level is not specified by the manufacturer.',
    'model_power_w is receiver input signal power; model_observed_power_w is signal-normalized detector output; model_snr_linear uses processed noise.',
    'ms uses abs(synthetic radial velocity) > ms_velocity_threshold_m_s; the vendor label mapping is unknown; mag, rcs and snr stay NaN unless a vendor field mapping is configured.',
    'Instantaneous independent frames; no hardware chirp timing, tracking, safety or device calibration claim.',
)


@dataclass(frozen=True)
class DetectionConfig:
    """Datasheet resolution/accuracy with explicitly uncalibrated sensitivity.

    Gains, wavelength and noise are derived properties, not independent inputs.
    Processing gain is fixed when other parameters change; the assumed anchor
    describes only the defaults. Zero error standard deviations disable jitter.
    """
    surface_rcs_density: float = 0.1  # Effective m^2 RCS per m^2 reference area.
    incidence_exponent: float = 1.0
    transmit_power_w: float = 0.1
    carrier_frequency_hz: float = 77e9
    tx_efficiency: float = 0.7
    rx_efficiency: float = 0.7
    system_loss_linear: float = 1.0
    attenuation_db_per_m: float = 0.0  # One-way excess propagation loss.
    antenna_pattern: str = 'GAUSSIAN'
    azimuth_half_power_deg: float = 45.0  # One-way off-axis half-power angle.
    elevation_half_power_deg: float = 15.0
    merge_range_m: float = 0.2
    merge_azimuth_deg: float = 1.0
    merge_elevation_deg: float = 2.0
    merge_velocity_m_s: float = 0.1
    velocity_min_m_s: float = -400. / 3.6
    velocity_max_m_s: float = 200. / 3.6
    noise_temperature_k: float = 290.
    noise_bandwidth_hz: float = 1e6
    noise_figure_db: float = 6.
    processing_gain_db: float = 43.1220604659924
    false_alarm_probability_per_cell: float = 1e-12
    fluctuation_model: str = 'SWERLING_0'
    miss_probability: float = 0.0  # Additional independent target-cell dropout.
    false_alarms_enabled: bool = True
    range_std_m: float = 0.05 / 2
    azimuth_std_deg: float = 0.1 / 2
    elevation_std_deg: float = 0.2 / 2
    radial_velocity_std_m_s: float = 0.05 / 2
    calibration_status: str = 'UNCALIBRATED'
    calibration_reference: str | None = None
    parameter_basis: str = 'Datasheet resolution/accuracy; assumed sensitivity anchor and RF/processing parameters; no measured calibration.'
    # Rectangular azimuth/elevation half angles of the hardware gate and noise search domain.
    azimuth_limit_deg: float = PROFILE['azimuth_deg']
    elevation_limit_deg: float = PROFILE['elevation_deg']
    # ANGULAR keeps |azimuth|/|elevation| within the half angles; PROJECTIVE is a pinhole
    # camera rectangle with the same half angles (simulation.FOV_SHAPES).
    field_of_view_shape: str = 'ANGULAR'
    # ms flag: abs(radial velocity) above this speed.
    ms_velocity_threshold_m_s: float = 1e-6
    # Vendor field mapping; None keeps mag/rcs/snr unknown (NaN). With an offset, snr is the
    # detector output power over processed noise in dB plus the offset, rcs is the RCS implied
    # by that power at the measured position in dBsm, and mag = slope * snr + intercept + noise.
    vendor_snr_offset_db: float | None = None
    vendor_mag_slope: float = 1.0
    vendor_mag_intercept: float = 0.0
    vendor_mag_std: float = 0.0

    @property
    def wavelength_m(self):
        return LIGHT_SPEED_M_S / self.carrier_frequency_hz

    @property
    def antenna_solid_angle_sr(self):
        return _pattern_solid_angle(self.antenna_pattern, self.azimuth_half_power_deg,
                                    self.elevation_half_power_deg)

    @property
    def directivity_linear(self):
        return 4 * math.pi / self.antenna_solid_angle_sr

    @property
    def tx_gain_linear(self):
        return self.tx_efficiency * self.directivity_linear

    @property
    def rx_gain_linear(self):
        return self.rx_efficiency * self.directivity_linear

    @property
    def noise_power_w(self):
        return BOLTZMANN_J_K * self.noise_temperature_k * self.noise_bandwidth_hz * 10 ** (self.noise_figure_db / 10)

    @property
    def processing_gain_linear(self):
        return 10 ** (self.processing_gain_db / 10)

    @property
    def detector_noise_power_w(self):
        return self.noise_power_w / self.processing_gain_linear

    @property
    def threshold_noise_ratio(self):
        return -math.log(self.false_alarm_probability_per_cell)

    def __post_init__(self):
        nonnegative = ('surface_rcs_density', 'incidence_exponent', 'attenuation_db_per_m',
                       'noise_figure_db', 'processing_gain_db', 'miss_probability', 'range_std_m',
                       'azimuth_std_deg', 'elevation_std_deg', 'radial_velocity_std_m_s')
        positive = ('transmit_power_w', 'carrier_frequency_hz', 'tx_efficiency', 'rx_efficiency',
                    'system_loss_linear', 'azimuth_half_power_deg', 'elevation_half_power_deg',
                    'merge_range_m', 'merge_azimuth_deg', 'merge_elevation_deg', 'merge_velocity_m_s',
                    'noise_temperature_k', 'noise_bandwidth_hz', 'false_alarm_probability_per_cell')
        nonnegative += ('ms_velocity_threshold_m_s', 'vendor_mag_std')
        positive += ('azimuth_limit_deg', 'elevation_limit_deg')
        finite = ('velocity_min_m_s', 'velocity_max_m_s', 'vendor_mag_slope', 'vendor_mag_intercept')
        if self.vendor_snr_offset_db is not None:
            finite += ('vendor_snr_offset_db',)
        for name in nonnegative + positive + finite:
            value = getattr(self, name)
            if (isinstance(value, (bool, str)) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or (name in nonnegative and value < 0)
                    or (name in positive and value <= 0)):
                raise ValueError(f'Invalid detection parameter: {name}')
        if (self.system_loss_linear < 1 or self.miss_probability > 1
                or max(self.tx_efficiency, self.rx_efficiency, self.false_alarm_probability_per_cell) > 1
                or not 76e9 <= self.carrier_frequency_hz <= 79e9
                or self.noise_figure_db > 100 or self.processing_gain_db > 100
                or self.velocity_min_m_s >= self.velocity_max_m_s
                or self.azimuth_limit_deg > 90 or self.elevation_limit_deg > 90
                or self.field_of_view_shape not in ('ANGULAR', 'PROJECTIVE')
                or (self.field_of_view_shape == 'PROJECTIVE'
                    and max(self.azimuth_limit_deg, self.elevation_limit_deg) > 89.9)
                or not .01 <= self.azimuth_half_power_deg <= 180
                or not .01 <= self.elevation_half_power_deg <= 90
                or self.fluctuation_model not in {'SWERLING_0', 'SWERLING_1'}
                or self.antenna_pattern not in {'GAUSSIAN', 'ISOTROPIC'}
                or type(self.false_alarms_enabled) is not bool
                or self.calibration_status not in {'UNCALIBRATED', 'USER_CALIBRATED'}
                or not isinstance(self.parameter_basis, str) or not 1 <= len(self.parameter_basis) <= 2048
                or (self.calibration_reference is not None
                    and (not isinstance(self.calibration_reference, str)
                         or not 1 <= len(self.calibration_reference) <= 2048))
                or (self.calibration_status == 'USER_CALIBRATED' and not self.calibration_reference)):
            raise ValueError('Invalid detection model or calibration declaration')
        _cell_grid(self)
        if not math.isfinite(self.noise_power_w) or self.detector_noise_power_w <= 0:
            raise ValueError('Invalid derived thermal noise')


@lru_cache(maxsize=128)
def _pattern_solid_angle(pattern, azimuth_half_power_deg, elevation_half_power_deg):
    """Full-sphere integral with dOmega = cos(elevation) dElevation dAzimuth."""
    if pattern == 'ISOTROPIC':
        return 4 * math.pi
    az, el = map(math.radians, (azimuth_half_power_deg, elevation_half_power_deg))
    log2 = math.log(2)
    az_integral = az * math.sqrt(math.pi / log2) * math.erf(math.pi * math.sqrt(log2) / az)
    # Scale quadrature by beam width to retain narrow-beam accuracy; omitted tails < 1e-44.
    nodes, weights = np.polynomial.legendre.leggauss(128)
    extent = min(math.pi / (2 * el), 12.)
    scaled = nodes * extent
    el_integral = el * extent * np.dot(weights, np.exp(-log2 * scaled**2) * np.cos(el * scaled))
    return float(az_integral * el_integral)


def _cell_grid(config):
    """Noise search bins in range, direction cosines and Doppler, not target clusters."""
    angles = np.sin(np.radians([config.azimuth_limit_deg, config.elevation_limit_deg]))
    lower = np.array([PROFILE['range_m'][0], -angles[0], -angles[1], config.velocity_min_m_s])
    upper = np.array([PROFILE['range_m'][1], angles[0], angles[1], config.velocity_max_m_s])
    widths = np.array([config.merge_range_m, math.radians(config.merge_azimuth_deg),
                       math.radians(config.merge_elevation_deg), config.merge_velocity_m_s])
    dimensions = np.maximum(1., np.ceil((upper - lower) / widths))
    if not np.isfinite(dimensions).all() or np.prod(dimensions) > MAX_RESOLUTION_CELLS:
        raise ValueError('Resolution-cell search exceeds numerical resource budget')
    return lower, upper, widths, dimensions.astype(np.int64)


def _resolution_coordinates(polar, velocity):
    return np.column_stack((polar[:, 0], np.sin(np.radians(polar[:, 1:])), velocity))


def _resolution_clusters(coordinates, widths):
    """Complete-link L-infinity clusters, without fixed bin-boundary separability.

    Lexicographic seeds select nearby unassigned points. Their local minima
    bound a box of width one, so every pair in a cluster is unresolved in all
    four dimensions. Spatial hashing only accelerates neighbor lookup. Dense
    chains cannot grow a cluster past one resolution width on any axis.

    Each point's candidates are those in adjacent hash bins within one width on
    every axis; they are listed once per frame and then filtered by the current
    assignment, so labels equal the per-seed bucket lookup in
    ``_resolution_clusters_hashed``, which remains the path for non-finite input
    or neighbor lists beyond ``MAX_NEIGHBOR_PAIRS``.
    """
    normalized = coordinates / widths
    if not len(normalized):
        return np.full(0, -1, dtype=np.int64), 0
    if not np.isfinite(normalized).all():
        return _resolution_clusters_hashed(coordinates, widths)
    bins = np.floor(normalized).astype(np.int64)
    lists = _neighbor_lists(normalized, bins)
    if lists is None:
        return _resolution_clusters_hashed(coordinates, widths)
    indptr, neighbors = lists
    starts, ends = indptr[:-1].tolist(), indptr[1:].tolist()
    labels = np.full(len(coordinates), -1, dtype=np.int64)
    count = 0
    for index in np.lexsort(normalized[:, ::-1].T).tolist():
        if labels[index] >= 0:
            continue
        start, end = starts[index], ends[index]
        if end - start > 1:
            candidates = neighbors[start:end]
            candidates = candidates[labels[candidates] < 0]
            if len(candidates) > 1:
                near = normalized[candidates]
                labels[candidates[(near - near.min(axis=0) <= 1).all(axis=1)]] = count
                count += 1
                continue
        # Only the seed itself remains unassigned within one width.
        labels[index] = count
        count += 1
    return labels, count


def _neighbor_lists(normalized, bins):
    """CSR lists of points in adjacent bins and within one width, or None beyond budget.

    Bins are encoded with one padding cell per axis, so adding an offset never
    wraps into another bin. The velocity axis is least significant: for each
    leading-axis offset its three adjacent bins form one key range. Half of the
    leading offsets are searched and mirrored, since adjacency is symmetric.
    """
    count = len(bins)
    low = bins.min(axis=0) - 1
    spans = [int(span) for span in (bins.max(axis=0) - low + 2).tolist()]
    if math.prod(spans) >= 2 ** 62:
        return None
    strides = (spans[1] * spans[2] * spans[3], spans[2] * spans[3], spans[3])
    keys = (bins - low) @ np.array(strides + (1,), dtype=np.int64)
    by_key = np.argsort(keys, kind='stable')
    unique, first_point, sizes = np.unique(keys[by_key], return_index=True, return_counts=True)
    bounds = np.append(first_point, count)
    point_bin = np.repeat(np.arange(len(unique)), sizes)
    sources, targets, pairs = [], [], 0
    for offset in _LEADING_OFFSETS:
        code = offset[0] * strides[0] + offset[1] * strides[1] + offset[2] * strides[2]
        first = bounds[np.searchsorted(unique, unique + (code - 1), 'left')]
        last = bounds[np.searchsorted(unique, unique + (code + 1), 'right')]
        lengths = (last - first)[point_bin]
        total = int(lengths.sum())
        pairs += total if offset == (0, 0, 0) else 2 * total
        if pairs > MAX_NEIGHBOR_PAIRS:
            return None
        if not total:
            continue
        source = np.repeat(by_key, lengths)
        target = by_key[np.repeat(first[point_bin] - np.cumsum(lengths) + lengths, lengths) + np.arange(total)]
        sources += [source] if offset == (0, 0, 0) else [source, target]
        targets += [target] if offset == (0, 0, 0) else [target, source]
    sources, targets = np.concatenate(sources), np.concatenate(targets)
    # The bin test keeps rounding at a width boundary identical to the bucket lookup.
    keep = ((np.abs(bins[targets] - bins[sources]) <= 1).all(axis=1)
            & (np.abs(normalized[targets] - normalized[sources]) <= 1).all(axis=1))
    sources, targets = sources[keep], targets[keep]
    indptr = np.zeros(count + 1, dtype=np.int64)
    np.cumsum(np.bincount(sources, minlength=count), out=indptr[1:])
    return indptr, targets[np.argsort(sources, kind='stable')]


_LEADING_OFFSETS = ((0, 0, 0),) + tuple(offset for offset in product((-1, 0, 1), repeat=3) if offset > (0, 0, 0))


def _resolution_clusters_hashed(coordinates, widths):
    """Per-seed lookup of the 81 adjacent hash buckets; same labels as ``_resolution_clusters``."""
    normalized = coordinates / widths
    bins = np.floor(normalized).astype(np.int64)
    buckets = {}
    for index, key in enumerate(map(tuple, bins.tolist())):
        buckets.setdefault(key, []).append(index)
    buckets = {key: np.asarray(indices) for key, indices in buckets.items()}
    labels = np.full(len(coordinates), -1, dtype=np.int64)
    offsets = tuple(product((-1, 0, 1), repeat=4))
    order = np.lexsort(normalized[:, ::-1].T)
    count = 0
    for index in order:
        if labels[index] >= 0:
            continue
        x, y, z, v = bins[index].tolist()
        # Scalar hash keys preserve offset order without allocating an array per neighbor.
        neighbors = [bucket for dx, dy, dz, dv in offsets
                     if (bucket := buckets.get((x + dx, y + dy, z + dz, v + dv))) is not None]
        candidates = np.concatenate(neighbors)
        candidates = candidates[(labels[candidates] < 0)
            & (np.abs(normalized[candidates] - normalized[index]) <= 1).all(axis=1)]
        minimum = normalized[candidates].min(axis=0)
        members = candidates[(normalized[candidates] - minimum <= 1).all(axis=1)]
        labels[members] = count
        count += 1
    return labels, count


@lru_cache(maxsize=128)
def _marcum_terms(threshold):
    # Above this amplitude separation the miss probability is bounded by exp(-100).
    limit = (math.sqrt(threshold) + 10) ** 2
    count = int(math.ceil(limit + 12 * math.sqrt(limit + 1) + 32))
    k = np.arange(count)
    log_factorial = np.array([math.lgamma(int(i) + 1) for i in k])
    poisson_cdf = np.minimum(1., np.cumsum(np.exp(-threshold + k * math.log(threshold) - log_factorial)))
    return limit, k, log_factorial, poisson_cdf


def probability_detection(snr_linear, *, config=None):
    """Single coherent-look Pd, including extra dropout but before output filters.

    Swerling 0 is Q1(sqrt(2*SNR), sqrt(2*threshold)), evaluated as a Poisson
    mixture of integer-shape gamma survival functions without a SciPy runtime
    dependency. Swerling 1 has exponential power with mean 1+SNR.
    """
    config = DetectionConfig() if config is None else config
    snr = np.asarray(snr_linear, dtype=float)
    if not np.isfinite(snr).all() or np.any(snr < 0):
        raise ValueError('Invalid detection SNR')
    threshold = config.threshold_noise_ratio
    if config.fluctuation_model == 'SWERLING_1':
        pd = np.exp(-threshold / (1 + snr))
    elif threshold == 0:
        pd = np.ones_like(snr)
    else:
        limit, k, log_factorial, poisson_cdf = _marcum_terms(threshold)
        pd = np.ones(snr.size)
        for i, value in enumerate(snr.flat):
            if value == 0:
                pd[i] = config.false_alarm_probability_per_cell
            elif value <= limit:
                pd[i] = np.dot(np.exp(-value + k * math.log(value) - log_factorial), poisson_cdf)
        pd = np.clip(pd.reshape(snr.shape), 0., 1.)
    return pd * (1 - config.miss_probability)


def model_derived_parameters(config):
    """Serializable link budget, noise-trial count and default-anchor comparison."""
    *_, dimensions = _cell_grid(config)
    cells = int(np.prod(dimensions))
    anchor = DEFAULT_SENSITIVITY_ANCHOR
    anchor_snr = float(received_power_w(anchor['range_m'], anchor['rcs_m2'], config=config)
                       / config.detector_noise_power_w)
    return dict(wavelength_m=config.wavelength_m, antenna_solid_angle_sr=config.antenna_solid_angle_sr,
        directivity_linear=config.directivity_linear, tx_gain_linear=config.tx_gain_linear,
        rx_gain_linear=config.rx_gain_linear, peak_eirp_w=config.transmit_power_w * config.tx_gain_linear,
        receiver_noise_power_w=config.noise_power_w, processing_gain_linear=config.processing_gain_linear,
        detector_noise_power_w=config.detector_noise_power_w, threshold_noise_ratio=config.threshold_noise_ratio,
        threshold_power_w=config.threshold_noise_ratio * config.detector_noise_power_w,
        threshold_db=(10 * math.log10(config.threshold_noise_ratio) if config.threshold_noise_ratio else None),
        noise_only_search_cells=cells,
        expected_noise_only_false_alarms_per_frame=cells * config.false_alarm_probability_per_cell * config.false_alarms_enabled,
        current_anchor_snr_linear=anchor_snr,
        current_anchor_probability_detection=float(probability_detection(anchor_snr, config=config)))


def surface_rcs(area_weights_m2, incidence_cosine, *, config=None):
    """Effective m^2 RCS under the explicit reference-area density model."""
    config = DetectionConfig() if config is None else config
    area, incidence = np.asarray(area_weights_m2, dtype=float), np.asarray(incidence_cosine, dtype=float)
    if (area.ndim != 1 or area.shape != incidence.shape or not np.isfinite(area).all()
            or not np.isfinite(incidence).all() or np.any(area < 0)
            or np.any(incidence < 0) or np.any(incidence > 1 + 1e-12)):
        raise ValueError('Invalid reference area or incidence')
    with np.errstate(over='ignore', invalid='ignore'):
        rcs = area * config.surface_rcs_density * np.minimum(incidence, 1) ** config.incidence_exponent
    if not np.isfinite(rcs).all():
        raise ValueError('Nonfinite surface RCS')
    return rcs


def received_power_w(range_m, rcs_m2, *, config=None, azimuth_deg=0., elevation_deg=0.):
    """Pt Gt Gr lambda^2 sigma / ((4 pi)^3 R^4 L), in watts.

    Angles apply the declared one-way power pattern twice. Excess attenuation
    is specified in dB/m one way and applied over the round-trip distance.
    This link budget assumes a monostatic far-field point scatterer.
    """
    config = DetectionConfig() if config is None else config
    r, rcs, az, el = np.broadcast_arrays(*[np.asarray(x, dtype=float)
        for x in (range_m, rcs_m2, azimuth_deg, elevation_deg)])
    if (not all(np.isfinite(x).all() for x in (r, rcs, az, el))
            or np.any(r <= 0) or np.any(rcs < 0)):
        raise ValueError('Invalid link-budget inputs')
    pattern = np.ones_like(r)
    if config.antenna_pattern == 'GAUSSIAN':
        pattern = np.exp(-math.log(2) * ((az / config.azimuth_half_power_deg) ** 2
                                        + (el / config.elevation_half_power_deg) ** 2))
    with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
        power = np.divide(
            config.transmit_power_w * config.tx_gain_linear * config.rx_gain_linear
            * config.wavelength_m ** 2 * rcs * pattern ** 2
            * 10 ** (-2 * config.attenuation_db_per_m * r / 10),
            (4 * math.pi) ** 3 * r ** 4 * config.system_loss_linear,
            out=np.zeros_like(r), where=rcs > 0)
    if not np.isfinite(power).all():
        raise ValueError('Nonfinite received power')
    return power


def _polar(xyz):
    return np.column_stack((np.linalg.norm(xyz, axis=1),
        np.degrees(np.arctan2(xyz[:, 1], xyz[:, 0])),
        np.degrees(np.arctan2(xyz[:, 2], np.hypot(xyz[:, 0], xyz[:, 1])))))


def _xyz(polar):
    r, az, el = polar[:, 0], np.radians(polar[:, 1]), np.radians(polar[:, 2])
    return np.column_stack((r * np.cos(el) * np.cos(az), r * np.cos(el) * np.sin(az), r * np.sin(el)))


def _top_exponential(rng, count, keep):
    """Largest keep of count unit exponentials, without allocating count rows."""
    if type(count) is not int or type(keep) is not int or min(count, keep) < 0:
        raise ValueError('Exponential sample counts must be nonnegative integers')
    keep = min(keep, count)
    if not keep:
        return np.empty(0)
    uniform = np.maximum(rng.random(keep), np.finfo(float).tiny)
    log_cdf = np.cumsum(np.log(uniform) / (count - np.arange(keep)))
    return -np.log(-np.expm1(log_cdf))


def generate_detections(xyz_m, radial_velocity_m_s, effective_rcs_m2, *,
                        template: RadarFrame | None, config=None, seed=100,
                        output_point_limit=MAX_POINTS, response_kind='FIXED_RCS_POINT_TARGETS'):
    """Return a synthetic RadarFrame, or None for a missing input frame.

    Inputs are geometrically visible scatterers in sensor coordinates. The
    template supplies identity/time/spatial/provenance and must have no points.
    Hardware range/FOV are checked here as well. A non-complete template retains
    its completeness and generates no detections or false alarms. Resolution
    clusters merge contributors regardless of target identity; material_ids is
    absent. Diagnostics are model quantities, not vendor measurements.
    """
    config = DetectionConfig() if config is None else config
    if (type(seed) is not int or seed < 0 or type(output_point_limit) is not int
            or not 1 <= output_point_limit <= MAX_POINTS
            or response_kind not in {'FIXED_RCS_POINT_TARGETS', 'REFERENCE_AREA_SURFACE'}):
        raise ValueError('Invalid detection seed, report limit or response kind')
    if template is None:
        return None
    if (len(template.points) or template.source_kind not in {'GEOMETRY_SIMULATION', 'ENGINEERING_SIMULATION', 'TEST'}
            or template.spatial.get('frame') != 'SENSOR_X_FORWARD_Y_LEFT_Z_UP'):
        raise ValueError('Detection template must be an empty synthetic sensor-coordinate frame')
    provenance = dict(template.provenance)
    provenance.pop('material_point_indices', None)
    vendor_fields = config.vendor_snr_offset_db is not None
    provenance.update(observation_model=dict(id=MODEL_ID, response_kind=response_kind,
        assumptions=list(ASSUMPTIONS), parameters=asdict(config), noise_seed=seed,
        parameter_sources=dict(PARAMETER_SOURCES), derived_parameters=model_derived_parameters(config),
        sensitivity_anchor=dict(DEFAULT_SENSITIVITY_ANCHOR),
        random_generator='numpy.PCG64', seed_sequence=[seed, template.sequence],
        random_streams=['target_power', 'target_dropout', 'false_alarms', 'measurement_error'],
        output_point_limit=output_point_limit,
        truth_use='Evaluation only; no one-to-one material association or vendor label mapping.'),
        unknown_attributes=['vendor_label'] if vendor_fields else ['mag', 'rcs', 'snr', 'vendor_label'],
        label_semantics='NOT_GENERATED',
        ms_semantics=f'Synthetic abs(radial_velocity_m_s) > {config.ms_velocity_threshold_m_s:g} m/s; not a vendor classifier',
        field_of_view_deg=[config.azimuth_limit_deg, config.elevation_limit_deg],
        field_of_view_shape=config.field_of_view_shape)
    if vendor_fields:
        provenance['vendor_field_semantics'] = (
            'Mapped from model quantities with the calibration in observation_model.parameters: '
            'snr = 10*log10(detector output power / processed noise) + vendor_snr_offset_db; '
            'rcs = RCS implied by that power at the measured position, dBsm; '
            'mag = vendor_mag_slope * snr + vendor_mag_intercept + N(0, vendor_mag_std). Not vendor measurements.')
    empty = np.zeros(0, dtype=POINT_DTYPE)
    base = replace(template, source_kind='ENGINEERING_SIMULATION', stream='DETECTIONS',
        points=empty, radial_velocity_m_s=np.empty(0), material_ids=None,
        model_diagnostics=ModelDiagnostics(MODEL_ID, np.zeros(0, dtype=MODEL_DIAGNOSTIC_DTYPE)),
        provenance=provenance)
    if template.completeness != 'COMPLETE':
        base.provenance['detection_statistics'] = None
        return base
    xyz, velocity, rcs = (np.asarray(value, dtype=float)
                          for value in (xyz_m, radial_velocity_m_s, effective_rcs_m2))
    if (xyz.ndim != 2 or xyz.shape[1] != 3 or velocity.shape != (len(xyz),)
            or rcs.shape != (len(xyz),) or not all(np.isfinite(x).all() for x in (xyz, velocity, rcs))
            or np.any(rcs < 0)):
        raise ValueError('Invalid detection scatterers')
    from .simulation import in_fov
    geometric_inside = in_fov(xyz, (config.azimuth_limit_deg, config.elevation_limit_deg),
                              config.field_of_view_shape)
    velocity_inside = (velocity >= config.velocity_min_m_s) & (velocity <= config.velocity_max_m_s)
    inside = geometric_inside & velocity_inside
    polar, velocity, rcs = _polar(xyz[inside]), velocity[inside], rcs[inside]
    power = received_power_w(polar[:, 0], rcs, config=config,
                             azimuth_deg=polar[:, 1], elevation_deg=polar[:, 2])
    positive = power > 0
    polar, velocity, rcs, power = polar[positive], velocity[positive], rcs[positive], power[positive]
    lower, upper, widths, dimensions = _cell_grid(config)
    total_cells = int(np.prod(dimensions))
    continuous = _resolution_coordinates(polar, velocity)
    search_coordinates = np.clip(np.floor((continuous - lower) / widths).astype(np.int64), 0, dimensions - 1)
    occupied = np.unique(np.ravel_multi_index(search_coordinates.T, dimensions))
    occupied_search_cells = len(occupied)
    inverse, cells = _resolution_clusters(continuous, widths)
    total_power = np.bincount(inverse, weights=power, minlength=cells)
    total_rcs = np.bincount(inverse, weights=rcs, minlength=cells)
    contributors = np.bincount(inverse, minlength=cells)
    weights = power / total_power[inverse]
    centroid = np.column_stack([np.bincount(inverse, weights=weights * polar[:, i], minlength=cells)
                                for i in range(3)])
    speed = np.bincount(inverse, weights=weights * velocity, minlength=cells)
    target_rng, dropout_rng, false_rng, error_rng = [np.random.Generator(np.random.PCG64(s))
        for s in np.random.SeedSequence([seed, template.sequence]).spawn(4)]
    noise = config.detector_noise_power_w
    threshold_w = config.threshold_noise_ratio * noise
    if config.fluctuation_model == 'SWERLING_0':
        quadratures = target_rng.normal(size=(cells, 2)) * math.sqrt(noise / 2)
        measured = (np.sqrt(total_power) + quadratures[:, 0]) ** 2 + quadratures[:, 1] ** 2
    else:
        measured = target_rng.exponential(total_power + noise)
    above = measured >= threshold_w
    retained = above & (dropout_rng.random(cells) >= config.miss_probability)
    pd = probability_detection(total_power / noise, config=config)
    pfa = config.false_alarm_probability_per_cell if config.false_alarms_enabled else 0.
    empty_search_cells = total_cells - occupied_search_cells
    false_count = int(false_rng.binomial(empty_search_cells, pfa))
    false_keep = min(false_count, output_point_limit)
    # Only the strongest report-limit false alarms can survive the common cap.
    false_power = (config.threshold_noise_ratio + _top_exponential(false_rng, false_count, false_keep)) * noise
    ranks = false_rng.choice(empty_search_cells, false_keep, replace=False)
    false_flat = ranks + np.searchsorted(occupied - np.arange(occupied_search_cells), ranks, side='right')
    false_coordinates = np.column_stack(np.unravel_index(false_flat, dimensions))
    cell_lower = lower + false_coordinates * widths
    false_sample = cell_lower + false_rng.random((false_keep, 4)) * (np.minimum(cell_lower + widths, upper) - cell_lower)
    false_polar = np.column_stack((false_sample[:, 0], np.degrees(np.arcsin(false_sample[:, 1:3]))))
    target_count = int(retained.sum())
    positions = np.vstack((centroid[retained], false_polar))
    speeds = np.concatenate((speed[retained], false_sample[:, 3]))
    diagnostics = np.zeros(target_count + false_keep, dtype=MODEL_DIAGNOSTIC_DTYPE)
    diagnostics['model_power_w'][:target_count] = total_power[retained]
    diagnostics['model_rcs_m2'][:target_count] = total_rcs[retained]
    diagnostics['model_snr_linear'][:target_count] = total_power[retained] / noise
    diagnostics['integration_samples'][:target_count] = contributors[retained]
    diagnostics['false_alarm'][target_count:] = 1
    diagnostics['model_observed_power_w'] = np.concatenate((measured[retained], false_power))
    selected = np.argsort(-diagnostics['model_observed_power_w'], kind='stable')[:output_point_limit]
    positions, speeds, diagnostics = positions[selected], speeds[selected], diagnostics[selected]
    positions = positions + error_rng.normal(size=positions.shape) * np.array([
        config.range_std_m, config.azimuth_std_deg, config.elevation_std_deg])
    speeds = speeds + error_rng.normal(size=len(speeds)) * config.radial_velocity_std_m_s
    measured_xyz = _xyz(positions)
    angular_tolerance = np.degrees(1e-12)
    angle_limits = np.array([config.azimuth_limit_deg, config.elevation_limit_deg])
    if config.field_of_view_shape == 'PROJECTIVE':
        from .simulation import fov_mask
        # The search grid bounds the rectangle; estimates outside it are rejected here.
        direction_inside = fov_mask(measured_xyz, tuple(angle_limits), 'PROJECTIVE')
    else:
        direction_inside = (np.abs(positions[:, 1:]) <= angle_limits + angular_tolerance).all(axis=1)
    accepted = ((positions[:, 0] >= lower[0]) & (positions[:, 0] <= upper[0]) & direction_inside
                & (speeds >= config.velocity_min_m_s) & (speeds <= config.velocity_max_m_s))
    points = np.zeros(int(accepted.sum()), dtype=POINT_DTYPE)
    for axis, name in enumerate(('x', 'y', 'z')):
        points[name] = measured_xyz[accepted, axis]
    if vendor_fields:
        observed = diagnostics['model_observed_power_w'][accepted]
        kept = positions[accepted]
        unit = received_power_w(kept[:, 0], np.ones(len(kept)), config=config,
                                azimuth_deg=kept[:, 1], elevation_deg=kept[:, 2])
        with np.errstate(divide='ignore'):
            points['snr'] = 10 * np.log10(observed / noise) + config.vendor_snr_offset_db
            points['rcs'] = 10 * np.log10(np.divide(observed, unit, out=np.full(len(kept), np.nan), where=unit > 0))
        points['mag'] = (config.vendor_mag_slope * points['snr'] + config.vendor_mag_intercept
                         + error_rng.normal(size=len(kept)) * config.vendor_mag_std)
    else:
        for name in ('mag', 'rcs', 'snr'):
            points[name] = np.nan
    points['ms'] = (np.abs(speeds[accepted]) > config.ms_velocity_threshold_m_s).astype(np.uint8)
    probability_any = float(1 - np.prod(1 - pd))
    if probability_any == 0. and np.any(pd > 0):
        probability_any = float(-np.expm1(np.log1p(-pd).sum()))
    provenance['detection_statistics'] = dict(
        input_scatterers=len(xyz), outside_hardware_profile=int((~inside).sum()),
        outside_velocity_profile=int((~velocity_inside).sum()),
        positive_response_samples=int(positive.sum()), occupied_cells=cells,
        occupied_search_cells=occupied_search_cells,
        search_cells=total_cells, total_model_power_w=float(total_power.sum()),
        total_model_rcs_m2=float(total_rcs.sum()),
        expected_target_detections_before_output_filters=float(pd.sum()),
        probability_any_target_before_output_filters=probability_any,
        false_alarm_probability_per_cell=pfa,
        expected_false_alarms_before_output_filters=float(empty_search_cells * pfa),
        threshold_misses=int((~above).sum()), dropout_misses=int((above & ~retained).sum()),
        target_detections_before_output_filters=target_count,
        false_alarms_before_output_filters=false_count,
        output_truncated=max(0, target_count + false_count - output_point_limit),
        measurement_rejected=int((~accepted).sum()))
    return replace(base, points=points, radial_velocity_m_s=speeds[accepted], provenance=provenance,
                   model_diagnostics=ModelDiagnostics(MODEL_ID, diagnostics[accepted]))


def detection_statistics(frame: RadarFrame | None, *, point_mask=None):
    """Evaluation statistics; an optional output-point ROI never changes the frame.

    None/incomplete/corrupt frames are UNKNOWN with null counts, while a valid
    zero-point frame contributes zero. Model totals always describe the whole
    input frame; selected-point totals describe only the supplied boolean ROI.
    Counts of true material points or target identities cannot be inferred.
    """
    if frame is None or frame.completeness != 'COMPLETE':
        return dict(status='UNKNOWN', frame_validity='MISSING' if frame is None else frame.validity,
                    reported_points=None, target_associated_points=None, false_alarm_points=None,
                    selected_model_power_w=None, selected_model_rcs_m2=None, frame_model_statistics=None)
    if frame.model_diagnostics is None or frame.model_diagnostics.model_id != MODEL_ID:
        raise ValueError('Detection statistics require engineering model diagnostics')
    mask = np.ones(len(frame.points), dtype=bool) if point_mask is None else np.asarray(point_mask)
    if mask.dtype != np.dtype(bool) or mask.shape != (len(frame.points),):
        raise ValueError('Detection point mask must be a matching boolean array')
    values = frame.model_diagnostics.values[mask]
    with np.errstate(over='ignore'):
        power = float(values['model_power_w'].sum())
        rcs = float(values['model_rcs_m2'].sum())
    if not np.isfinite([power, rcs]).all():
        raise ValueError('Nonfinite detection statistic totals')
    return dict(status='KNOWN', frame_validity=frame.validity, reported_points=len(values),
        target_associated_points=int((values['false_alarm'] == 0).sum()),
        false_alarm_points=int(values['false_alarm'].sum()),
        selected_model_power_w=power, selected_model_rcs_m2=rcs,
        frame_model_statistics=copy.deepcopy(frame.provenance.get('detection_statistics')))


def summarize_detections(frames, *, point_masks=None, min_points=1):
    """Summarize an explicitly enumerated expected frame grid for installation evaluation.

    Supply None for every missing sample; sequence gaps are not invented here.
    ROI masks apply to output points and false alarms never count as target
    support. Unknown frames break known miss runs and make all-grid coverage
    unknown. Coverage conditional on known frames has its own denominator.
    """
    frames = list(frames)
    masks = [None] * len(frames) if point_masks is None else list(point_masks)
    if len(masks) != len(frames) or type(min_points) is not int or min_points < 1:
        raise ValueError('Invalid detection evaluation grid or support threshold')
    rows = [detection_statistics(frame, point_mask=mask) for frame, mask in zip(frames, masks)]
    known = [row for row in rows if row['status'] == 'KNOWN']
    supported = sum(row['target_associated_points'] >= min_points for row in known)
    longest_gap = run = 0
    for row in rows:
        run = run + 1 if row['status'] == 'KNOWN' and row['target_associated_points'] < min_points else 0
        longest_gap = max(longest_gap, run)
    return dict(status='KNOWN' if rows and len(known) == len(rows) else 'INSUFFICIENT_OR_UNKNOWN',
        expected_frames=len(rows), known_frames=len(known), unknown_frames=len(rows) - len(known),
        empty_frames=sum(row['reported_points'] == 0 for row in known), supported_frames=supported,
        support_fraction=supported / len(rows) if rows and len(known) == len(rows) else None,
        support_fraction_known=supported / len(known) if known else None,
        longest_known_miss_run_frames=longest_gap, min_points=min_points, frames=rows)
