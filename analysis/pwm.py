import numpy as np
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Any
from .edge import extract_edges


@dataclass
class PwmMeasurement:
    channel: int
    cycle_count: int               # Number of valid analyzed cycles
    cycles_total: int              # Total cycles detected across capture
    cycles_valid: int              # Cycles passing normal PWM classification
    missing_pulse_count: int       # Detected dropped/missing pulses
    extra_edge_count: int          # Detected spurious extra edges
    glitch_count: int              # Pulses narrower than resolution/threshold
    period_outlier_count: int      # Cycles deviating >50% from nominal period
    static_high: bool              # Signal permanently High
    static_low: bool               # Signal permanently Low
    measurement_resolution_ns: float # Sampling period in ns (1e9 / sample_rate)
    frequency_mean_hz: float       # Fundamental frequency: 1.0 / mean(period)
    frequency_min_hz: float
    frequency_max_hz: float
    frequency_std_hz: float
    duty_cycle_mean: float
    duty_cycle_min: float
    duty_cycle_max: float
    duty_cycle_std: float
    period_mean_s: float
    period_min_s: float
    period_max_s: float
    high_time_mean_s: float
    low_time_mean_s: float
    jitter_peak_to_peak_ns: float
    jitter_rms_ns: float
    valid: bool
    message: str
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def analyze_pwm(
    raw_bytes: bytes,
    sample_rate: float,
    channel: int = 0,
    max_samples: Optional[int] = None,
    glitch_threshold_ns: float = 10.0
) -> PwmMeasurement:
    """
    Perform deterministic cycle-by-cycle PWM analysis using a formal Cycle Classifier.
    Accurately detects missing pulses, extra edges, glitches, and period outliers.
    """
    resolution_ns = 1e9 / sample_rate
    warnings: List[str] = []

    # Enforce physical resolution bounds on glitch detection
    effective_glitch_ns = glitch_threshold_ns
    if glitch_threshold_ns < resolution_ns:
        warnings.append(
            f"Requested glitch threshold ({glitch_threshold_ns:.1f} ns) is below sampling "
            f"resolution ({resolution_ns:.1f} ns). Clamped to {resolution_ns:.1f} ns."
        )
        effective_glitch_ns = resolution_ns

    glitch_thresh_s = effective_glitch_ns * 1e-9

    initial_level, edges, levels = extract_edges(raw_bytes, sample_rate, max_samples)

    # Static signal detection
    if len(edges) == 0:
        is_hi = (initial_level == 1)
        return PwmMeasurement(
            channel=channel, cycle_count=0, cycles_total=0, cycles_valid=0,
            missing_pulse_count=0, extra_edge_count=0, glitch_count=0, period_outlier_count=0,
            static_high=is_hi, static_low=(not is_hi), measurement_resolution_ns=resolution_ns,
            frequency_mean_hz=0.0, frequency_min_hz=0.0, frequency_max_hz=0.0, frequency_std_hz=0.0,
            duty_cycle_mean=1.0 if is_hi else 0.0, duty_cycle_min=0.0, duty_cycle_max=0.0, duty_cycle_std=0.0,
            period_mean_s=0.0, period_min_s=0.0, period_max_s=0.0,
            high_time_mean_s=0.0, low_time_mean_s=0.0,
            jitter_peak_to_peak_ns=0.0, jitter_rms_ns=0.0,
            valid=False, message="Signal is static " + ("HIGH" if is_hi else "LOW"),
            warnings=warnings
        )

    if len(edges) < 4:
        return PwmMeasurement(
            channel=channel, cycle_count=0, cycles_total=0, cycles_valid=0,
            missing_pulse_count=0, extra_edge_count=0, glitch_count=0, period_outlier_count=0,
            static_high=False, static_low=False, measurement_resolution_ns=resolution_ns,
            frequency_mean_hz=0.0, frequency_min_hz=0.0, frequency_max_hz=0.0, frequency_std_hz=0.0,
            duty_cycle_mean=0.0, duty_cycle_min=0.0, duty_cycle_max=0.0, duty_cycle_std=0.0,
            period_mean_s=0.0, period_min_s=0.0, period_max_s=0.0,
            high_time_mean_s=0.0, low_time_mean_s=0.0,
            jitter_peak_to_peak_ns=0.0, jitter_rms_ns=0.0,
            valid=False, message="Insufficient transitions (< 4 edges) to form full PWM cycles",
            warnings=warnings
        )

    rising_indices = edges[levels == 1]
    falling_indices = edges[levels == 0]

    if len(rising_indices) < 2 or len(falling_indices) < 2:
        return PwmMeasurement(
            channel=channel, cycle_count=0, cycles_total=0, cycles_valid=0,
            missing_pulse_count=0, extra_edge_count=0, glitch_count=0, period_outlier_count=0,
            static_high=False, static_low=False, measurement_resolution_ns=resolution_ns,
            frequency_mean_hz=0.0, frequency_min_hz=0.0, frequency_max_hz=0.0, frequency_std_hz=0.0,
            duty_cycle_mean=0.0, duty_cycle_min=0.0, duty_cycle_max=0.0, duty_cycle_std=0.0,
            period_mean_s=0.0, period_min_s=0.0, period_max_s=0.0,
            high_time_mean_s=0.0, low_time_mean_s=0.0,
            jitter_peak_to_peak_ns=0.0, jitter_rms_ns=0.0,
            valid=False, message="Signal does not alternate both rising and falling edges",
            warnings=warnings
        )

    # Compute raw periods between consecutive rising edges
    raw_periods_s = np.diff(rising_indices) / sample_rate
    nominal_period_s = float(np.median(raw_periods_s))
    if nominal_period_s <= 0:
        return PwmMeasurement(
            channel=channel, cycle_count=0, cycles_total=0, cycles_valid=0,
            missing_pulse_count=0, extra_edge_count=0, glitch_count=0, period_outlier_count=0,
            static_high=False, static_low=False, measurement_resolution_ns=resolution_ns,
            frequency_mean_hz=0.0, frequency_min_hz=0.0, frequency_max_hz=0.0, frequency_std_hz=0.0,
            duty_cycle_mean=0.0, duty_cycle_min=0.0, duty_cycle_max=0.0, duty_cycle_std=0.0,
            period_mean_s=0.0, period_min_s=0.0, period_max_s=0.0,
            high_time_mean_s=0.0, low_time_mean_s=0.0,
            jitter_peak_to_peak_ns=0.0, jitter_rms_ns=0.0,
            valid=False, message="Invalid zero or negative nominal period",
            warnings=warnings
        )

    cycles_total = len(rising_indices) - 1
    missing_pulses = 0
    extra_edges = 0
    glitches = 0
    period_outliers = 0

    valid_periods_s: List[float] = []
    valid_highs_s: List[float] = []
    valid_lows_s: List[float] = []
    valid_duties: List[float] = []

    for i in range(cycles_total):
        r_start = rising_indices[i]
        r_end = rising_indices[i + 1]
        t_period = raw_periods_s[i]

        # Check for dropped / missing pulses (period is ~2x, 3x nominal)
        if t_period > 1.7 * nominal_period_s:
            dropped = int(round(t_period / nominal_period_s)) - 1
            missing_pulses += max(1, dropped)
            period_outliers += 1
            continue

        # Check for period outlier / extra rising edge
        if t_period < 0.6 * nominal_period_s:
            period_outliers += 1
            extra_edges += 1
            continue

        # Falling edges within this period
        falls = falling_indices[(falling_indices > r_start) & (falling_indices < r_end)]

        if len(falls) == 0:
            # Missing falling edge
            missing_pulses += 1
            continue
        elif len(falls) > 1:
            # Extra edges detected
            extra_edges += (len(falls) - 1)
            continue

        # Exactly 1 falling edge: compute pulse metrics
        f = falls[0]
        t_high = (f - r_start) / sample_rate
        t_low = (r_end - f) / sample_rate

        if t_high < glitch_thresh_s or t_low < glitch_thresh_s:
            glitches += 1
            continue

        duty = t_high / t_period
        valid_periods_s.append(t_period)
        valid_highs_s.append(t_high)
        valid_lows_s.append(t_low)
        valid_duties.append(duty)

    cycles_valid = len(valid_duties)

    if cycles_valid < 5:
        return PwmMeasurement(
            channel=channel, cycle_count=cycles_valid, cycles_total=cycles_total, cycles_valid=cycles_valid,
            missing_pulse_count=missing_pulses, extra_edge_count=extra_edges,
            glitch_count=glitches, period_outlier_count=period_outliers,
            static_high=False, static_low=False, measurement_resolution_ns=resolution_ns,
            frequency_mean_hz=0.0, frequency_min_hz=0.0, frequency_max_hz=0.0, frequency_std_hz=0.0,
            duty_cycle_mean=0.0, duty_cycle_min=0.0, duty_cycle_max=0.0, duty_cycle_std=0.0,
            period_mean_s=0.0, period_min_s=0.0, period_max_s=0.0,
            high_time_mean_s=0.0, low_time_mean_s=0.0,
            jitter_peak_to_peak_ns=0.0, jitter_rms_ns=0.0,
            valid=False,
            message=f"Insufficient valid cycles ({cycles_valid}/{cycles_total}). Missing pulses: {missing_pulses}, Extra edges: {extra_edges}",
            warnings=warnings
        )

    periods_arr = np.array(valid_periods_s)
    freqs_arr = 1.0 / periods_arr
    duties_arr = np.array(valid_duties)
    highs_arr = np.array(valid_highs_s)
    lows_arr = np.array(valid_lows_s)

    # Jitter calculation: variation in period around mean
    period_jitter_ns = (periods_arr - np.mean(periods_arr)) * 1e9
    jitter_ptp = float(np.ptp(period_jitter_ns))
    jitter_rms = float(np.std(period_jitter_ns))

    # Strict validity check
    is_valid = (missing_pulses == 0) and (cycles_valid >= 5) and (extra_edges == 0)
    status_msg = "OK" if is_valid else f"ANOMALIES_DETECTED: missing={missing_pulses}, extra={extra_edges}, glitches={glitches}"

    return PwmMeasurement(
        channel=channel,
        cycle_count=cycles_valid,
        cycles_total=cycles_total,
        cycles_valid=cycles_valid,
        missing_pulse_count=missing_pulses,
        extra_edge_count=extra_edges,
        glitch_count=glitches,
        period_outlier_count=period_outliers,
        static_high=False,
        static_low=False,
        measurement_resolution_ns=resolution_ns,
        frequency_mean_hz=float(1.0 / np.mean(periods_arr)),
        frequency_min_hz=float(np.min(freqs_arr)),
        frequency_max_hz=float(np.max(freqs_arr)),
        frequency_std_hz=float(np.std(freqs_arr)),
        duty_cycle_mean=float(np.mean(duties_arr)),
        duty_cycle_min=float(np.min(duties_arr)),
        duty_cycle_max=float(np.max(duties_arr)),
        duty_cycle_std=float(np.std(duties_arr)),
        period_mean_s=float(np.mean(periods_arr)),
        period_min_s=float(np.min(periods_arr)),
        period_max_s=float(np.max(periods_arr)),
        high_time_mean_s=float(np.mean(highs_arr)),
        low_time_mean_s=float(np.mean(lows_arr)),
        jitter_peak_to_peak_ns=jitter_ptp,
        jitter_rms_ns=jitter_rms,
        valid=is_valid,
        message=status_msg,
        warnings=warnings
    )
