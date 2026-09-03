import numpy as np
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any
from .edge import extract_edges


@dataclass
class PwmMeasurement:
    channel: int
    cycle_count: int
    frequency_mean_hz: float
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
    glitch_count: int
    valid: bool
    message: str

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
    Perform deterministic PWM cycle-by-cycle waveform analysis.
    """
    initial_level, edges, levels = extract_edges(raw_bytes, sample_rate, max_samples)

    if len(edges) < 4:
        return PwmMeasurement(
            channel=channel,
            cycle_count=0,
            frequency_mean_hz=0.0,
            frequency_min_hz=0.0,
            frequency_max_hz=0.0,
            frequency_std_hz=0.0,
            duty_cycle_mean=0.0,
            duty_cycle_min=0.0,
            duty_cycle_max=0.0,
            duty_cycle_std=0.0,
            period_mean_s=0.0,
            period_min_s=0.0,
            period_max_s=0.0,
            high_time_mean_s=0.0,
            low_time_mean_s=0.0,
            jitter_peak_to_peak_ns=0.0,
            jitter_rms_ns=0.0,
            glitch_count=0,
            valid=False,
            message="Insufficient transitions detected (< 4 edges) to form full PWM cycles"
        )

    # Find rising edges and falling edges
    rising_indices = edges[levels == 1]
    falling_indices = edges[levels == 0]

    if len(rising_indices) < 2 or len(falling_indices) < 2:
        return PwmMeasurement(
            channel=channel, cycle_count=0,
            frequency_mean_hz=0.0, frequency_min_hz=0.0, frequency_max_hz=0.0, frequency_std_hz=0.0,
            duty_cycle_mean=0.0, duty_cycle_min=0.0, duty_cycle_max=0.0, duty_cycle_std=0.0,
            period_mean_s=0.0, period_min_s=0.0, period_max_s=0.0,
            high_time_mean_s=0.0, low_time_mean_s=0.0,
            jitter_peak_to_peak_ns=0.0, jitter_rms_ns=0.0, glitch_count=0,
            valid=False, message="Signal does not alternate both rising and falling edges"
        )

    # Calculate periods between consecutive rising edges
    periods_samples = np.diff(rising_indices)
    periods_s = periods_samples / sample_rate

    # Match each period with its corresponding falling edge to compute high time
    high_times_s = []
    low_times_s = []
    duties = []

    for i in range(len(rising_indices) - 1):
        r_start = rising_indices[i]
        r_end = rising_indices[i + 1]

        # Find falling edge between r_start and r_end
        falls = falling_indices[(falling_indices > r_start) & (falling_indices < r_end)]
        if len(falls) == 1:
            f = falls[0]
            t_high = (f - r_start) / sample_rate
            t_low = (r_end - f) / sample_rate
            t_period = periods_s[i]
            duty = t_high / t_period

            high_times_s.append(t_high)
            low_times_s.append(t_low)
            duties.append(duty)

    if not duties:
        return PwmMeasurement(
            channel=channel, cycle_count=0,
            frequency_mean_hz=0.0, frequency_min_hz=0.0, frequency_max_hz=0.0, frequency_std_hz=0.0,
            duty_cycle_mean=0.0, duty_cycle_min=0.0, duty_cycle_max=0.0, duty_cycle_std=0.0,
            period_mean_s=0.0, period_min_s=0.0, period_max_s=0.0,
            high_time_mean_s=0.0, low_time_mean_s=0.0,
            jitter_peak_to_peak_ns=0.0, jitter_rms_ns=0.0, glitch_count=0,
            valid=False, message="Unable to extract regular PWM pulse cycles"
        )

    periods_arr = np.array(periods_s[:len(duties)])
    freqs_arr = 1.0 / periods_arr
    duties_arr = np.array(duties)
    highs_arr = np.array(high_times_s)
    lows_arr = np.array(low_times_s)

    # Glitch count: pulses narrower than threshold
    glitch_thresh_s = glitch_threshold_ns * 1e-9
    glitches = np.sum(highs_arr < glitch_thresh_s) + np.sum(lows_arr < glitch_thresh_s)

    # Jitter calculation: variation in period
    period_jitter_ns = (periods_arr - np.mean(periods_arr)) * 1e9
    jitter_ptp = float(np.ptp(period_jitter_ns))
    jitter_rms = float(np.std(period_jitter_ns))

    return PwmMeasurement(
        channel=channel,
        cycle_count=len(duties_arr),
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
        glitch_count=int(glitches),
        valid=True,
        message="OK"
    )
