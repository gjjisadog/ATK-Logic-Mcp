import pytest
import numpy as np
from unittest.mock import patch
from tests.hil.run_dk9_hil import run_dk9_hil_test


def generate_mock_dk9_openloop_waveforms(
    sample_rate: float = 100_000_000.0,
    f_sw: float = 16_000.0,
    f_mod: float = 50.0,
    duration_ms: float = 40.0,
    deadtime_ns: float = 1000.0
):
    """
    Generate authentic 6-channel center-aligned complementary 3-phase waveforms
    matching Hybrid30K TMS320F28P65 ePWM hardware specifications.
    """
    period_samples = int(round(sample_rate / f_sw))  # 6250 samples
    n_cycles = int(round(duration_ms * 1e-3 * f_sw)) # 640 cycles
    total_samples = period_samples * n_cycles        # 4,000,000 samples
    dt_samples = int(round(deadtime_ns * 1e-9 * sample_rate)) # 100 samples

    t_cycles = np.arange(n_cycles) / f_sw
    du = 0.50 + 0.35 * np.sin(2.0 * np.pi * f_mod * t_cycles)
    dv = 0.50 + 0.35 * np.sin(2.0 * np.pi * f_mod * t_cycles - 2.0 * np.pi / 3.0)
    dw = 0.50 + 0.35 * np.sin(2.0 * np.pi * f_mod * t_cycles + 2.0 * np.pi / 3.0)

    channels = {i: np.zeros(total_samples, dtype=np.uint8) for i in range(6)}

    for k in range(n_cycles):
        base = k * period_samples
        for phase_idx, d in [(0, du[k]), (2, dv[k]), (4, dw[k])]:
            h_ch = phase_idx
            l_ch = phase_idx + 1

            high_len = int(round(d * period_samples))
            # Center-aligned pulse inside switching cycle
            h_start = (period_samples - high_len) // 2
            h_end = h_start + high_len

            # High-side active
            channels[h_ch][base + h_start : base + h_end] = 1

            # Low-side complementary with deadband
            l_fall = max(0, h_start - dt_samples)
            l_rise = min(period_samples, h_end + dt_samples)

            if l_fall > 0:
                channels[l_ch][base : base + l_fall] = 1
            if l_rise < period_samples:
                channels[l_ch][base + l_rise : base + period_samples] = 1

    return {i: np.packbits(channels[i], bitorder='little').tobytes() for i in range(6)}


def test_mock_hil_contract_pass():
    """
    Verify full HIL execution pipeline with mocked hardware communication:
      1. Hardware detected and ready.
      2. Capture succeeds with REAL_HARDWARE and COMPLETE integrity.
      3. All 5 declared DK9 assertions executed:
         - pwm_carrier
         - duty_cycle
         - deadtime
         - shoot_through_protection
         - three_phase_modulation
      4. Report status is HIL_PASS with evidence_source REAL_HARDWARE.
    """
    raw_channels = generate_mock_dk9_openloop_waveforms()

    mock_status = {
        "connected": True,
        "ready": True,
        "is_busy": False,
        "device_name": "ATK-DL16",
        "hardware_version": 1
    }

    mock_cap_res = {
        "success": True,
        "evidence_source": "REAL_HARDWARE",
        "data_integrity": "COMPLETE",
        "capture_id": "hil_mock_pass_001",
        "requested_samples": 4_000_000,
        "minimum_actual_samples": 4_000_000,
        "actual_samples_per_channel": {i: 4_000_000 for i in range(6)},
        "trigger_ack_received": True,
        "trigger_offset_received": True,
        "capture_complete_received": True,
        "capacity_exceeded": False,
        "bandwidth_exceeded": False,
        "artifact_dir": "captures/hil_mock_pass_001",
        "warnings": []
    }

    def mock_load_channel(cap_id, ch):
        meta = {
            "evidence_source": "REAL_HARDWARE",
            "data_integrity": "COMPLETE",
            "mode": "buffer",
            "sample_rate": 100_000_000.0,
            "samples": 4_000_000
        }
        return raw_channels[ch], meta

    with patch("tests.hil.run_dk9_hil.logic_status", return_value=mock_status), \
         patch("tests.hil.run_dk9_hil.logic_capture", return_value=mock_cap_res), \
         patch("tests.hil.run_dk9_hil._load_capture_channel_bits", side_effect=mock_load_channel):

        rep = run_dk9_hil_test("tests/hil/dk9_openloop_pwm.yaml")

    assert rep["status"] == "HIL_PASS"
    assert rep["evidence_source"] == "REAL_HARDWARE"
    assert rep["failed_assertions"] == 0
    assert rep["passed_assertions"] == 5

    # Verify every declared assertion was executed and passed
    for key in ("pwm_carrier", "duty_cycle", "deadtime", "shoot_through_protection", "three_phase_modulation"):
        assert key in rep["assertions"]
        assert rep["assertions"][key]["executed"] is True
        assert rep["assertions"][key]["passed"] is True

    print("\n[MOCK_HIL_CONTRACT_PASS] Verified end-to-end HIL contract execution")

