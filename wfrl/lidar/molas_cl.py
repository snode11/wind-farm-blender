"""Molas CL V3.0 manual contract (PDF pages 7-10 and 32-36).

This is a structured DP decoder, not an undocumented bus-byte decoder or a
reimplementation of the manufacturer's proprietary optical/inversion filters.
DP averages cannot establish simultaneous, same-blade S2/S3 intersections.
"""
import math

SPEC = {
    'range_accuracy_m': .2,
    'range_repeatability_m': .2,
    'range_resolution_upper_m': .1,
    'beam_relative_angles_deg': [0., 2.05, 4.09],
    'relative_angle_tolerance_deg': .2,
    'raw_rate_per_channel_hz': 20000,
    'dp_rate_hz': 50,
    'dp_period_s': .02,
    'range_unit_m': .01,
    'invalid_range_code': 65535,
    'clearance_outside_detection_code': 10000,
    # The manual does NOT specify a final flexible-tip accuracy or end-to-end
    # protection latency. Neither 0.2 m nor 20 ms can fill those fields.
    'final_clearance_accuracy_m': None,
    'end_to_end_alarm_latency_s': None,
}


def manual_clearance(range_m, angle_deg, axial_offset_m, tip_tower_radius_m):
    """Section 3.5.3 straight-blade estimate, explicitly not flex reconstruction.

    axial_offset_m avoids the manual's X/Y naming difference between its
    installation figure and application formula. Coordinates must be mapped
    by physical direction, never by blindly swapping X and Y labels.
    """
    values = (range_m, angle_deg, axial_offset_m, tip_tower_radius_m)
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise ValueError('Nonfinite manual estimate input')
    if range_m < 0 or tip_tower_radius_m <= 0:
        raise ValueError('Invalid range or tower radius')
    return range_m * math.sin(math.radians(angle_deg)) + axial_offset_m - tip_tower_radius_m


def _integer(packet, key, maximum):
    value = packet[key]
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError('Invalid DP field: ' + key)
    return value


class DPDecoder:
    """Decode already parsed fields, retaining validity, heartbeat and unknowns.

    timestamp_s is the receiver's monotonic timestamp; the manual does not
    provide individual optical hit timestamps or blade IDs in DP packets.
    A duplicate Data_Index never creates a new S1 event, even if BUS_Index moves.
    The initial receiver packet establishes a baseline; freshness before that
    packet is unknown. Reconnect after >255 DP periods requires a fresh baseline.
    """
    def __init__(self):
        self.last_data_index = None
        self.last_bus_index = None
        self.last_timestamp_s = None

    def decode(self, packet, timestamp_s):
        if type(timestamp_s) not in (int, float) or not math.isfinite(timestamp_s):
            raise ValueError('Invalid receiver timestamp')
        if self.last_timestamp_s is not None and timestamp_s <= self.last_timestamp_s:
            raise ValueError('Receiver timestamps must increase')
        data = _integer(packet, 'Data_Index', 255)
        bus = _integer(packet, 'BUS_Index', 255)
        flags = _integer(packet, 'Data_Valid', 255)
        status = _integer(packet, 'System_Status', 255)
        fault = _integer(packet, 'Lidar_fault', 65535)
        ranges = [_integer(packet, 'Laser_Distance_'+str(i), 65535) for i in (1, 2, 3)]
        intensities = [_integer(packet, 'Laser_Intensity_'+str(i), 255) for i in (1, 2, 3)]
        clearance = _integer(packet, 'Laser_Distance_4', 65535)
        initial = self.last_data_index is None
        elapsed = None if initial else timestamp_s - self.last_timestamp_s
        delta = None if initial else (data - self.last_data_index) % 256
        # A gap longer than a full counter cycle is ambiguous, not proof of a
        # fresh sample. Fault handling is conservative; warning bits stay visible.
        fresh = initial or (delta != 0 and elapsed < 256 * SPEC['dp_period_s'])
        visibility_valid = not bool(flags & 8)
        critical_fault_mask = sum(1 << bit for bit in (15, 14, 13, 12, 11, 1, 0))
        low_intensity = any(value < 10 for value in intensities)
        healthy = status == 1 and not (fault & critical_fault_mask) and not low_intensity
        observations = {}
        for i, (raw, intensity) in enumerate(zip(ranges, intensities), 1):
            effective = fresh and healthy and visibility_valid and raw != 65535
            blade_valid = effective and bool(flags & (1 << (i-1)))
            reason = ('stale_or_ambiguous_counter' if not fresh else
                      'inconsistent_low_intensity' if low_intensity else
                      'system_unavailable' if not healthy else
                      'visibility_invalid' if not visibility_valid else
                      'invalid_sentinel' if raw == 65535 else
                      'valid_blade_dp' if blade_valid else 'not_valid_blade_dp')
            observations['S'+str(i)] = dict(raw_range_code=raw,
                slant_range_m=raw*.01 if effective else None,
                valid=blade_valid, observed=effective, intensity=intensity,
                blade_id=None, observation_time_s=None,
                time_support='20ms_processed_interval_unknown_hit_times', reason=reason)
        first = observations['S1']
        s1 = 'triggered' if first['valid'] else 'not_triggered' if first['observed'] else 'unknown'
        result = dict(receiver_time_s=timestamp_s, data_index=data, bus_index=bus,
            fresh=fresh, first_packet=initial, data_index_delta=delta,
            bus_progressed=None if initial else bus != self.last_bus_index,
            observations=observations, s1_state=s1,
            s1_available_at_receiver_s=timestamp_s if s1 == 'triggered' else None,
            pairing_allowed=False,
            pairing_reason='DP averages lack per-hit simultaneity and same-blade identity',
            device_clearance_m=clearance*.01 if fresh and healthy and visibility_valid
                and clearance not in (65535, 10000) else None,
            device_clearance_raw_code=clearance,
            device_clearance_is_independent_truth=False,
            system_status=status, visibility_valid=visibility_valid, fault_word=fault,
            fault_policy='local conservative rejection of manual fault-grade bits; not a claim about undocumented firmware fault handling',
            alarm_end_to_end_latency_s=None)
        self.last_data_index, self.last_bus_index, self.last_timestamp_s = data, bus, timestamp_s
        return result
