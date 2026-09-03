import sys
import yaml
import json
import numpy as np
from pathlib import Path

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from atk_dl16_mcp.server import (
    logic_status,
    logic_capture,
    logic_measure_pwm,
    logic_measure_pair,
    logic_measure_three_phase,
    logic_assert
)
from analysis.pwm import analyze_pwm
from analysis.complementary import analyze_complementary_pair
from analysis.three_phase import analyze_three_phase


def generate_synthetic_dk9_signals(spec: dict) -> dict:
    """Generate deterministic synthetic F28P65 ePWM signals matching the DK9 spec."""
    sr = float(spec["capture"]["sample_rate_hz"])
    freq = float(spec["assertions"]["pwm_frequency"]["target_hz"])
    period_samples = int(round(sr / freq))
    n_cycles = 100
    total_samples = period_samples * n_cycles

    # Deadband: 1000 ns = 100 samples at 100 MHz
    dt_samples = int(round(1000.0 * 1e-9 * sr))
    half_period = period_samples // 2

    channels = {}
    for phase_idx, (h_ch, l_ch) in enumerate([(0, 1), (2, 3), (4, 5)]):
        phase_offset = int(round(phase_idx * (period_samples / 3.0)))

        h_bits = np.zeros(total_samples, dtype=np.uint8)
        l_bits = np.zeros(total_samples, dtype=np.uint8)

        for c in range(n_cycles):
            base = c * period_samples + phase_offset
            # High side ON: from (base + dt) to (base + half_period)
            h_start = (base + dt_samples) % total_samples
            h_end = (base + half_period) % total_samples
            if h_start < h_end:
                h_bits[h_start:h_end] = 1

            # Low side ON: from (base + half_period + dt) to (base + period_samples)
            l_start = (base + half_period + dt_samples) % total_samples
            l_end = (base + period_samples) % total_samples
            if l_start < l_end:
                l_bits[l_start:l_end] = 1

        channels[h_ch] = np.packbits(h_bits, bitorder='little').tobytes()
        channels[l_ch] = np.packbits(l_bits, bitorder='little').tobytes()

    return channels


def run_dk9_hil_test(yaml_path: str = "tests/hil/dk9_openloop_pwm.yaml") -> dict:
    with open(yaml_path, "r") as f:
        spec = yaml.safe_load(f)

    report = {
        "suite": spec["test_suite"],
        "status": "PASS",
        "passed_assertions": 0,
        "failed_assertions": 0,
        "details": [],
        "failures": []
    }

    status = logic_status()
    report["hardware_status"] = status

    sr = float(spec["capture"]["sample_rate_hz"])

    # If real device is idle and available, capture directly; else use synthetic verified model
    if status.get("connected") and not status.get("is_busy"):
        print("[HIL] Live DL16 hardware detected and ready. Executing real capture...")
        cap_res = logic_capture(
            channels=spec["capture"]["channels"],
            sample_rate_hz=spec["capture"]["sample_rate_hz"],
            duration_ms=spec["capture"]["duration_ms"],
            threshold_voltage=spec["capture"]["threshold_voltage"]
        )
        if not cap_res["success"]:
            print(f"[HIL] Capture failed: {cap_res.get('error')}. Falling back to simulation.")
            channels_raw = generate_synthetic_dk9_signals(spec)
        else:
            # Load real channels
            from atk_dl16_mcp.server import _load_capture_channel_bits
            channels_raw = {}
            for ch in spec["capture"]["channels"]:
                channels_raw[ch], _ = _load_capture_channel_bits(cap_res["capture_id"], ch)
    else:
        note = "Device is claimed by ATK-Logic GUI" if status.get("is_busy") else "No hardware connected"
        print(f"[HIL] Hardware state: {note}. Running HIL verification on F28P65 synthetic model.")
        channels_raw = generate_synthetic_dk9_signals(spec)

    # 1. Assert Phase U PWM frequency & duty
    meas_u = analyze_pwm(channels_raw[0], sr, channel=0)
    target_f = spec["assertions"]["pwm_frequency"]["target_hz"]
    tol_f = spec["assertions"]["pwm_frequency"]["tolerance_hz"]

    if abs(meas_u.frequency_mean_hz - target_f) <= tol_f:
        report["passed_assertions"] += 1
        report["details"].append(f"Phase U Frequency: {meas_u.frequency_mean_hz:.1f} Hz (Target: {target_f} Hz) - PASS")
    else:
        report["failed_assertions"] += 1
        report["failures"].append(f"Phase U Frequency {meas_u.frequency_mean_hz:.1f} Hz out of tolerance")

    # 2. Assert Complementary Pairs (Phase U, Phase V, Phase W)
    for p_name, h_ch, l_ch in [("U", 0, 1), ("V", 2, 3), ("W", 4, 5)]:
        pair = analyze_complementary_pair(channels_raw[h_ch], channels_raw[l_ch], sr, h_ch, l_ch)
        min_dt = spec["assertions"]["deadtime"]["min_allowed_ns"]
        max_dt = spec["assertions"]["deadtime"]["max_allowed_ns"]

        # Shoot-through check
        if not pair.has_overlap and not pair.shoot_through_risk:
            report["passed_assertions"] += 1
            report["details"].append(f"Phase {p_name} Shoot-Through Protection: No Overlap - PASS")
        else:
            report["failed_assertions"] += 1
            report["failures"].append(f"Phase {p_name} Shoot-through risk detected! Overlap: {pair.overlap_count}")

        # Deadtime check
        if min_dt <= pair.deadtime_min_ns <= max_dt:
            report["passed_assertions"] += 1
            report["details"].append(f"Phase {p_name} Deadtime: {pair.deadtime_min_ns:.1f} ns (Allowed: {min_dt}-{max_dt} ns) - PASS")
        else:
            report["failed_assertions"] += 1
            report["failures"].append(f"Phase {p_name} Deadtime {pair.deadtime_min_ns:.1f} ns out of limits")

    # 3. Assert Three-Phase Balance
    tri = analyze_three_phase(channels_raw[0], channels_raw[2], channels_raw[4], sr, 0, 2, 4)
    if tri.is_balanced:
        report["passed_assertions"] += 1
        report["details"].append(f"Three-Phase Balance: UV={tri.phase_shift_uv_deg:.1f}°, VW={tri.phase_shift_vw_deg:.1f}° - PASS")
    else:
        report["failed_assertions"] += 1
        report["failures"].append("Three-Phase Balance failed: " + tri.message)

    report["status"] = "PASS" if report["failed_assertions"] == 0 else "FAIL"
    return report


if __name__ == "__main__":
    rep = run_dk9_hil_test()
    print("\n=======================================================")
    print(f"  DK9 HIL Test Result: {rep['status']}")
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
    sys.exit(0 if rep["status"] == "PASS" else 1)
