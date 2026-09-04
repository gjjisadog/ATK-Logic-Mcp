import pytest
import json
import shutil
import numpy as np
from pathlib import Path
from unittest.mock import patch

from atk_dl16_mcp.server import (
    logic_assert,
    _load_capture_channel_bits
)
from analysis.pwm import analyze_pwm
from analysis.complementary import analyze_complementary_pair
from analysis.three_phase import analyze_three_phase
from tests.hil.run_dk9_hil import validate_hil_spec, run_dk9_hil_test, HilSpecError


def test_fail_closed_missing_sample_rate(tmp_path):
    """Fail closed when sample_rate is missing or invalid in metadata."""
    cap_dir = tmp_path / "cap_no_sr"
    cap_dir.mkdir()
    meta = {
        "evidence_source": "REAL_HARDWARE",
        "data_integrity": "COMPLETE",
        "requested_samples": 1000
    }
    with open(cap_dir / "meta.json", "w") as f:
        json.dump(meta, f)
    with open(cap_dir / "ch00.bits", "wb") as f:
        f.write(b"\x00" * 200)

    with pytest.raises(ValueError) as exc_info:
        _load_capture_channel_bits(str(cap_dir), 0)
    assert "ANALYSIS_INVALID" in str(exc_info.value)
    assert "sample_rate" in str(exc_info.value)


def test_fail_closed_truncated_artifact(tmp_path):
    """Fail closed when raw .bits file is shorter than metadata sample count."""
    cap_dir = tmp_path / "cap_truncated"
    cap_dir.mkdir()
    meta = {
        "evidence_source": "REAL_HARDWARE",
        "data_integrity": "COMPLETE",
        "sample_rate": 10_000_000.0,
        "requested_samples": 80_000 # Requires 10,000 bytes
    }
    with open(cap_dir / "meta.json", "w") as f:
        json.dump(meta, f)
    # Write only 500 bytes (severe truncation)
    with open(cap_dir / "ch00.bits", "wb") as f:
        f.write(b"\xaa" * 500)

    raw_bytes, loaded_meta = _load_capture_channel_bits(str(cap_dir), 0)
    assert loaded_meta["data_integrity"] == "ARTIFACT_INCOMPLETE"

    # Assert tool must fail closed on incomplete artifact
    assert_res = logic_assert(
        capture_id=str(cap_dir),
        channel=0,
        freq_min_hz=1000.0,
        freq_max_hz=20000.0
    )
    assert assert_res["passed"] is False
    assert any("ARTIFACT_INCOMPLETE" in f for f in assert_res["failures"])


def test_fail_closed_unknown_data_integrity(tmp_path):
    """Fail closed when data_integrity is UNKNOWN or not COMPLETE."""
    cap_dir = tmp_path / "cap_unknown_integrity"
    cap_dir.mkdir()
    meta = {
        "evidence_source": "REAL_HARDWARE",
        "data_integrity": "UNKNOWN",
        "sample_rate": 10_000_000.0,
        "requested_samples": 8000
    }
    with open(cap_dir / "meta.json", "w") as f:
        json.dump(meta, f)
    with open(cap_dir / "ch00.bits", "wb") as f:
        f.write(b"\x55" * 1000)

    assert_res = logic_assert(
        capture_id=str(cap_dir),
        channel=0,
        freq_min_hz=1000.0,
        freq_max_hz=20000.0
    )
    assert assert_res["passed"] is False
    assert any("incomplete" in f.lower() or "unknown" in f.lower() for f in assert_res["failures"])


def test_fail_closed_hil_spec_validation():
    """HIL specification schema must reject deprecated and unknown assertion keys."""
    # 1. Deprecated pwm_frequency
    spec_old_freq = {
        "test_suite": "Old Suite",
        "capture": {"channels": [0], "sample_rate_hz": 1e6, "duration_ms": 10, "mode": "buffer"},
        "assertions": {"pwm_frequency": {"target_hz": 1000.0}}
    }
    with pytest.raises(HilSpecError) as exc:
        validate_hil_spec(spec_old_freq)
    assert "pwm_frequency" in str(exc.value)

    # 2. Deprecated three_phase_balance
    spec_old_bal = {
        "test_suite": "Old Suite",
        "capture": {"channels": [0, 1, 2], "sample_rate_hz": 1e6, "duration_ms": 10, "mode": "buffer"},
        "assertions": {"three_phase_balance": {}}
    }
    with pytest.raises(HilSpecError) as exc:
        validate_hil_spec(spec_old_bal)
    assert "three_phase_balance" in str(exc.value)

    # 3. Stream mode rejected in HIL
    spec_stream = {
        "test_suite": "Stream Suite",
        "capture": {"channels": [0], "sample_rate_hz": 1e6, "duration_ms": 10, "mode": "stream"},
        "assertions": {"pwm_carrier": {"target_hz": 10000.0}}
    }
    with pytest.raises(HilSpecError) as exc:
        validate_hil_spec(spec_stream)
    assert "buffer" in str(exc.value)


def test_fail_closed_pwm_anomalies_split():
    """Verify PWM measurement_valid vs anomaly_free vs valid fail-closed behavior."""
    sample_rate = 10_000_000.0
    period = 1000
    n_cycles = 20
    total = period * n_cycles

    # 1. Missing pulse anomaly
    bits_missing = np.zeros(total, dtype=np.uint8)
    for k in range(n_cycles):
        base = k * period
        if k != 5: # Drop cycle 5
            bits_missing[base : base + 500] = 1
    raw_missing = np.packbits(bits_missing, bitorder='little').tobytes()

    meas_missing = analyze_pwm(raw_missing, sample_rate)
    assert meas_missing.measurement_valid is True  # Enough cycles exist
    assert meas_missing.anomaly_free is False       # But dropped pulses exist
    assert meas_missing.missing_pulse_count >= 1
    assert meas_missing.valid is False

    # 2. Glitch anomaly (single falling edge but pulse narrower than glitch_threshold)
    sr_hi = 100_000_000.0 # 10 ns resolution
    p_hi = 10000
    tot_hi = p_hi * 15
    b_glitch = np.zeros(tot_hi, dtype=np.uint8)
    for k in range(15):
        base = k * p_hi
        if k == 4:
            # Narrow 2 samples = 20 ns pulse (below 50 ns threshold)
            b_glitch[base : base + 2] = 1
        else:
            b_glitch[base : base + 5000] = 1

    meas_glitch = analyze_pwm(np.packbits(b_glitch, bitorder='little').tobytes(), sr_hi, glitch_threshold_ns=50.0)
    assert meas_glitch.measurement_valid is True
    assert meas_glitch.anomaly_free is False
    assert meas_glitch.glitch_count >= 1
    assert meas_glitch.valid is False


def test_fail_closed_shoot_through_overlap():
    """Verify complementary pair detects shoot-through overlap and fails closed."""
    sample_rate = 100_000_000.0
    period = 10000
    n_cycles = 15
    total = period * n_cycles

    h_bits = np.zeros(total, dtype=np.uint8)
    l_bits = np.zeros(total, dtype=np.uint8)

    for k in range(n_cycles):
        base = k * period
        # High-side active in [1000, 6000]
        h_bits[base + 1000 : base + 6000] = 1
        # Low-side active outside [900, 6100], but bug: overlaps High at rising edge [950, 1050]
        # (overlap from 1000 to 1050 = 50 samples = 500 ns shoot-through)
        l_bits[base : base + 1050] = 1
        l_bits[base + 6100 : base + period] = 1

    h_raw = np.packbits(h_bits, bitorder='little').tobytes()
    l_raw = np.packbits(l_bits, bitorder='little').tobytes()

    pair = analyze_complementary_pair(h_raw, l_raw, sample_rate, 0, 1)
    assert pair.has_overlap is True
    assert pair.overlap_count > 0


def test_fail_closed_three_phase_unbalanced_angles():
    """Verify three-phase analysis fails closed when phase displacement deviates from 120 deg."""
    sample_rate = 10_000_000.0
    f_sw = 16_000.0
    f_mod = 50.0
    period_samples = int(round(sample_rate / f_sw))
    n_cycles = 640
    total = period_samples * n_cycles

    t_span = np.arange(n_cycles) / f_sw
    du = 0.50 + 0.35 * np.sin(2.0 * np.pi * f_mod * t_span)
    # Severe unbalance: Phase V shifted by 30 deg instead of 120 deg
    dv = 0.50 + 0.35 * np.sin(2.0 * np.pi * f_mod * t_span - np.radians(30.0))
    dw = 0.50 + 0.35 * np.sin(2.0 * np.pi * f_mod * t_span + 2.0 * np.pi / 3.0)

    u_bits = np.zeros(total, dtype=np.uint8)
    v_bits = np.zeros(total, dtype=np.uint8)
    w_bits = np.zeros(total, dtype=np.uint8)

    for k in range(n_cycles):
        base = k * period_samples
        u_high = int(round(du[k] * period_samples))
        v_high = int(round(dv[k] * period_samples))
        w_high = int(round(dw[k] * period_samples))

        u_bits[base : base + u_high] = 1
        v_bits[base : base + v_high] = 1
        w_bits[base : base + w_high] = 1

    res = analyze_three_phase(
        np.packbits(u_bits, bitorder='little').tobytes(),
        np.packbits(v_bits, bitorder='little').tobytes(),
        np.packbits(w_bits, bitorder='little').tobytes(),
        sample_rate
    )

    assert res.modulation.is_balanced is False
    assert res.valid is False
    assert "UNBALANCED" in res.message


def test_fail_closed_hil_runner_incomplete_capture():
    """HIL test runner must return HIL_FAIL if physical capture integrity is not COMPLETE."""
    mock_status = {"connected": True, "ready": True, "is_busy": False}
    mock_cap_res = {
        "success": True,
        "evidence_source": "REAL_HARDWARE",
        "data_integrity": "INCOMPLETE",
        "capture_id": "test_incomplete"
    }

    with patch("tests.hil.run_dk9_hil.logic_status", return_value=mock_status), \
         patch("tests.hil.run_dk9_hil.logic_capture", return_value=mock_cap_res):

        rep = run_dk9_hil_test("tests/hil/dk9_openloop_pwm.yaml")

    assert rep["status"] == "HIL_FAIL"
    assert "integrity" in rep["reason"].lower()

