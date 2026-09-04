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
    carrier_phase_status: str              # "NOT_MEASURED", "SYNCHRONIZED", "DESYNCHRONIZED", "INVALID_REFERENCE"
    carrier_phase_offset_ns: Optional[float] = None
    carrier_is_synchronized: Optional[bool] = None
    carrier_sync_offset_uv_ns: Optional[float] = None
    carrier_sync_offset_vw_ns: Optional[float] = None
    carrier_jitter_rms_ns: float = 0.0


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
    max_samples: Optional[int] = None,
    carrier_ref_raw: Optional[bytes] = None
) -> ThreePhaseMeasurement:
    """
    Analyze 3-phase PWM system (U, V, W inverter legs) with physical separation of:
      1. Carrier-Level: Switching frequency consistency across phases, optional carrier synchronization
         against dedicated reference (e.g. EPWM11 / CarrierSync).
      2. Modulation-Level: Fundamental modulation envelope (sine 120 deg phase shift or balanced constant duty),
         extracted using per-channel edges to respect center-aligned symmetric PWM semantics.
    """
    pu = analyze_pwm(u_raw, sample_rate, u_channel, max_samples)
    pv = analyze_pwm(v_raw, sample_rate, v_channel, max_samples)
    pw = analyze_pwm(w_raw, sample_rate, w_channel, max_samples)

    carrier_fallback = CarrierMetrics(
        carrier_frequency_u_hz=0.0,
        carrier_frequency_v_hz=0.0,
        carrier_frequency_w_hz=0.0,
        carrier_frequency_mean_hz=0.0,
        carrier_frequency_diff_max_hz=0.0,
        carrier_phase_status="NOT_MEASURED" if carrier_ref_raw is None else "INVALID_REFERENCE",
        carrier_phase_offset_ns=None,
        carrier_is_synchronized=None if carrier_ref_raw is None else False,
        carrier_sync_offset_uv_ns=None,
        carrier_sync_offset_vw_ns=None,
        carrier_jitter_rms_ns=0.0
    )
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
    max_jitter_rms = float(max(pu.jitter_rms_ns, pv.jitter_rms_ns, pw.jitter_rms_ns))

    # Extract transitions upfront for carrier and modulation analysis
    _, u_edges, u_levels = extract_edges(u_raw, sample_rate, max_samples)
    _, v_edges, v_levels = extract_edges(v_raw, sample_rate, max_samples)
    _, w_edges, w_levels = extract_edges(w_raw, sample_rate, max_samples)

    # Carrier phase sync:
    # In center-aligned symmetric PWM (e.g. TI F28P65 Up-Down count), rising edges of U, V, W
    # are modulated by CMPA/CMPB and inherently shift relative to each other even when
    # time-base counters (TBCTR) are 100% synchronized.
    # Therefore, carrier phase synchronization CANNOT be measured across output edges.
    # It requires a dedicated carrier reference channel (e.g. EPWM11 / CarrierSync).
    if carrier_ref_raw is not None:
        _, ref_edges, ref_levels = extract_edges(carrier_ref_raw, sample_rate, max_samples)
        ref_rise = ref_edges[ref_levels == 1]
        if len(ref_rise) >= 5:
            ref_periods = np.diff(ref_rise) / sample_rate
            ref_freq = 1.0 / float(np.mean(ref_periods))
            freq_match = abs(ref_freq - freq_mean) <= 0.02 * freq_mean

            # Calculate pulse centers for phase U
            # In center-aligned PWM, pulse center (t_rise + t_fall) / 2 is invariant to duty cycle
            u_rises = u_edges[u_levels == 1]
            u_falls = u_edges[u_levels == 0]
            carrier_period_samples = sample_rate / freq_mean
            carrier_period_ns = (1.0 / freq_mean) * 1e9

            offsets = []
            for r in ref_rise[:100]:
                r_u = u_rises[(u_rises >= r) & (u_rises < r + carrier_period_samples)]
                if len(r_u) > 0:
                    r0 = r_u[0]
                    f_u = u_falls[(u_falls > r0) & (u_falls < r0 + carrier_period_samples)]
                    if len(f_u) > 0:
                        f0 = f_u[0]
                        u_center = (r0 + f0) / 2.0
                        diff_samples = (u_center - r) % carrier_period_samples
                        dev_samples = min(
                            diff_samples,
                            abs(diff_samples - 0.5 * carrier_period_samples),
                            abs(diff_samples - carrier_period_samples)
                        )
                        offsets.append(dev_samples / sample_rate * 1e9)

            med_offset_ns = float(np.median(offsets)) if offsets else 0.0
            is_synced = freq_match and (med_offset_ns < 0.05 * carrier_period_ns)
            carrier_phase_status = "SYNCHRONIZED" if is_synced else "DESYNCHRONIZED"
            carrier_is_synchronized = is_synced
            carrier_phase_offset_ns = med_offset_ns
        else:
            carrier_phase_status = "INVALID_REFERENCE"
            carrier_is_synchronized = False
            carrier_phase_offset_ns = None
    else:
        carrier_phase_status = "NOT_MEASURED"
        carrier_is_synchronized = None
        carrier_phase_offset_ns = None

    carrier = CarrierMetrics(
        carrier_frequency_u_hz=pu.frequency_mean_hz,
        carrier_frequency_v_hz=pv.frequency_mean_hz,
        carrier_frequency_w_hz=pw.frequency_mean_hz,
        carrier_frequency_mean_hz=freq_mean,
        carrier_frequency_diff_max_hz=freq_diff_max,
        carrier_phase_status=carrier_phase_status,
        carrier_phase_offset_ns=carrier_phase_offset_ns,
        carrier_is_synchronized=carrier_is_synchronized,
        carrier_sync_offset_uv_ns=None,
        carrier_sync_offset_vw_ns=None,
        carrier_jitter_rms_ns=max_jitter_rms
    )

    # 2. Modulation-level Analysis: Per-cycle Duty Envelopes
    # Extract duty cycles independently per phase using each channel's own rising and falling edges

    def _extract_channel_duties(edges: np.ndarray, levels: np.ndarray) -> List[float]:
        rises = edges[levels == 1]
        falls = edges[levels == 0]
        duties: List[float] = []
        for i in range(len(rises) - 1):
            r0 = rises[i]
            r1 = rises[i + 1]
            span = r1 - r0
            if span <= 0:
                continue
            f_in = falls[(falls > r0) & (falls < r1)]
            if len(f_in) == 1:
                duties.append(float((f_in[0] - r0) / span))
        return duties

    duties_u = _extract_channel_duties(u_edges, u_levels)
    duties_v = _extract_channel_duties(v_edges, v_levels)
    duties_w = _extract_channel_duties(w_edges, w_levels)

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

    carrier_ok = (carrier_is_synchronized is not False)
    is_overall_valid = pu.valid and pv.valid and pw.valid and modulation.is_balanced and carrier_ok

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
