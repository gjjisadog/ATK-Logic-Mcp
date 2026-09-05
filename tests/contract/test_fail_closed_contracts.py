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
        "capture": {"channels": [0], "sample_rate_hz": 1e6, "duration_ms": 10, "threshold_voltage": 1.65, "mode": "buffer"},
        "assertions": {"pwm_frequency": {"target_hz": 1000.0}}
    }
    with pytest.raises(HilSpecError) as exc:
        validate_hil_spec(spec_old_freq)
    assert "pwm_frequency" in str(exc.value)

    # 2. Deprecated three_phase_balance
    spec_old_bal = {
        "test_suite": "Old Suite",
        "capture": {"channels": [0, 1, 2], "sample_rate_hz": 1e6, "duration_ms": 10, "threshold_voltage": 1.65, "mode": "buffer"},
        "assertions": {"three_phase_balance": {}}
    }
    with pytest.raises(HilSpecError) as exc:
        validate_hil_spec(spec_old_bal)
    assert "three_phase_balance" in str(exc.value)

    # 3. Stream mode rejected in HIL
    spec_stream = {
        "test_suite": "Stream Suite",
        "capture": {"channels": [0], "sample_rate_hz": 1e6, "duration_ms": 10, "threshold_voltage": 1.65, "mode": "stream"},
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


def test_fail_closed_nested_spec_schema():
    """Verify validate_hil_spec strictly fails closed against typos and illegal bounds."""
    base_valid = {
        "test_suite": "DK9 OpenLoop PWM Verification",
        "version": "1.1.0",
        "capture": {
            "channels": [0, 1, 2, 3, 4, 5],
            "sample_rate_hz": 100_000_000,
            "duration_ms": 40,
            "threshold_voltage": 1.65,
            "mode": "buffer"
        },
        "assertions": {
            "pwm_carrier": {"target_hz": 16000.0, "tolerance_hz": 100.0},
            "duty_cycle": {"mode": "dynamic_sine_modulation", "min_allowed_duty": 0.05, "max_allowed_duty": 0.95},
            "deadtime": {"min_allowed_ns": 800.0, "max_allowed_ns": 1200.0},
            "shoot_through_protection": {"allow_overlap": False, "max_overlap_samples": 0},
            "three_phase_modulation": {
                "fundamental_frequency_hz": 50.0,
                "phase_shift_target_deg": 120.0,
                "phase_shift_tolerance_deg": 15.0,
                "phase_sequence": "UVW"
            }
        }
    }

    # 1. Typo in pwm_carrier: target_hzz
    spec_typo1 = dict(base_valid)
    spec_typo1["assertions"] = dict(base_valid["assertions"])
    spec_typo1["assertions"]["pwm_carrier"] = {"target_hzz": 16000.0, "tolerance_hz": 100.0}
    with pytest.raises(HilSpecError) as exc:
        validate_hil_spec(spec_typo1)
    assert "target_hzz" in str(exc.value)

    # 2. Typo in capture: sample_rate_hzz
    spec_typo2 = dict(base_valid)
    spec_typo2["capture"] = dict(base_valid["capture"])
    spec_typo2["capture"]["sample_rate_hzz"] = 100_000_000
    with pytest.raises(HilSpecError) as exc:
        validate_hil_spec(spec_typo2)
    assert "sample_rate_hzz" in str(exc.value)

    # 3. Bad duty bounds: min > max
    spec_bad_duty = dict(base_valid)
    spec_bad_duty["assertions"] = dict(base_valid["assertions"])
    spec_bad_duty["assertions"]["duty_cycle"] = {
        "mode": "dynamic_sine_modulation",
        "min_allowed_duty": 0.90,
        "max_allowed_duty": 0.10
    }
    with pytest.raises(HilSpecError) as exc:
        validate_hil_spec(spec_bad_duty)
    assert "Duty bounds" in str(exc.value)

    # 4. Bad deadtime bounds: min > max
    spec_bad_dt = dict(base_valid)
    spec_bad_dt["assertions"] = dict(base_valid["assertions"])
    spec_bad_dt["assertions"]["deadtime"] = {
        "min_allowed_ns": 1500.0,
        "max_allowed_ns": 800.0
    }
    with pytest.raises(HilSpecError) as exc:
        validate_hil_spec(spec_bad_dt)
    assert "Deadtime bounds" in str(exc.value)

    # 5. Invalid shoot-through setting: allow_overlap=False with max_overlap_samples > 0
    spec_bad_st = dict(base_valid)
    spec_bad_st["assertions"] = dict(base_valid["assertions"])
    spec_bad_st["assertions"]["shoot_through_protection"] = {
        "allow_overlap": False,
        "max_overlap_samples": 10
    }
    with pytest.raises(HilSpecError) as exc:
        validate_hil_spec(spec_bad_st)
    assert "max_overlap_samples" in str(exc.value)


def test_fail_closed_reversed_phase_sequence():
    """Verify reversed phase sequence UWV fails closed with PHASE_SEQUENCE_MISMATCH."""
    from tests.contract.test_mock_hil_contract import generate_mock_dk9_openloop_waveforms

    raw_channels = generate_mock_dk9_openloop_waveforms()
    # Reverse sequence by swapping Phase V (ch 2, 3) and Phase W (ch 4, 5)
    raw_reversed = {
        0: raw_channels[0],
        1: raw_channels[1],
        2: raw_channels[4],
        3: raw_channels[5],
        4: raw_channels[2],
        5: raw_channels[3],
    }

    mock_status = {"connected": True, "ready": True, "is_busy": False}
    mock_cap_res = {
        "success": True,
        "evidence_source": "REAL_HARDWARE",
        "data_integrity": "COMPLETE",
        "capture_id": "hil_mock_rev_seq",
        "requested_samples": 4_000_000,
        "minimum_actual_samples": 4_000_000,
        "actual_samples_per_channel": {i: 4_000_000 for i in range(6)},
        "trigger_ack_received": True,
        "trigger_offset_received": True,
        "capture_complete_received": True,
        "capacity_exceeded": False,
        "bandwidth_exceeded": False,
        "artifact_dir": "captures/hil_mock_rev_seq",
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
        return raw_reversed[ch], meta

    with patch("tests.hil.run_dk9_hil.logic_status", return_value=mock_status), \
         patch("tests.hil.run_dk9_hil.logic_capture", return_value=mock_cap_res), \
         patch("tests.hil.run_dk9_hil._load_capture_channel_bits", side_effect=mock_load_channel):

        rep = run_dk9_hil_test("tests/hil/dk9_openloop_pwm.yaml")

    assert rep["status"] == "HIL_FAIL"
    assert rep["reason"] == "PHASE_SEQUENCE_MISMATCH"
    assert rep["assertions"]["three_phase_modulation"]["passed"] is False
    assert any("sequence mismatch" in f.lower() for f in rep["failures"])


def test_fail_closed_deadtime_max_bound_exceeded():
    """Verify deadtime exceeding max_allowed_ns fails closed even if min_allowed_ns passes."""
    # Generate waveforms with 2500 ns deadtime (exceeds max_allowed_ns: 1200.0 ns)
    from tests.contract.test_mock_hil_contract import generate_mock_dk9_openloop_waveforms

    raw_channels = generate_mock_dk9_openloop_waveforms(deadtime_ns=2500.0)

    mock_status = {"connected": True, "ready": True, "is_busy": False}
    mock_cap_res = {
        "success": True,
        "evidence_source": "REAL_HARDWARE",
        "data_integrity": "COMPLETE",
        "capture_id": "hil_mock_excess_deadtime",
        "requested_samples": 4_000_000,
        "minimum_actual_samples": 4_000_000,
        "actual_samples_per_channel": {i: 4_000_000 for i in range(6)},
        "trigger_ack_received": True,
        "trigger_offset_received": True,
        "capture_complete_received": True,
        "capacity_exceeded": False,
        "bandwidth_exceeded": False,
        "artifact_dir": "captures/hil_mock_excess_deadtime",
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

    assert rep["status"] == "HIL_FAIL"
    assert rep["assertions"]["deadtime"]["passed"] is False
    assert any("deadtime" in f.lower() and "outside" in f.lower() for f in rep["failures"])


def test_cli_json_escape_windows_path():
    """Verify CLI JSON output correctly escapes Windows paths with backslashes and spaces."""
    import subprocess
    from atk_dl16_mcp.server import _find_cli
    cli = _find_cli()
    if not cli:
        pytest.skip("CLI not built yet")
    win_out_dir = r"D:\a\ATK-Logic-Mcp\captures\run 01\sub path"
    cmd = [str(cli), "capture", "--json", "--channels", "0,1", "--out", win_out_dir]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.stdout.strip()
    data = json.loads(proc.stdout)
    assert isinstance(data, dict)
    assert data.get("artifact_dir") == win_out_dir
    assert r"\\" in proc.stdout


def test_cli_json_escape_quote():
    """Verify CLI JSON output correctly escapes quotation marks."""
    import subprocess
    from atk_dl16_mcp.server import _find_cli
    cli = _find_cli()
    if not cli:
        pytest.skip("CLI not built yet")
    win_out_dir = r'C:\captures\"quoted_session"'
    cmd = [str(cli), "capture", "--json", "--out", win_out_dir]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(proc.stdout)
    assert isinstance(data, dict)
    assert data.get("artifact_dir") == win_out_dir
    assert r'\"quoted_session\"' in proc.stdout


def test_cli_json_escape_backslash():
    """Verify CLI JSON output correctly escapes double backslashes and network shares."""
    import subprocess
    from atk_dl16_mcp.server import _find_cli
    cli = _find_cli()
    if not cli:
        pytest.skip("CLI not built yet")
    win_out_dir = r"\\network\share\captures\\nested"
    cmd = [str(cli), "capture", "--json", "--out", win_out_dir]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(proc.stdout)
    assert isinstance(data, dict)
    assert data.get("artifact_dir") == win_out_dir


def test_cli_json_escape_control_chars():
    """Verify CLI JSON output correctly escapes control characters without JSONDecodeError."""
    import subprocess
    from atk_dl16_mcp.server import _find_cli
    cli = _find_cli()
    if not cli:
        pytest.skip("CLI not built yet")
    ctrl_dir = "captures\nwith\rnewline\tand\ttab"
    cmd = [str(cli), "capture", "--json", "--out", ctrl_dir]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(proc.stdout)
    assert isinstance(data, dict)
    assert data.get("artifact_dir") == ctrl_dir
    assert r"\n" in proc.stdout
    assert r"\t" in proc.stdout


def test_nested_hil_schema_target_hzz_reject():
    """Reject unknown key target_hzz in assertions.pwm_carrier."""
    spec = {
        "test_suite": "DK9 Test",
        "capture": {"channels": [0], "sample_rate_hz": 100e6, "duration_ms": 10, "threshold_voltage": 1.65, "mode": "buffer"},
        "assertions": {"pwm_carrier": {"target_hzz": 16000.0}}
    }
    with pytest.raises(HilSpecError) as exc:
        validate_hil_spec(spec)
    assert "target_hzz" in str(exc.value)


def test_nested_hil_schema_deadtime_empty_reject():
    """Reject empty deadtime {} block."""
    spec = {
        "test_suite": "DK9 Test",
        "capture": {"channels": [0], "sample_rate_hz": 100e6, "duration_ms": 10, "threshold_voltage": 1.65, "mode": "buffer"},
        "assertions": {"deadtime": {}}
    }
    with pytest.raises(HilSpecError) as exc:
        validate_hil_spec(spec)
    assert "deadtime" in str(exc.value).lower()


def test_nested_hil_schema_missing_threshold_voltage_reject():
    """Reject missing threshold_voltage in capture section."""
    spec = {
        "test_suite": "DK9 Test",
        "capture": {"channels": [0], "sample_rate_hz": 100e6, "duration_ms": 10, "mode": "buffer"},
        "assertions": {"pwm_carrier": {"target_hz": 16000.0}}
    }
    with pytest.raises(HilSpecError) as exc:
        validate_hil_spec(spec)
    assert "threshold_voltage" in str(exc.value)


def test_nested_hil_schema_min_duty_gt_max_duty_reject():
    """Reject min_allowed_duty > max_allowed_duty."""
    spec = {
        "test_suite": "DK9 Test",
        "capture": {"channels": [0], "sample_rate_hz": 100e6, "duration_ms": 10, "threshold_voltage": 1.65, "mode": "buffer"},
        "assertions": {"duty_cycle": {"mode": "dynamic_sine_modulation", "min_allowed_duty": 0.85, "max_allowed_duty": 0.15}}
    }
    with pytest.raises(HilSpecError) as exc:
        validate_hil_spec(spec)
    assert "Duty bounds" in str(exc.value)


def test_nested_hil_schema_min_deadtime_gt_max_deadtime_reject():
    """Reject min_allowed_ns > max_allowed_ns."""
    spec = {
        "test_suite": "DK9 Test",
        "capture": {"channels": [0], "sample_rate_hz": 100e6, "duration_ms": 10, "threshold_voltage": 1.65, "mode": "buffer"},
        "assertions": {"deadtime": {"min_allowed_ns": 2000.0, "max_allowed_ns": 1000.0}}
    }
    with pytest.raises(HilSpecError) as exc:
        validate_hil_spec(spec)
    assert "Deadtime bounds" in str(exc.value)


def test_nested_hil_schema_unknown_nested_field_reject():
    """Reject unknown nested field across all assertion sections."""
    spec = {
        "test_suite": "DK9 Test",
        "capture": {"channels": [0], "sample_rate_hz": 100e6, "duration_ms": 10, "threshold_voltage": 1.65, "mode": "buffer"},
        "assertions": {"shoot_through_protection": {"allow_overlap": False, "unknown_field": 123}}
    }
    with pytest.raises(HilSpecError) as exc:
        validate_hil_spec(spec)
    assert "unknown_field" in str(exc.value)


def test_phase_sequence_uvw_valid_pass():
    """Verify UVW phase sequence passes and is recognized."""
    from tests.contract.test_mock_hil_contract import generate_mock_dk9_openloop_waveforms
    raw_channels = generate_mock_dk9_openloop_waveforms()
    res = analyze_three_phase(
        raw_channels[0], raw_channels[2], raw_channels[4],
        sample_rate=100_000_000.0
    )
    assert res.valid is True
    assert res.modulation.is_balanced is True
    assert abs(res.modulation.phase_shift_uv_deg - 120.0) <= 15.0
    assert abs(res.modulation.phase_shift_vw_deg - 120.0) <= 15.0


def test_phase_sequence_balanced_reverse_fail():
    """Prove that balanced 120 deg displacement != correct sequence (UWV is balanced but fails sequence check)."""
    from tests.contract.test_mock_hil_contract import generate_mock_dk9_openloop_waveforms
    raw_channels = generate_mock_dk9_openloop_waveforms()
    # Swap Phase V (ch 2) and Phase W (ch 4) -> sequence becomes UWV
    res = analyze_three_phase(
        raw_channels[0], raw_channels[4], raw_channels[2],
        sample_rate=100_000_000.0
    )
    # The displacement between phases is still 120 degrees, so modulation is balanced!
    assert res.modulation.is_balanced is True
    assert abs(res.modulation.phase_shift_uv_deg - 240.0) <= 15.0
    assert abs(res.modulation.phase_shift_uv_deg - 120.0) > 15.0

    # Now verify HIL runner strictly fails on UWV when UVW expected
    mock_status = {"connected": True, "ready": True, "is_busy": False}
    mock_cap_res = {
        "success": True, "evidence_source": "REAL_HARDWARE", "data_integrity": "COMPLETE",
        "capture_id": "hil_mock_uwv", "requested_samples": 4_000_000,
        "minimum_actual_samples": 4_000_000,
        "actual_samples_per_channel": {i: 4_000_000 for i in range(6)},
        "trigger_ack_received": True, "trigger_offset_received": True,
        "capture_complete_received": True, "capacity_exceeded": False,
        "bandwidth_exceeded": False, "artifact_dir": "captures/hil_mock_uwv", "warnings": []
    }
    raw_reversed = {
        0: raw_channels[0], 1: raw_channels[1],
        2: raw_channels[4], 3: raw_channels[5],
        4: raw_channels[2], 5: raw_channels[3],
    }
    def mock_load_channel(cap_id, ch):
        return raw_reversed[ch], {"evidence_source": "REAL_HARDWARE", "data_integrity": "COMPLETE", "mode": "buffer", "sample_rate": 100e6, "samples": 4_000_000}

    with patch("tests.hil.run_dk9_hil.logic_status", return_value=mock_status), \
         patch("tests.hil.run_dk9_hil.logic_capture", return_value=mock_cap_res), \
         patch("tests.hil.run_dk9_hil._load_capture_channel_bits", side_effect=mock_load_channel):
        rep = run_dk9_hil_test("tests/hil/dk9_openloop_pwm.yaml")

    assert rep["status"] == "HIL_FAIL"
    assert rep["reason"] == "PHASE_SEQUENCE_MISMATCH"


def test_deadtime_max_old_logic_pass_new_logic_fail():
    """Verify deadtime validation:
    Normal 1000 ns -> PASS.
    One commutation 900 ns, another 3000 ns:
    Old min-only logic (min >= 800 ns) would PASS (since 900 >= 800 and 3000 >= 800).
    New min+max logic fails closed because max (3000 ns) > max_allowed_ns (1200 ns).
    """
    sr = 100_000_000.0
    period = 6250
    n_cycles = 10
    total_samples = period * n_cycles

    h_bits = np.zeros(total_samples, dtype=np.uint8)
    l_bits = np.zeros(total_samples, dtype=np.uint8)

    for k in range(n_cycles):
        base = k * period
        if k == 5:
            dt_r = 90   # 900 ns
            dt_f = 90   # 900 ns
        elif k == 6:
            dt_r = 300  # 3000 ns
            dt_f = 300  # 3000 ns
        else:
            dt_r = 100  # 1000 ns
            dt_f = 100  # 1000 ns

        h_start = 1500
        h_end = 4500
        h_bits[base + h_start : base + h_end] = 1

        l_bits[base : base + h_start - dt_r] = 1
        l_bits[base + h_end + dt_f : base + period] = 1

    h_raw = np.packbits(h_bits, bitorder="little").tobytes()
    l_raw = np.packbits(l_bits, bitorder="little").tobytes()

    pair = analyze_complementary_pair(h_raw, l_raw, sr, 0, 1)

    min_allowed = 800.0   # ns
    max_allowed = 1200.0  # ns

    # Assert old min-only logic would PASS:
    old_min_only_pass = pair.deadtime_min_ns >= min_allowed
    assert old_min_only_pass is True
    assert pair.deadtime_min_ns == pytest.approx(900.0, abs=20.0)

    # Assert new logic FAILS because deadtime_max exceeds max_allowed:
    new_logic_pass = (pair.deadtime_min_ns >= min_allowed) and (pair.deadtime_max_ns <= max_allowed)
    assert new_logic_pass is False
    assert pair.deadtime_max_ns == pytest.approx(3000.0, abs=20.0)


def test_output_edge_period_variation_dynamic_duty():
    """Verify under center-aligned triangular modulation + dynamic duty that output edge
    period variation RMS is non-zero, and verify documentation/API does NOT call it carrier clock jitter."""
    sr = 100_000_000.0
    f_carrier = 16_000.0
    f_mod = 50.0
    period_samples = int(round(sr / f_carrier))
    n_cycles = 320
    total = period_samples * n_cycles

    t_span = np.arange(n_cycles) / f_carrier
    duty = 0.50 + 0.35 * np.sin(2.0 * np.pi * f_mod * t_span)

    bits = np.zeros(total, dtype=np.uint8)
    for k in range(n_cycles):
        base = k * period_samples
        high_samples = int(round(duty[k] * period_samples))
        offset = (period_samples - high_samples) // 2
        bits[base + offset : base + offset + high_samples] = 1

    raw = np.packbits(bits, bitorder="little").tobytes()
    meas = analyze_pwm(raw, sr)

    assert meas.period_variation_rms_ns > 0.0
    assert hasattr(meas, "period_variation_rms_ns")
    assert not hasattr(meas, "carrier_clock_jitter_ns")


def test_trigger_offset_optional_when_complete():
    """Verify upstream AlienTek audit conclusion:
    Order 3 (TriggerOffset) is optional when sample depth is complete.
    When all channels receive target sample count, absence of Order 3 does not fail the capture.
    """
    mock_status = {"connected": True, "ready": True, "is_busy": False}
    from tests.contract.test_mock_hil_contract import generate_mock_dk9_openloop_waveforms
    raw_channels = generate_mock_dk9_openloop_waveforms()

    mock_cap_res = {
        "success": True,
        "evidence_source": "REAL_HARDWARE",
        "data_integrity": "COMPLETE",
        "capture_id": "hil_mock_no_order3",
        "requested_samples": 4_000_000,
        "minimum_actual_samples": 4_000_000,
        "actual_samples_per_channel": {i: 4_000_000 for i in range(6)},
        "trigger_ack_received": True,
        "trigger_offset_received": False,
        "capture_complete_received": True,
        "capacity_exceeded": False,
        "bandwidth_exceeded": False,
        "artifact_dir": "captures/hil_mock_no_order3",
        "warnings": ["TriggerOffset packet (Order 3) not received; pre-trigger offset alignment skipped"]
    }

    def mock_load_channel(cap_id, ch):
        return raw_channels[ch], {"evidence_source": "REAL_HARDWARE", "data_integrity": "COMPLETE", "mode": "buffer", "sample_rate": 100e6, "samples": 4_000_000}

    with patch("tests.hil.run_dk9_hil.logic_status", return_value=mock_status), \
         patch("tests.hil.run_dk9_hil.logic_capture", return_value=mock_cap_res), \
         patch("tests.hil.run_dk9_hil._load_capture_channel_bits", side_effect=mock_load_channel):
        rep = run_dk9_hil_test("tests/hil/dk9_openloop_pwm.yaml")

    assert rep["evidence_source"] == "REAL_HARDWARE"
    assert rep["data_integrity"] == "COMPLETE"
    assert rep["status"] == "HIL_PASS"


def test_artifact_write_failure_simulation():
    """Verify artifact write failure contract:
    save_to_directory == False -> CaptureResult.success == False, ErrorCode::ArtifactWriteError.
    """
    from atk_dl16_mcp.server import logic_capture
    fail_cli_json = json.dumps({
        "success": False,
        "error_code": "ARTIFACT_WRITE_ERROR",
        "message": "Failed to write capture artifacts to directory: /invalid/readonly/dir",
        "evidence_source": "REAL_HARDWARE",
        "data_integrity": "INCOMPLETE",
        "capture_complete_received": True,
        "artifact_dir": "/invalid/readonly/dir",
        "warnings": []
    })
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = fail_cli_json
        mock_run.return_value.stderr = ""
        res = logic_capture([0, 1], sample_rate_hz=20_000_000, duration_ms=10)

    assert res["success"] is False
    assert res["error_code"] == "ARTIFACT_WRITE_ERROR"
    assert res["data_integrity"] == "INCOMPLETE"


def test_hil_unsupported_device_model_not_run():
    """Verify that an unsupported device model returns HIL_NOT_RUN / UNSUPPORTED_DEVICE, not a waveform failure."""
    mock_status = {
        "connected": True,
        "ready": True,
        "is_busy": False,
        "model_name": "Saleae Logic Pro 8"
    }
    with patch("tests.hil.run_dk9_hil.logic_status", return_value=mock_status):
        rep = run_dk9_hil_test("tests/hil/dk9_openloop_pwm.yaml")

    assert rep["status"] == "HIL_NOT_RUN"
    assert rep["evidence_source"] == "NONE"
    assert "UNSUPPORTED_DEVICE" in rep["reason"]



