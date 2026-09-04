import numpy as np
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, Any, List
from .edge import extract_edges
from .pwm import analyze_pwm, PwmMeasurement


@dataclass
class CarrierMetrics:
    carrier_frequency_u_hz: float
    carrier_frequency_v_hz: float
    carrier_frequency_w_hz: float
    carrier_frequency_mean_hz: float
    carrier_frequency_diff_max_hz: float
    carrier_sync_offset_uv_ns: float
    carrier_sync_offset_vw_ns: float
    carrier_is_synchronized: bool


@dataclass
class ModulationMetrics:
    is_constant_duty: bool
    fundamental_frequency_hz: float
    modulation_depth_u: float
    modulation_depth_v: float
    modulation_depth_w: float
    phase_shift_uv_deg: float
    phase_shift_vw_deg: float
    phase_shift_wu_deg: float
    phase_balance_error_deg: float
    is_balanced: bool


@dataclass
class ThreePhaseMeasurement:
    u_channel: int
    v_channel: int
    w_channel: int
    pwm_u: PwmMeasurement
    pwm_v: PwmMeasurement
    pwm_w: PwmMeasurement
    carrier: CarrierMetrics
    modulation: ModulationMetrics
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
    Analyze 3-phase PWM system (U, V, W inverter legs) with physical separation of:
      1. Carrier-Level: Switching frequency consistency, carrier synchronization (offset ~0ns).
      2. Modulation-Level: Fundamental modulation envelope (sine 120 deg phase shift or balanced constant duty).
    """
    pu = analyze_pwm(u_raw, sample_rate, u_channel, max_samples)
    pv = analyze_pwm(v_raw, sample_rate, v_channel, max_samples)
    pw = analyze_pwm(w_raw, sample_rate, w_channel, max_samples)

    carrier_fallback = CarrierMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, False)
    mod_fallback = ModulationMetrics(False, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, False)

    if not (pu.valid and pv.valid and pw.valid):
        return ThreePhaseMeasurement(
            u_channel=u_channel, v_channel=v_channel, w_channel=w_channel,
            pwm_u=pu, pwm_v=pv, pwm_w=pw,
            carrier=carrier_fallback, modulation=mod_fallback,
            valid=False, message="One or more phases failed PWM analysis"
        )

    # 1. Carrier-level Analysis
    freqs = [pu.frequency_mean_hz, pv.frequency_mean_hz, pw.frequency_mean_hz]
    freq_mean = float(np.mean(freqs))
    freq_diff_max = float(np.max(freqs) - np.min(freqs))

    # Extract rising edges to inspect carrier sync
    _, u_edges, u_levels = extract_edges(u_raw, sample_rate, max_samples)
    _, v_edges, v_levels = extract_edges(v_raw, sample_rate, max_samples)
    _, w_edges, w_levels = extract_edges(w_raw, sample_rate, max_samples)

    u_rise = u_edges[u_levels == 1]
    v_rise = v_edges[v_levels == 1]
    w_rise = w_edges[w_levels == 1]

    # Carrier offset: relative alignment between carriers
    def compute_carrier_offset_ns(e1, e2):
        offsets = []
        for t1 in e1[:100]:
            diffs = np.abs(e2.astype(np.int64) - t1)
            if len(diffs) > 0:
                min_diff = np.min(diffs)
                offsets.append(min_diff / sample_rate * 1e9)
        return float(np.median(offsets)) if offsets else 0.0

    offset_uv_ns = compute_carrier_offset_ns(u_rise, v_rise)
    offset_vw_ns = compute_carrier_offset_ns(v_rise, w_rise)
    carrier_period_ns = (1.0 / freq_mean) * 1e9
    # Synchronized if carrier offset is < 15% of switching period
    carrier_sync = (offset_uv_ns < 0.15 * carrier_period_ns) and (freq_diff_max < 0.02 * freq_mean)

    carrier = CarrierMetrics(
        carrier_frequency_u_hz=pu.frequency_mean_hz,
        carrier_frequency_v_hz=pv.frequency_mean_hz,
        carrier_frequency_w_hz=pw.frequency_mean_hz,
        carrier_frequency_mean_hz=freq_mean,
        carrier_frequency_diff_max_hz=freq_diff_max,
        carrier_sync_offset_uv_ns=offset_uv_ns,
        carrier_sync_offset_vw_ns=offset_vw_ns,
        carrier_is_synchronized=carrier_sync
    )

    # 2. Modulation-level Analysis: Per-cycle Duty Envelopes
    # Sample duty cycle over synchronous carrier periods
    period_samples = sample_rate / freq_mean
    n_cycles = min(len(u_rise), len(v_rise), len(w_rise)) - 1

    duties_u = []
    duties_v = []
    duties_w = []

    for k in range(n_cycles):
        t0 = u_rise[k]
        t1 = u_rise[k + 1]
        t_span = t1 - t0
        if t_span <= 0: continue

        # U high samples
        u_falls = u_edges[(u_levels == 0) & (u_edges > t0) & (u_edges < t1)]
        if len(u_falls) == 1:
            duties_u.append((u_falls[0] - t0) / t_span)

        # V high samples
        v_falls = v_edges[(v_levels == 0) & (v_edges > t0) & (v_edges < t1)]
        if len(v_falls) == 1:
            duties_v.append((v_falls[0] - t0) / t_span)

        # W high samples
        w_falls = w_edges[(w_levels == 0) & (w_edges > t0) & (w_edges < t1)]
        if len(w_falls) == 1:
            duties_w.append((w_falls[0] - t0) / t_span)

    min_pts = min(len(duties_u), len(duties_v), len(duties_w))
    if min_pts < 10:
        return ThreePhaseMeasurement(
            u_channel=u_channel, v_channel=v_channel, w_channel=w_channel,
            pwm_u=pu, pwm_v=pv, pwm_w=pw,
            carrier=carrier, modulation=mod_fallback,
            valid=False, message="Insufficient carrier periods to extract modulation envelope"
        )

    du = np.array(duties_u[:min_pts])
    dv = np.array(duties_v[:min_pts])
    dw = np.array(duties_w[:min_pts])

    std_u, std_v, std_w = float(np.std(du)), float(np.std(dv)), float(np.std(dw))
    is_constant_duty = (std_u < 0.02 and std_v < 0.02 and std_w < 0.02)

    if is_constant_duty:
        # Constant Duty / DC injection mode
        diff_uv = abs(float(np.mean(du)) - float(np.mean(dv)))
        diff_vw = abs(float(np.mean(dv)) - float(np.mean(dw)))
        is_bal = (diff_uv < 0.05) and (diff_vw < 0.05)

        modulation = ModulationMetrics(
            is_constant_duty=True,
            fundamental_frequency_hz=0.0,
            modulation_depth_u=0.0,
            modulation_depth_v=0.0,
            modulation_depth_w=0.0,
            phase_shift_uv_deg=0.0,
            phase_shift_vw_deg=0.0,
            phase_shift_wu_deg=0.0,
            phase_balance_error_deg=0.0,
            is_balanced=is_bal
        )
    else:
        # AC Sinusoidal Modulation Envelope Analysis
        # Estimate fundamental frequency via FFT on zero-mean duty
        du_zm = du - np.mean(du)
        fft_mag = np.abs(np.fft.rfft(du_zm))
        fft_freqs = np.fft.rfftfreq(len(du_zm), d=1.0/freq_mean)

        # Ignore DC component
        peak_idx = int(np.argmax(fft_mag[1:])) + 1
        fund_freq = float(fft_freqs[peak_idx])

        # Extract phase angles at fundamental frequency: exp(-j * 2*pi * f_m * t)
        t_arr = np.arange(len(du)) / freq_mean
        ref_wave = np.exp(-1j * 2.0 * np.pi * fund_freq * t_arr)

        coeff_u = np.sum((du - np.mean(du)) * ref_wave)
        coeff_v = np.sum((dv - np.mean(dv)) * ref_wave)
        coeff_w = np.sum((dw - np.mean(dw)) * ref_wave)

        angle_u = np.degrees(np.angle(coeff_u)) % 360.0
        angle_v = np.degrees(np.angle(coeff_v)) % 360.0
        angle_w = np.degrees(np.angle(coeff_w)) % 360.0

        shift_uv = (angle_u - angle_v) % 360.0
        shift_vw = (angle_v - angle_w) % 360.0
        shift_wu = (angle_w - angle_u) % 360.0

        err_uv = min(abs(shift_uv - 120.0), abs(shift_uv - 240.0))
        err_vw = min(abs(shift_vw - 120.0), abs(shift_vw - 240.0))
        err_wu = min(abs(shift_wu - 120.0), abs(shift_wu - 240.0))
        phase_err = float(max(err_uv, err_vw, err_wu))

        is_bal = (phase_err < 15.0) and (freq_diff_max < 0.05 * freq_mean)

        modulation = ModulationMetrics(
            is_constant_duty=False,
            fundamental_frequency_hz=fund_freq,
            modulation_depth_u=float(np.ptp(du) / 2.0),
            modulation_depth_v=float(np.ptp(dv) / 2.0),
            modulation_depth_w=float(np.ptp(dw) / 2.0),
            phase_shift_uv_deg=shift_uv,
            phase_shift_vw_deg=shift_vw,
            phase_shift_wu_deg=shift_wu,
            phase_balance_error_deg=phase_err,
            is_balanced=is_bal
        )

    is_overall_valid = pu.valid and pv.valid and pw.valid and modulation.is_balanced

    return ThreePhaseMeasurement(
        u_channel=u_channel,
        v_channel=v_channel,
        w_channel=w_channel,
        pwm_u=pu,
        pwm_v=pv,
        pwm_w=pw,
        carrier=carrier,
        modulation=modulation,
        valid=is_overall_valid,
        message="OK" if is_overall_valid else "UNBALANCED_THREE_PHASE"
    )
