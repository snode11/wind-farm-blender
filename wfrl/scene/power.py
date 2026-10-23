"""全场功率只在全部预期机组具有有限标量读数时有效。"""
from numbers import Real

import numpy as np


def farm_power_total(power, expected_count):
    """仅接受数值实数读数；缺测、形状错误或非有限总量返回 NaN。"""
    if np.ma.is_masked(power):
        return float('nan')
    if isinstance(power, np.ndarray) and power.dtype.kind in 'mM':
        return float('nan')
    try:
        values = np.asarray(power, dtype=object)
    except (TypeError, ValueError):
        return float('nan')
    if expected_count < 1 or values.shape != (expected_count,):
        return float('nan')
    if any(isinstance(value, (bool, np.bool_, np.timedelta64)) or not isinstance(value, Real)
           for value in values):
        return float('nan')
    try:
        with np.errstate(over='ignore', invalid='ignore'):
            values = values.astype(float)
            if not np.isfinite(values).all():
                return float('nan')
            total = float(values.sum())
    except (TypeError, ValueError, OverflowError):
        return float('nan')
    return total if np.isfinite(total) else float('nan')
