import numpy as np
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, List
from .edge import bit_to_samples, extract_edges
from .pwm import analyze_pwm, PwmMeasurement


@dataclass
class ComplementaryMeasurement:
    high_channel: int
    low_channel: int
    pwm_high: PwmMeasurement
    pwm_low: PwmMeasurement
    rising_deadtime_mean_ns: float
    rising_deadtime_min_ns: float
    rising_deadtime_max_ns: float
    falling_deadtime_mean_ns: float
    falling_deadtime_min_ns: float
    falling_deadtime_max_ns: float
    deadtime_min_ns: float
    has_overlap: bool
    overlap_count: int
    max_overlap_duration_ns: float
    missing_pulse_count: int
    extra_edge_count: int
    pairing_error_count: int
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
    Analyze complementary switching pair using strict Commutation Event Pairing.
    Measures rising deadtime (Low OFF -> High ON), falling deadtime (High OFF -> Low ON),
    and strictly detects shoot-through conduction overlaps.
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
            rising_deadtime_mean_ns=0.0, rising_deadtime_min_ns=0.0, rising_deadtime_max_ns=0.0,
            falling_deadtime_mean_ns=0.0, falling_deadtime_min_ns=0.0, falling_deadtime_max_ns=0.0,
            deadtime_min_ns=0.0, has_overlap=False, overlap_count=0,
            max_overlap_duration_ns=0.0, missing_pulse_count=0, extra_edge_count=0,
            pairing_error_count=0, shoot_through_risk=False,
            valid=False, message="Empty sample buffers"
        )

    h_arr = h_samples[:min_len]
    l_arr = l_samples[:min_len]

    # 1. Overlap Detection (Simultaneous Conduction / Shoot-Through)
    overlap_mask = (h_arr == 1) & (l_arr == 1)
    overlap_samples = int(np.sum(overlap_mask))
    has_overlap = (overlap_samples > 0)

    overlap_count = 0
    max_overlap_ns = 0.0
    if has_overlap:
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

    # 2. Extract Transition Edges
    _, h_edges, h_levels = extract_edges(high_raw_bytes, sample_rate, min_len)
    _, l_edges, l_levels = extract_edges(low_raw_bytes, sample_rate, min_len)

    h_rising = h_edges[h_levels == 1]
    h_falling = h_edges[h_levels == 0]
    l_rising = l_edges[l_levels == 1]
    l_falling = l_edges[l_levels == 0]

    # Determine nominal switching period
    nominal_period_samples = 0.0
    if len(h_rising) >= 2:
        nominal_period_samples = float(np.median(np.diff(h_rising)))
    elif len(l_rising) >= 2:
        nominal_period_samples = float(np.median(np.diff(l_rising)))
    else:
        nominal_period_samples = float(min_len)

    max_commutation_window = nominal_period_samples * 0.5

    # 3. Commutation Event Pairing: Low OFF -> High ON (Rising Deadtime)
    dt_rising_ns: List[float] = []
    pairing_errors = 0

    for hr in h_rising:
        # Find Low falling edge immediately preceding hr
        preceding_lf = l_falling[l_falling < hr]
        if len(preceding_lf) > 0:
            lf = preceding_lf[-1]
            dt_samples = hr - lf
            if dt_samples <= max_commutation_window:
                dt_rising_ns.append((dt_samples / sample_rate) * 1e9)
            else:
                pairing_errors += 1
        else:
            pairing_errors += 1

    # 4. Commutation Event Pairing: High OFF -> Low ON (Falling Deadtime)
    dt_falling_ns: List[float] = []
    for lr in l_rising:
        # Find High falling edge immediately preceding lr
        preceding_hf = h_falling[h_falling < lr]
        if len(preceding_hf) > 0:
            hf = preceding_hf[-1]
            dt_samples = lr - hf
            if dt_samples <= max_commutation_window:
                dt_falling_ns.append((dt_samples / sample_rate) * 1e9)
            else:
                pairing_errors += 1
        else:
            pairing_errors += 1

    all_dts = dt_rising_ns + dt_falling_ns
    min_dt = float(np.min(all_dts)) if all_dts else 0.0

    dt_r_mean = float(np.mean(dt_rising_ns)) if dt_rising_ns else 0.0
    dt_r_min = float(np.min(dt_rising_ns)) if dt_rising_ns else 0.0
    dt_r_max = float(np.max(dt_rising_ns)) if dt_rising_ns else 0.0

    dt_f_mean = float(np.mean(dt_falling_ns)) if dt_falling_ns else 0.0
    dt_f_min = float(np.min(dt_falling_ns)) if dt_falling_ns else 0.0
    dt_f_max = float(np.max(dt_falling_ns)) if dt_falling_ns else 0.0

    missing_pulses = abs(len(h_rising) - len(l_rising))
    extra_edges = pwm_h.extra_edge_count + pwm_l.extra_edge_count
    shoot_through = has_overlap or (min_dt <= 0.0)

    # Valid if both channels had valid PWM and sufficient paired commutation events
    is_valid = pwm_h.valid and pwm_l.valid and (len(all_dts) >= 10) and (pairing_errors <= len(all_dts) * 0.1)

    msg = "OK"
    if shoot_through:
        msg = f"SHOOT_THROUGH_OVERLAP_DETECTED (count={overlap_count}, max_duration={max_overlap_ns:.1f}ns)"
    elif not is_valid:
        msg = f"INVALID_PAIRING: pairing_errors={pairing_errors}, missing_pulses={missing_pulses}"

    return ComplementaryMeasurement(
        high_channel=high_channel,
        low_channel=low_channel,
        pwm_high=pwm_h,
        pwm_low=pwm_l,
        rising_deadtime_mean_ns=dt_r_mean,
        rising_deadtime_min_ns=dt_r_min,
        rising_deadtime_max_ns=dt_r_max,
        falling_deadtime_mean_ns=dt_f_mean,
        falling_deadtime_min_ns=dt_f_min,
        falling_deadtime_max_ns=dt_f_max,
        deadtime_min_ns=min_dt,
        has_overlap=has_overlap,
        overlap_count=overlap_count,
        max_overlap_duration_ns=max_overlap_ns,
        missing_pulse_count=int(missing_pulses),
        extra_edge_count=int(extra_edges),
        pairing_error_count=int(pairing_errors),
        shoot_through_risk=shoot_through,
        valid=is_valid,
        message=msg
    )
