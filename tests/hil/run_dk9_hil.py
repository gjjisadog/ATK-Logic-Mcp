import sys
import yaml
import json
from pathlib import Path
from typing import Dict, Any

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from atk_dl16_mcp.server import (
    logic_status,
    logic_capture,
    _load_capture_channel_bits
)
from analysis.pwm import analyze_pwm
from analysis.complementary import analyze_complementary_pair
from analysis.three_phase import analyze_three_phase


class HilSpecError(Exception):
    """Raised when an HIL test specification is malformed or invalid."""
    pass


ALLOWED_TOP_KEYS = {"test_suite", "version", "device", "capture", "assertions"}
ALLOWED_DEVICE_KEYS = {"expected_model", "min_hardware_version"}
ALLOWED_CAPTURE_KEYS = {
    "channels", "channel_map", "sample_rate_hz", "duration_ms",
    "threshold_voltage", "trigger", "mode"
}
ALLOWED_ASSERTION_KEYS = {
    "pwm_carrier",
    "duty_cycle",
    "deadtime",
    "shoot_through_protection",
    "three_phase_modulation",
}
ALLOWED_PWM_CARRIER_KEYS = {
    "target_hz", "tolerance_hz",
    "max_output_edge_period_variation_rms_ns", "max_jitter_rms_ns",
    "max_carrier_freq_diff_hz"
}
ALLOWED_DUTY_CYCLE_KEYS = {
    "mode", "min_allowed_duty", "max_allowed_duty"
}
ALLOWED_DEADTIME_KEYS = {
    "target_ns", "min_allowed_ns", "max_allowed_ns", "target_tolerance_ns"
}
ALLOWED_SHOOT_THROUGH_KEYS = {
    "allow_overlap", "max_overlap_samples"
}
ALLOWED_THREE_PHASE_KEYS = {
    "fundamental_frequency_hz", "fundamental_tolerance_hz",
    "phase_shift_target_deg", "phase_shift_tolerance_deg",
    "phase_sequence", "require_modulation_balance"
}


def validate_hil_spec(spec: Dict[str, Any]) -> None:
    """
    Validate HIL test specification schema and fail-closed against unknown, malformed, or deprecated keys.
    """
    if not isinstance(spec, dict):
        raise HilSpecError("HIL specification must be a dictionary")

    for key in spec.keys():
        if key not in ALLOWED_TOP_KEYS:
            raise HilSpecError(f"Unknown top-level key '{key}' in HIL spec. Allowed: {sorted(ALLOWED_TOP_KEYS)}")

    if "device" in spec and isinstance(spec["device"], dict):
        for key in spec["device"].keys():
            if key not in ALLOWED_DEVICE_KEYS:
                raise HilSpecError(f"Unknown key '{key}' in 'device' section. Allowed: {sorted(ALLOWED_DEVICE_KEYS)}")

    if "capture" not in spec or not isinstance(spec["capture"], dict):
        raise HilSpecError("Missing or invalid 'capture' section in HIL spec")

    cap = spec["capture"]
    for key in cap.keys():
        if key not in ALLOWED_CAPTURE_KEYS:
            raise HilSpecError(f"Unknown key '{key}' in 'capture' section. Allowed: {sorted(ALLOWED_CAPTURE_KEYS)}")

    mode = cap.get("mode", "buffer")
    if mode != "buffer":
        raise HilSpecError(f"HIL capture mode must be 'buffer', got '{mode}'")

    for req_field in ("channels", "sample_rate_hz", "duration_ms", "threshold_voltage"):
        if req_field not in cap:
            raise HilSpecError(f"Missing required capture field '{req_field}' in HIL spec")

    channels = cap["channels"]
    if not isinstance(channels, list) or not channels:
        raise HilSpecError("'capture.channels' must be a non-empty list of channel numbers")
    if len(set(channels)) != len(channels):
        raise HilSpecError("'capture.channels' must contain unique channel numbers")
    for ch in channels:
        if not isinstance(ch, int) or ch < 0 or ch > 15:
            raise HilSpecError(f"Invalid channel number {ch} in 'capture.channels'; must be in range 0..15")

    sr = cap["sample_rate_hz"]
    if not isinstance(sr, (int, float)) or sr <= 0:
        raise HilSpecError(f"'capture.sample_rate_hz' must be a positive number, got {sr}")

    dur = cap["duration_ms"]
    if not isinstance(dur, (int, float)) or dur <= 0:
        raise HilSpecError(f"'capture.duration_ms' must be a positive number, got {dur}")

    if "threshold_voltage" in cap:
        th = cap["threshold_voltage"]
        if not isinstance(th, (int, float)) or th < -5.0 or th > 5.0:
            raise HilSpecError(f"'capture.threshold_voltage' must be between -5.0V and +5.0V, got {th}")

    if "assertions" not in spec or not isinstance(spec["assertions"], dict):
        raise HilSpecError("Missing or invalid 'assertions' section in HIL spec")

    assertions = spec["assertions"]
    if not assertions:
        raise HilSpecError("HIL assertions dictionary is empty")

    for key, aspec in assertions.items():
        if key not in ALLOWED_ASSERTION_KEYS:
            raise HilSpecError(
                f"Unknown or deprecated assertion key '{key}' in HIL spec. "
                f"Allowed keys: {sorted(ALLOWED_ASSERTION_KEYS)}"
            )
        if not isinstance(aspec, dict):
            raise HilSpecError(f"Assertion block '{key}' must be a dictionary")

        if key == "pwm_carrier":
            for k in aspec.keys():
                if k not in ALLOWED_PWM_CARRIER_KEYS:
                    raise HilSpecError(f"Unknown key '{k}' in assertions.pwm_carrier. Allowed: {sorted(ALLOWED_PWM_CARRIER_KEYS)}")
            if "target_hz" not in aspec or not isinstance(aspec["target_hz"], (int, float)) or aspec["target_hz"] <= 0:
                raise HilSpecError("assertions.pwm_carrier requires positive numeric 'target_hz'")
            if "tolerance_hz" in aspec and (not isinstance(aspec["tolerance_hz"], (int, float)) or aspec["tolerance_hz"] < 0):
                raise HilSpecError("assertions.pwm_carrier 'tolerance_hz' must be non-negative numeric")

        elif key == "duty_cycle":
            for k in aspec.keys():
                if k not in ALLOWED_DUTY_CYCLE_KEYS:
                    raise HilSpecError(f"Unknown key '{k}' in assertions.duty_cycle. Allowed: {sorted(ALLOWED_DUTY_CYCLE_KEYS)}")
            mode = aspec.get("mode", "dynamic_sine_modulation")
            if mode != "dynamic_sine_modulation":
                raise HilSpecError(f"assertions.duty_cycle mode must be 'dynamic_sine_modulation', got '{mode}'")
            min_d = aspec.get("min_allowed_duty", 0.05)
            max_d = aspec.get("max_allowed_duty", 0.95)
            if not isinstance(min_d, (int, float)) or not isinstance(max_d, (int, float)):
                raise HilSpecError("Duty bounds must be numeric")
            if not (0.0 <= min_d < max_d <= 1.0):
                raise HilSpecError(f"Duty bounds must satisfy 0.0 <= min_allowed_duty < max_allowed_duty <= 1.0; got [{min_d}, {max_d}]")

        elif key == "deadtime":
            for k in aspec.keys():
                if k not in ALLOWED_DEADTIME_KEYS:
                    raise HilSpecError(f"Unknown key '{k}' in assertions.deadtime. Allowed: {sorted(ALLOWED_DEADTIME_KEYS)}")
            if "min_allowed_ns" not in aspec or "max_allowed_ns" not in aspec:
                raise HilSpecError("assertions.deadtime requires both 'min_allowed_ns' and 'max_allowed_ns'")
            min_dt = aspec["min_allowed_ns"]
            max_dt = aspec["max_allowed_ns"]
            if not isinstance(min_dt, (int, float)) or not isinstance(max_dt, (int, float)):
                raise HilSpecError("Deadtime bounds must be numeric")
            if not (0.0 <= min_dt <= max_dt):
                raise HilSpecError(f"Deadtime bounds must satisfy 0.0 <= min_allowed_ns <= max_allowed_ns; got [{min_dt}, {max_dt}]")
            if "target_ns" in aspec:
                tgt_dt = aspec["target_ns"]
                if not isinstance(tgt_dt, (int, float)) or not (min_dt <= tgt_dt <= max_dt):
                    raise HilSpecError(f"Target deadtime {tgt_dt} ns must be within bounds [{min_dt}, {max_dt}] ns")

        elif key == "shoot_through_protection":
            for k in aspec.keys():
                if k not in ALLOWED_SHOOT_THROUGH_KEYS:
                    raise HilSpecError(f"Unknown key '{k}' in assertions.shoot_through_protection. Allowed: {sorted(ALLOWED_SHOOT_THROUGH_KEYS)}")
            allow_ov = aspec.get("allow_overlap", False)
            if not isinstance(allow_ov, bool):
                raise HilSpecError("assertions.shoot_through_protection 'allow_overlap' must be boolean")
            max_ov = aspec.get("max_overlap_samples", 0)
            if not isinstance(max_ov, int) or max_ov < 0:
                raise HilSpecError("assertions.shoot_through_protection 'max_overlap_samples' must be non-negative integer")
            if not allow_ov and max_ov != 0:
                raise HilSpecError("When allow_overlap is False, max_overlap_samples must be 0")

        elif key == "three_phase_modulation":
            for k in aspec.keys():
                if k not in ALLOWED_THREE_PHASE_KEYS:
                    raise HilSpecError(f"Unknown key '{k}' in assertions.three_phase_modulation. Allowed: {sorted(ALLOWED_THREE_PHASE_KEYS)}")
            for req_field in ("fundamental_frequency_hz", "phase_shift_target_deg", "phase_shift_tolerance_deg"):
                if req_field not in aspec:
                    raise HilSpecError(f"Missing required field '{req_field}' in assertions.three_phase_modulation")
            if not isinstance(aspec["fundamental_frequency_hz"], (int, float)) or aspec["fundamental_frequency_hz"] <= 0:
                raise HilSpecError("fundamental_frequency_hz must be positive numeric")
            if not isinstance(aspec["phase_shift_target_deg"], (int, float)) or aspec["phase_shift_target_deg"] <= 0:
                raise HilSpecError("phase_shift_target_deg must be positive numeric")
            if not isinstance(aspec["phase_shift_tolerance_deg"], (int, float)) or aspec["phase_shift_tolerance_deg"] < 0:
                raise HilSpecError("phase_shift_tolerance_deg must be non-negative numeric")
            if "phase_sequence" in aspec and aspec["phase_sequence"] not in ("UVW", "UWV"):
                raise HilSpecError(f"Invalid phase_sequence '{aspec['phase_sequence']}'; allowed values: UVW, UWV")


def run_dk9_hil_test(yaml_path: str = "tests/hil/dk9_openloop_pwm.yaml", dry_run: bool = False) -> dict:
    """
    Execute DK9 Hardware-in-the-Loop (HIL) Test.
    Strict State Model:
      - HIL_PASS: Real DL16 connected + captured + data integrity PASS + all assertions PASS.
      - HIL_FAIL: Real capture failed, incomplete/overflow, or any assertion failed.
      - HIL_NOT_RUN: No hardware connected, hardware busy, or dry-run requested.

    NO SYNTHETIC FALLBACK ALLOWED.
    """
    config_file = Path(yaml_path)
    if not config_file.is_absolute():
        config_file = ROOT_DIR / yaml_path

    if not config_file.exists():
        return {
            "suite": "DK9 HIL Test",
            "status": "HIL_FAIL",
            "reason": f"HIL config file not found: {config_file}",
            "evidence_source": "NONE",
            "passed_assertions": 0,
            "failed_assertions": 1,
            "details": [],
            "failures": [f"Config file missing: {config_file}"],
            "assertions": {}
        }

    try:
        with open(config_file, "r") as f:
            spec = yaml.safe_load(f)
    except Exception as ex:
        return {
            "suite": "DK9 HIL Test",
            "status": "HIL_FAIL",
            "reason": f"Failed to parse HIL config YAML: {ex}",
            "evidence_source": "NONE",
            "passed_assertions": 0,
            "failed_assertions": 1,
            "details": [],
            "failures": [str(ex)],
            "assertions": {}
        }

    try:
        validate_hil_spec(spec)
    except HilSpecError as ex:
        return {
            "suite": spec.get("test_suite", "DK9 HIL Test") if isinstance(spec, dict) else "DK9 HIL Test",
            "status": "HIL_FAIL",
            "reason": f"HIL specification validation error: {ex}",
            "evidence_source": "NONE",
            "passed_assertions": 0,
            "failed_assertions": 1,
            "details": [],
            "failures": [str(ex)],
            "assertions": {}
        }

    report = {
        "suite": spec.get("test_suite", "DK9 HIL Test"),
        "status": "HIL_NOT_RUN",
        "evidence_source": "NONE",
        "reason": "",
        "passed_assertions": 0,
        "failed_assertions": 0,
        "details": [],
        "failures": [],
        "assertions": {
            k: {"executed": False, "passed": False, "details": [], "failures": []}
            for k in spec["assertions"]
        }
    }

    if dry_run or "--dry-run" in sys.argv:
        report["status"] = "HIL_NOT_RUN"
        report["evidence_source"] = "NONE"
        report["reason"] = "Dry-run requested by user. Hardware capture skipped."
        return report

    status = logic_status()
    report["hardware_status"] = status

    # Check hardware presence and lock state
    if not status.get("connected", False):
        report["status"] = "HIL_NOT_RUN"
        report["evidence_source"] = "NONE"
        report["reason"] = "ATK-DL16 hardware not detected on USB bus."
        return report

    if status.get("is_busy", False):
        report["status"] = "HIL_NOT_RUN"
        report["evidence_source"] = "NONE"
        report["reason"] = f"ATK-DL16 device is currently claimed by another application ({status.get('lock_owner', 'unknown')})."
        return report

    if not status.get("ready", False):
        report["status"] = "HIL_NOT_RUN"
        report["evidence_source"] = "NONE"
        report["reason"] = f"ATK-DL16 device not ready for capture: {status.get('message', 'Device cannot be opened')}"
        return report

    # Verify expected device model
    exp_model = spec.get("device", {}).get("expected_model")
    if exp_model:
        dev_model = status.get("model_name", status.get("model", status.get("device_name", "")))
        if dev_model and exp_model.lower() not in dev_model.lower():
            report["status"] = "HIL_NOT_RUN"
            report["evidence_source"] = "NONE"
            report["reason"] = f"Connected device model '{dev_model}' does not match expected '{exp_model}' (UNSUPPORTED_DEVICE)"
            return report

    # Hardware is ready, execute REAL physical capture
    print(f"[HIL] Triggering physical capture on channels {spec['capture']['channels']}...")
    cap_res = logic_capture(
        channels=spec["capture"]["channels"],
        sample_rate_hz=spec["capture"]["sample_rate_hz"],
        duration_ms=spec["capture"]["duration_ms"],
        threshold_voltage=spec["capture"]["threshold_voltage"],
        trigger=spec["capture"].get("trigger", "immediate"),
        mode=spec["capture"].get("mode", "buffer")
    )

    if not cap_res.get("success", False):
        report["status"] = "HIL_FAIL"
        report["evidence_source"] = cap_res.get("evidence_source", "NONE")
        report["reason"] = f"Hardware capture failed: {cap_res.get('error_code', cap_res.get('error', 'unknown error'))}"
        report["failed_assertions"] += 1
        report["failures"].append(report["reason"])
        return report

    # Verify Data Integrity of real capture
    integrity = cap_res.get("data_integrity", "UNKNOWN")
    if integrity != "COMPLETE":
        report["status"] = "HIL_FAIL"
        report["evidence_source"] = cap_res.get("evidence_source", "NONE")
        report["reason"] = f"Capture data integrity check failed: {integrity}"
        report["failed_assertions"] += 1
        report["failures"].append(report["reason"])
        return report

    cap_id = cap_res["capture_id"]
    report["capture_id"] = cap_id
    report["evidence_source"] = cap_res.get("evidence_source", "REAL_HARDWARE")
    report["data_integrity"] = integrity
    sr = float(spec["capture"]["sample_rate_hz"])

    # Load captured channel samples
    channels_raw = {}
    try:
        for ch in spec["capture"]["channels"]:
            channels_raw[ch], _ = _load_capture_channel_bits(cap_id, ch)
    except Exception as ex:
        report["status"] = "HIL_FAIL"
        report["reason"] = f"Failed to load captured channel data: {ex}"
        report["failed_assertions"] += 1
        report["failures"].append(report["reason"])
        return report

    # Perform three-phase modulation analysis on high-side channels 0, 2, 4 if available
    tri = None
    if all(ch in channels_raw for ch in (0, 2, 4)):
        try:
            tri = analyze_three_phase(channels_raw[0], channels_raw[2], channels_raw[4], sr, 0, 2, 4)
        except Exception as ex:
            report["failures"].append(f"Three-phase analysis raised exception: {ex}")

    # 1. Carrier-level assertions (pwm_carrier)
    if "pwm_carrier" in spec["assertions"]:
        aspec = spec["assertions"]["pwm_carrier"]
        rec = report["assertions"]["pwm_carrier"]
        rec["executed"] = True
        carrier_pass = True

        target_hz = aspec.get("target_hz")
        tol_hz = aspec.get("tolerance_hz", 100.0)
        max_jitter_ns = aspec.get("max_output_edge_period_variation_rms_ns", aspec.get("max_jitter_rms_ns"))
        max_carrier_freq_diff_hz = aspec.get("max_carrier_freq_diff_hz")

        for ch in spec["capture"]["channels"]:
            meas = analyze_pwm(channels_raw[ch], sr, channel=ch)
            if not meas.valid:
                carrier_pass = False
                msg = f"CH{ch} PWM analysis invalid: {meas.message}"
                rec["failures"].append(msg)
                report["failures"].append(msg)
                continue

            if target_hz is not None:
                if abs(meas.frequency_mean_hz - target_hz) <= tol_hz:
                    rec["details"].append(
                        f"CH{ch} Carrier Frequency {meas.frequency_mean_hz:.1f} Hz matches target {target_hz} +/- {tol_hz} Hz - PASS"
                    )
                else:
                    carrier_pass = False
                    msg = f"CH{ch} Carrier Frequency {meas.frequency_mean_hz:.1f} Hz outside tolerance ({target_hz} +/- {tol_hz} Hz)"
                    rec["failures"].append(msg)
                    report["failures"].append(msg)

            if max_jitter_ns is not None:
                if meas.jitter_rms_ns <= max_jitter_ns:
                    rec["details"].append(
                        f"CH{ch} Output-Edge Period Variation RMS {meas.jitter_rms_ns:.2f} ns <= {max_jitter_ns} ns - PASS"
                    )
                else:
                    carrier_pass = False
                    msg = f"CH{ch} Output-Edge Period Variation RMS {meas.jitter_rms_ns:.2f} ns exceeds limit {max_jitter_ns} ns"
                    rec["failures"].append(msg)
                    report["failures"].append(msg)

        if max_carrier_freq_diff_hz is not None and tri is not None:
            if tri.carrier.carrier_frequency_diff_max_hz <= max_carrier_freq_diff_hz:
                rec["details"].append(
                    f"Three-Phase Carrier Freq Diff {tri.carrier.carrier_frequency_diff_max_hz:.2f} Hz <= limit {max_carrier_freq_diff_hz} Hz - PASS"
                )
            else:
                carrier_pass = False
                msg = f"Three-Phase Carrier Freq Diff {tri.carrier.carrier_frequency_diff_max_hz:.2f} Hz exceeds limit {max_carrier_freq_diff_hz} Hz"
                rec["failures"].append(msg)
                report["failures"].append(msg)

        rec["passed"] = carrier_pass
        if carrier_pass:
            report["passed_assertions"] += 1
            report["details"].extend(rec["details"])
        else:
            report["failed_assertions"] += 1

    # 2. Duty Cycle Assertions (duty_cycle)
    if "duty_cycle" in spec["assertions"]:
        dspec = spec["assertions"]["duty_cycle"]
        rec = report["assertions"]["duty_cycle"]
        rec["executed"] = True
        duty_pass = True

        mode = dspec.get("mode", "dynamic_sine_modulation")
        min_allowed = dspec.get("min_allowed_duty", 0.05)
        max_allowed = dspec.get("max_allowed_duty", 0.95)

        if mode == "dynamic_sine_modulation" and tri is not None:
            if not tri.modulation.is_constant_duty:
                rec["details"].append("Dynamic sine modulation verified (duty cycle is dynamic, not constant) - PASS")
            else:
                duty_pass = False
                msg = "Expected dynamic sine modulation, but duty cycle is constant across cycles"
                rec["failures"].append(msg)
                report["failures"].append(msg)

        for ch in [0, 2, 4]:
            if ch in channels_raw:
                meas = analyze_pwm(channels_raw[ch], sr, channel=ch)
                if not meas.valid:
                    duty_pass = False
                    msg = f"CH{ch} PWM analysis invalid for duty bounds: {meas.message}"
                    rec["failures"].append(msg)
                    report["failures"].append(msg)
                    continue

                if meas.duty_cycle_min >= min_allowed and meas.duty_cycle_max <= max_allowed:
                    rec["details"].append(
                        f"CH{ch} Duty cycle span [{meas.duty_cycle_min:.3f}, {meas.duty_cycle_max:.3f}] within [{min_allowed}, {max_allowed}] - PASS"
                    )
                else:
                    duty_pass = False
                    msg = f"CH{ch} Duty cycle span [{meas.duty_cycle_min:.3f}, {meas.duty_cycle_max:.3f}] outside allowed range [{min_allowed}, {max_allowed}]"
                    rec["failures"].append(msg)
                    report["failures"].append(msg)

        rec["passed"] = duty_pass
        if duty_pass:
            report["passed_assertions"] += 1
            report["details"].extend(rec["details"])
        else:
            report["failed_assertions"] += 1

    # 3. Deadtime Assertions (deadtime)
    if "deadtime" in spec["assertions"]:
        dtspec = spec["assertions"]["deadtime"]
        rec = report["assertions"]["deadtime"]
        rec["executed"] = True
        dt_pass = True

        min_dt = dtspec.get("min_allowed_ns")
        max_dt = dtspec.get("max_allowed_ns")

        for p_name, h_ch, l_ch in [("U", 0, 1), ("V", 2, 3), ("W", 4, 5)]:
            if h_ch in channels_raw and l_ch in channels_raw:
                pair = analyze_complementary_pair(channels_raw[h_ch], channels_raw[l_ch], sr, h_ch, l_ch)
                if not pair.valid:
                    dt_pass = False
                    msg = f"Phase {p_name} complementary analysis invalid: {pair.message}"
                    rec["failures"].append(msg)
                    report["failures"].append(msg)
                    continue

                if min_dt is not None and max_dt is not None:
                    if pair.deadtime_min_ns >= min_dt and pair.deadtime_max_ns <= max_dt:
                        rec["details"].append(
                            f"Phase {p_name} Deadtime range [{pair.deadtime_min_ns:.1f}, {pair.deadtime_max_ns:.1f}] ns within [{min_dt}, {max_dt}] ns - PASS"
                        )
                    else:
                        dt_pass = False
                        msg = (
                            f"Phase {p_name} Deadtime span [{pair.deadtime_min_ns:.1f}, {pair.deadtime_max_ns:.1f}] ns "
                            f"outside allowed limits [{min_dt}, {max_dt}] ns"
                        )
                        rec["failures"].append(msg)
                        report["failures"].append(msg)

        rec["passed"] = dt_pass
        if dt_pass:
            report["passed_assertions"] += 1
            report["details"].extend(rec["details"])
        else:
            report["failed_assertions"] += 1

    # 4. Shoot-Through Protection Assertions (shoot_through_protection)
    if "shoot_through_protection" in spec["assertions"]:
        stspec = spec["assertions"]["shoot_through_protection"]
        rec = report["assertions"]["shoot_through_protection"]
        rec["executed"] = True
        st_pass = True

        allow_overlap = stspec.get("allow_overlap", False)
        max_overlap_samples = stspec.get("max_overlap_samples", 0)

        for p_name, h_ch, l_ch in [("U", 0, 1), ("V", 2, 3), ("W", 4, 5)]:
            if h_ch in channels_raw and l_ch in channels_raw:
                pair = analyze_complementary_pair(channels_raw[h_ch], channels_raw[l_ch], sr, h_ch, l_ch)
                if not pair.valid:
                    st_pass = False
                    msg = f"Phase {p_name} complementary pair invalid: {pair.message}"
                    rec["failures"].append(msg)
                    report["failures"].append(msg)
                    continue

                if (not allow_overlap and pair.has_overlap) or (pair.overlap_count > max_overlap_samples):
                    st_pass = False
                    msg = f"Phase {p_name} Shoot-Through Overlap detected! Count: {pair.overlap_count}"
                    rec["failures"].append(msg)
                    report["failures"].append(msg)
                else:
                    rec["details"].append(f"Phase {p_name} Shoot-Through Protection: Zero Overlap - PASS")

        rec["passed"] = st_pass
        if st_pass:
            report["passed_assertions"] += 1
            report["details"].extend(rec["details"])
        else:
            report["failed_assertions"] += 1

    # 5. Three-Phase Modulation Balance Assertions (three_phase_modulation)
    if "three_phase_modulation" in spec["assertions"]:
        tspec = spec["assertions"]["three_phase_modulation"]
        rec = report["assertions"]["three_phase_modulation"]
        rec["executed"] = True
        tp_pass = True

        if tri is None or not tri.valid:
            tp_pass = False
            msg = f"Three-Phase analysis failed or invalid: {tri.message if tri else 'Missing phase channels'}"
            rec["failures"].append(msg)
            report["failures"].append(msg)
        else:
            fund_target = tspec.get("fundamental_frequency_hz", 50.0)
            fund_tol = tspec.get("fundamental_tolerance_hz", 2.0)
            phase_tol = tspec.get("phase_shift_tolerance_deg", 15.0)
            req_bal = tspec.get("require_modulation_balance", True)
            phase_seq = tspec.get("phase_sequence")

            if abs(tri.modulation.fundamental_frequency_hz - fund_target) <= fund_tol:
                rec["details"].append(
                    f"Three-Phase Fundamental Frequency {tri.modulation.fundamental_frequency_hz:.2f} Hz matches target {fund_target} +/- {fund_tol} Hz - PASS"
                )
            else:
                tp_pass = False
                msg = f"Three-Phase Fundamental Frequency {tri.modulation.fundamental_frequency_hz:.2f} Hz outside tolerance ({fund_target} +/- {fund_tol} Hz)"
                rec["failures"].append(msg)
                report["failures"].append(msg)

            if tri.modulation.phase_balance_error_deg <= phase_tol:
                rec["details"].append(
                    f"Three-Phase Balance Error {tri.modulation.phase_balance_error_deg:.2f}° <= {phase_tol}° "
                    f"(UV={tri.modulation.phase_shift_uv_deg:.1f}°, VW={tri.modulation.phase_shift_vw_deg:.1f}°, WU={tri.modulation.phase_shift_wu_deg:.1f}°) - PASS"
                )
            else:
                tp_pass = False
                msg = f"Three-Phase Balance Error {tri.modulation.phase_balance_error_deg:.2f}° exceeds limit {phase_tol}°"
                rec["failures"].append(msg)
                report["failures"].append(msg)

            if req_bal and not tri.modulation.is_balanced:
                tp_pass = False
                msg = "Three-Phase Modulation envelope is not balanced"
                rec["failures"].append(msg)
                report["failures"].append(msg)

            if phase_seq is not None:
                if phase_seq == "UVW":
                    uv_ok = abs(tri.modulation.phase_shift_uv_deg - 120.0) <= phase_tol
                    vw_ok = abs(tri.modulation.phase_shift_vw_deg - 120.0) <= phase_tol
                    if uv_ok and vw_ok:
                        rec["details"].append(
                            f"Three-Phase Sequence UVW verified (shift_uv={tri.modulation.phase_shift_uv_deg:.1f}°, shift_vw={tri.modulation.phase_shift_vw_deg:.1f}°) - PASS"
                        )
                    else:
                        tp_pass = False
                        msg = (
                            f"Three-Phase sequence mismatch: expected UVW (~120°, ~120°), "
                            f"observed shift_uv={tri.modulation.phase_shift_uv_deg:.1f}°, shift_vw={tri.modulation.phase_shift_vw_deg:.1f}°"
                        )
                        rec["failures"].append(msg)
                        report["failures"].append(msg)
                        report["reason"] = "PHASE_SEQUENCE_MISMATCH"

        rec["passed"] = tp_pass
        if tp_pass:
            report["passed_assertions"] += 1
            report["details"].extend(rec["details"])
        else:
            report["failed_assertions"] += 1

    # Check that ALL declared assertions were executed and passed
    all_executed = all(a["executed"] for a in report["assertions"].values())
    all_passed = all(a["passed"] for a in report["assertions"].values())

    if not all_executed:
        unexec = [k for k, a in report["assertions"].items() if not a["executed"]]
        report["status"] = "HIL_FAIL"
        report["reason"] = f"One or more declared assertions were not executed: {unexec}"
        report["failures"].append(report["reason"])
    elif not all_passed or report["failed_assertions"] > 0:
        report["status"] = "HIL_FAIL"
        if any("sequence mismatch" in f.lower() for f in report["failures"]):
            report["reason"] = "PHASE_SEQUENCE_MISMATCH"
        elif not report.get("reason"):
            report["reason"] = "One or more HIL assertions failed"
    else:
        report["status"] = "HIL_PASS"
        report["reason"] = "All assertions executed and passed successfully"

    return report


if __name__ == "__main__":
    rep = run_dk9_hil_test()
    print("\n=======================================================")
    print(f"  DK9 HIL Execution Status: {rep['status']}")
    if rep.get("reason"):
        print(f"  Reason: {rep['reason']}")
    print(f"  Evidence Source: {rep['evidence_source']}")
    print(f"  Passed Assertions: {rep['passed_assertions']}")
    print(f"  Failed Assertions: {rep['failed_assertions']}")
    print("=======================================================")
    for d in rep["details"]:
        print(f"  [+] {d}")
    if rep["failures"]:
        print("\nFailures:")
        for f in rep["failures"]:
            print(f"  [-] {f}")
    print("=======================================================\n")

    if rep["status"] == "HIL_PASS":
        sys.exit(0)
    elif rep["status"] == "HIL_NOT_RUN":
        sys.exit(0)  # Non-error skip for clean CI/environments without physical board
    else:
        sys.exit(1)
