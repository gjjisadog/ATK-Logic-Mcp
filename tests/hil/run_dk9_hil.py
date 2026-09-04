import sys
import yaml
import json
from pathlib import Path

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
            "status": "HIL_FAIL",
            "reason": f"HIL config file not found: {config_file}",
            "evidence_source": "NONE",
            "passed_assertions": 0,
            "failed_assertions": 1,
            "details": [],
            "failures": [f"Config file missing: {config_file}"]
        }

    with open(config_file, "r") as f:
        spec = yaml.safe_load(f)

    report = {
        "suite": spec.get("test_suite", "DK9 HIL Test"),
        "status": "HIL_NOT_RUN",
        "evidence_source": "REAL_HARDWARE",
        "reason": "",
        "passed_assertions": 0,
        "failed_assertions": 0,
        "details": [],
        "failures": []
    }

    if dry_run or "--dry-run" in sys.argv:
        report["status"] = "HIL_NOT_RUN"
        report["reason"] = "Dry-run requested by user. Hardware capture skipped."
        return report

    status = logic_status()
    report["hardware_status"] = status

    # Check hardware presence and lock state
    if not status.get("connected", False):
        report["status"] = "HIL_NOT_RUN"
        report["reason"] = "ATK-DL16 hardware not detected on USB bus."
        return report

    if status.get("is_busy", False):
        report["status"] = "HIL_NOT_RUN"
        report["reason"] = f"ATK-DL16 device is currently claimed by another application ({status.get('lock_owner', 'unknown')})."
        return report

    if not status.get("ready", False):
        report["status"] = "HIL_NOT_RUN"
        report["reason"] = f"ATK-DL16 device not ready for capture: {status.get('message', 'Device cannot be opened')}"
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
        report["reason"] = f"Hardware capture failed: {cap_res.get('error', 'unknown error')}"
        report["failed_assertions"] += 1
        report["failures"].append(report["reason"])
        return report

    # Verify Data Integrity of real capture
    integrity = cap_res.get("data_integrity", "UNKNOWN")
    if integrity not in ("COMPLETE", "PASS"):
        report["status"] = "HIL_FAIL"
        report["reason"] = f"Capture data integrity check failed: {integrity}"
        report["failed_assertions"] += 1
        report["failures"].append(report["reason"])
        return report

    cap_id = cap_res["capture_id"]
    report["capture_id"] = cap_id
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

    # Execute Assertions on Real Captured Waveforms
    # 1. Switching Frequency & Duty Cycle Assertions
    freq_spec = spec.get("assertions", {}).get("pwm_frequency", {})
    target_f = freq_spec.get("target_hz")
    tol_f = freq_spec.get("tolerance_hz", 100.0)

    for ch in spec["capture"]["channels"]:
        meas = analyze_pwm(channels_raw[ch], sr, channel=ch)
        if not meas.valid:
            report["failed_assertions"] += 1
            report["failures"].append(f"Channel {ch} PWM analysis invalid: {meas.message}")
            continue

        if target_f is not None:
            if abs(meas.frequency_mean_hz - target_f) <= tol_f:
                report["passed_assertions"] += 1
                report["details"].append(f"CH{ch} Frequency {meas.frequency_mean_hz:.1f} Hz matches target {target_f} Hz - PASS")
            else:
                report["failed_assertions"] += 1
                report["failures"].append(f"CH{ch} Frequency {meas.frequency_mean_hz:.1f} Hz outside tolerance ({target_f} +/- {tol_f} Hz)")

    # 2. Complementary Pair Assertions (Deadtime & Shoot-through)
    dt_spec = spec.get("assertions", {}).get("deadtime", {})
    min_dt = dt_spec.get("min_allowed_ns")
    max_dt = dt_spec.get("max_allowed_ns")
    st_spec = spec.get("assertions", {}).get("shoot_through_protection", {})
    allow_overlap = st_spec.get("allow_overlap", False)

    for p_name, h_ch, l_ch in [("U", 0, 1), ("V", 2, 3), ("W", 4, 5)]:
        if h_ch in channels_raw and l_ch in channels_raw:
            pair = analyze_complementary_pair(channels_raw[h_ch], channels_raw[l_ch], sr, h_ch, l_ch)
            if not pair.valid:
                report["failed_assertions"] += 1
                report["failures"].append(f"Phase {p_name} complementary analysis invalid: {pair.message}")
                continue

            if pair.has_overlap and not allow_overlap:
                report["failed_assertions"] += 1
                report["failures"].append(f"Phase {p_name} Shoot-Through Overlap detected! Count: {pair.overlap_count}")
            else:
                report["passed_assertions"] += 1
                report["details"].append(f"Phase {p_name} Shoot-Through Protection: Zero Overlap - PASS")

            if min_dt is not None and max_dt is not None:
                if min_dt <= pair.deadtime_min_ns <= max_dt:
                    report["passed_assertions"] += 1
                    report["details"].append(f"Phase {p_name} Deadtime {pair.deadtime_min_ns:.1f} ns in range [{min_dt}, {max_dt}] ns - PASS")
                else:
                    report["failed_assertions"] += 1
                    report["failures"].append(f"Phase {p_name} Deadtime {pair.deadtime_min_ns:.1f} ns outside [{min_dt}, {max_dt}] ns")

    # 3. Three-Phase Modulation Balance Assertions
    tri_spec = spec.get("assertions", {}).get("three_phase_balance", {})
    if all(ch in channels_raw for ch in (0, 2, 4)) and tri_spec:
        tri = analyze_three_phase(channels_raw[0], channels_raw[2], channels_raw[4], sr, 0, 2, 4)
        if tri.is_balanced:
            report["passed_assertions"] += 1
            report["details"].append(f"Three-Phase Balance: UV={tri.phase_shift_uv_deg:.1f}°, VW={tri.phase_shift_vw_deg:.1f}° - PASS")
        else:
            report["failed_assertions"] += 1
            report["failures"].append(f"Three-Phase Balance assertion failed: {tri.message}")

    report["status"] = "HIL_PASS" if report["failed_assertions"] == 0 and report["passed_assertions"] > 0 else "HIL_FAIL"
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
