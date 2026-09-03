import numpy as np
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any
from .edge import bit_to_samples, extract_edges
from .pwm import analyze_pwm, PwmMeasurement


@dataclass
class ComplementaryMeasurement:
    high_channel: int
    low_channel: int
    pwm_high: PwmMeasurement
    pwm_low: PwmMeasurement
    deadtime_rising_mean_ns: float
    deadtime_rising_min_ns: float
    deadtime_rising_max_ns: float
    deadtime_falling_mean_ns: float
    deadtime_falling_min_ns: float
    deadtime_falling_max_ns: float
    deadtime_min_ns: float
    has_overlap: bool
    overlap_count: int
    max_overlap_duration_ns: float
    missing_pulse_count: int
    shoot_through_risk: bool
    valid: bool
    message: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["pwm_high"] = self.pwm_high.to_dict()
        d["pwm_low"] = self.pwm_low.to_dict()
        return d


def analyze_complementary_pair(
    high_raw_bytes: bytes,
    low_raw_bytes: bytes,
    sample_rate: float,
    high_channel: int = 0,
    low_channel: int = 1,
    max_samples: Optional[int] = None
) -> ComplementaryMeasurement:
    """
    Analyze complementary PWM switching pair (e.g. half-bridge / H-bridge legs).
    Detects deadtime, shoot-through overlaps, and pulse symmetry.
    """
    pwm_h = analyze_pwm(high_raw_bytes, sample_rate, high_channel, max_samples)
    pwm_l = analyze_pwm(low_raw_bytes, sample_rate, low_channel, max_samples)

    h_samples = bit_to_samples(high_raw_bytes, max_samples)
    l_samples = bit_to_samples(low_raw_bytes, max_samples)

    min_len = min(len(h_samples), len(l_samples))
    if min_len == 0:
        return ComplementaryMeasurement(
            high_channel=high_channel, low_channel=low_channel,
            pwm_high=pwm_h, pwm_low=pwm_l,
            deadtime_rising_mean_ns=0.0, deadtime_rising_min_ns=0.0, deadtime_rising_max_ns=0.0,
            deadtime_falling_mean_ns=0.0, deadtime_falling_min_ns=0.0, deadtime_falling_max_ns=0.0,
            deadtime_min_ns=0.0, has_overlap=False, overlap_count=0,
            max_overlap_duration_ns=0.0, missing_pulse_count=0, shoot_through_risk=False,
            valid=False, message="Empty sample buffers"
        )

    h_arr = h_samples[:min_len]
    l_arr = l_samples[:min_len]

    # Check simultaneous conduction (Overlap / Shoot-Through)
    overlap_mask = (h_arr == 1) & (l_arr == 1)
    overlap_samples = np.sum(overlap_mask)
    has_overlap = bool(overlap_samples > 0)

    overlap_count = 0
    max_overlap_ns = 0.0
    if has_overlap:
        # Find contiguous overlap runs
        diff_ov = np.diff(overlap_mask.astype(np.int8))
        starts = np.where(diff_ov == 1)[0] + 1
        ends = np.where(diff_ov == -1)[0] + 1
        if overlap_mask[0]:
            starts = np.insert(starts, 0, 0)
        if overlap_mask[-1]:
            ends = np.append(ends, len(overlap_mask))

        overlap_count = len(starts)
        durations = (ends - starts) / sample_rate * 1e9
        max_overlap_ns = float(np.max(durations)) if len(durations) > 0 else 0.0

    # Deadtime Extraction using transition timestamps
    _, h_edges, h_levels = extract_edges(high_raw_bytes, sample_rate, min_len)
    _, l_edges, l_levels = extract_edges(low_raw_bytes, sample_rate, min_len)

    h_rising = h_edges[h_levels == 1]
    h_falling = h_edges[h_levels == 0]
    l_rising = l_edges[l_levels == 1]
    l_falling = l_edges[l_levels == 0]

    # Deadtime Rising: low falling to high rising (DT1)
    dt_rising_ns = []
    for hr in h_rising:
        prior_lf = l_falling[l_falling < hr]
        if len(prior_lf) > 0:
            dt = (hr - prior_lf[-1]) / sample_rate * 1e9
            if dt > 0 and dt < 100000: # Filter sanity bounds
                dt_rising_ns.append(dt)

    # Deadtime Falling: high falling to low rising (DT2)
    dt_falling_ns = []
    for lr in l_rising:
        prior_hf = h_falling[h_falling < lr]
        if len(prior_hf) > 0:
            dt = (lr - prior_hf[-1]) / sample_rate * 1e9
            if dt > 0 and dt < 100000:
                dt_falling_ns.append(dt)

    all_dts = dt_rising_ns + dt_falling_ns
    min_dt = float(np.min(all_dts)) if all_dts else 0.0

    dt_r_mean = float(np.mean(dt_rising_ns)) if dt_rising_ns else 0.0
    dt_r_min = float(np.min(dt_rising_ns)) if dt_rising_ns else 0.0
    dt_r_max = float(np.max(dt_rising_ns)) if dt_rising_ns else 0.0

    dt_f_mean = float(np.mean(dt_falling_ns)) if dt_falling_ns else 0.0
    dt_f_min = float(np.min(dt_falling_ns)) if dt_falling_ns else 0.0
    dt_f_max = float(np.max(dt_falling_ns)) if dt_falling_ns else 0.0

    missing_pulses = abs(len(h_rising) - len(l_rising))
    shoot_through = has_overlap or (min_dt <= 0.0)

    return ComplementaryMeasurement(
        high_channel=high_channel,
        low_channel=low_channel,
        pwm_high=pwm_h,
        pwm_low=pwm_l,
        deadtime_rising_mean_ns=dt_r_mean,
        deadtime_rising_min_ns=dt_r_min,
        deadtime_rising_max_ns=dt_r_max,
        deadtime_falling_mean_ns=dt_f_mean,
        deadtime_falling_min_ns=dt_f_min,
        deadtime_falling_max_ns=dt_f_max,
        deadtime_min_ns=min_dt,
        has_overlap=has_overlap,
        overlap_count=overlap_count,
        max_overlap_duration_ns=max_overlap_ns,
        missing_pulse_count=int(missing_pulses),
        shoot_through_risk=shoot_through,
        valid=pwm_h.valid and pwm_l.valid,
        message="SHOOT_THROUGH_OVERLAP_DETECTED" if shoot_through else "OK"
    )
