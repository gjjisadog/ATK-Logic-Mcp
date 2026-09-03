import numpy as np
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any
from .edge import extract_edges
from .pwm import analyze_pwm, PwmMeasurement


@dataclass
class ThreePhaseMeasurement:
    u_channel: int
    v_channel: int
    w_channel: int
    pwm_u: PwmMeasurement
    pwm_v: PwmMeasurement
    pwm_w: PwmMeasurement
    frequency_mean_hz: float
    frequency_diff_max_hz: float
    phase_shift_uv_deg: float
    phase_shift_vw_deg: float
    phase_shift_wu_deg: float
    phase_balance_error_deg: float
    duty_cycle_balance_error: float
    is_balanced: bool
    valid: bool
    message: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["pwm_u"] = self.pwm_u.to_dict()
        d["pwm_v"] = self.pwm_v.to_dict()
        d["pwm_w"] = self.pwm_w.to_dict()
        return d


def analyze_three_phase(
    u_raw: bytes,
    v_raw: bytes,
    w_raw: bytes,
    sample_rate: float,
    u_channel: int = 0,
    v_channel: int = 1,
    w_channel: int = 2,
    max_samples: Optional[int] = None
) -> ThreePhaseMeasurement:
    """
    Analyze 3-phase PWM system (U, V, W legs).
    Measures per-phase PWM, phase shift angles (target 120 deg), frequency uniformity, and balance.
    """
    pu = analyze_pwm(u_raw, sample_rate, u_channel, max_samples)
    pv = analyze_pwm(v_raw, sample_rate, v_channel, max_samples)
    pw = analyze_pwm(w_raw, sample_rate, w_channel, max_samples)

    if not (pu.valid and pv.valid and pw.valid):
        return ThreePhaseMeasurement(
            u_channel=u_channel, v_channel=v_channel, w_channel=w_channel,
            pwm_u=pu, pwm_v=pv, pwm_w=pw,
            frequency_mean_hz=0.0, frequency_diff_max_hz=0.0,
            phase_shift_uv_deg=0.0, phase_shift_vw_deg=0.0, phase_shift_wu_deg=0.0,
            phase_balance_error_deg=0.0, duty_cycle_balance_error=0.0,
            is_balanced=False, valid=False, message="One or more phases failed PWM analysis"
        )

    freqs = [pu.frequency_mean_hz, pv.frequency_mean_hz, pw.frequency_mean_hz]
    freq_mean = float(np.mean(freqs))
    freq_diff_max = float(np.max(freqs) - np.min(freqs))
    period_samples = sample_rate / freq_mean

    # Extract rising edges
    _, u_edges, u_levels = extract_edges(u_raw, sample_rate, max_samples)
    _, v_edges, v_levels = extract_edges(v_raw, sample_rate, max_samples)
    _, w_edges, w_levels = extract_edges(w_raw, sample_rate, max_samples)

    u_rise = u_edges[u_levels == 1]
    v_rise = v_edges[v_levels == 1]
    w_rise = w_edges[w_levels == 1]

    # Compute phase shift angles
    def compute_shift(e1, e2):
        shifts = []
        for t1 in e1:
            future_e2 = e2[e2 > t1]
            if len(future_e2) > 0:
                dt = future_e2[0] - t1
                deg = (dt % period_samples) / period_samples * 360.0
                shifts.append(deg)
        return float(np.mean(shifts)) if shifts else 0.0

    shift_uv = compute_shift(u_rise, v_rise)
    shift_vw = compute_shift(v_rise, w_rise)
    shift_wu = compute_shift(w_rise, u_rise)

    # Balance errors
    phase_err = max(abs(shift_uv - 120.0), abs(shift_vw - 120.0), abs(shift_wu - 120.0))
    duties = [pu.duty_cycle_mean, pv.duty_cycle_mean, pw.duty_cycle_mean]
    duty_err = float(np.max(duties) - np.min(duties))

    is_bal = (phase_err < 15.0) and (freq_diff_max < 0.05 * freq_mean)

    return ThreePhaseMeasurement(
        u_channel=u_channel,
        v_channel=v_channel,
        w_channel=w_channel,
        pwm_u=pu,
        pwm_v=pv,
        pwm_w=pw,
        frequency_mean_hz=freq_mean,
        frequency_diff_max_hz=freq_diff_max,
        phase_shift_uv_deg=shift_uv,
        phase_shift_vw_deg=shift_vw,
        phase_shift_wu_deg=shift_wu,
        phase_balance_error_deg=phase_err,
        duty_cycle_balance_error=duty_err,
        is_balanced=is_bal,
        valid=True,
        message="OK" if is_bal else "UNBALANCED_THREE_PHASE"
    )
