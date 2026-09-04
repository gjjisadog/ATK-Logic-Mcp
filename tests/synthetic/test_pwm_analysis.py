import pytest
import numpy as np
from analysis.pwm import analyze_pwm
from analysis.edge import extract_edges


def test_synthetic_pwm_normal():
    # 20 kHz PWM @ 10 MHz sample rate (500 samples/period)
    # 40% duty cycle: 200 samples HIGH, 300 samples LOW
    sample_rate = 10_000_000.0
    period = 500
    n_cycles = 100
    total = period * n_cycles

    bits = np.zeros(total, dtype=np.uint8)
    for c in range(n_cycles):
        base = c * period
        bits[base : base + 200] = 1

    raw = np.packbits(bits, bitorder='little').tobytes()
    res = analyze_pwm(raw, sample_rate, channel=0)

    assert res.valid is True
    assert abs(res.frequency_mean_hz - 20000.0) < 5.0
    assert abs(res.duty_cycle_mean - 0.40) < 0.001
    assert res.cycle_count >= 95
    print("\n[SYNTHETIC_PASS] Normal PWM analyzed successfully")


def test_synthetic_pwm_static_levels():
    sample_rate = 10_000_000.0
    total = 2000

    # Static Low
    low_raw = np.packbits(np.zeros(total, dtype=np.uint8), bitorder='little').tobytes()
    res_low = analyze_pwm(low_raw, sample_rate, channel=0)
    assert res_low.valid is False
    assert res_low.cycle_count == 0

    # Static High
    high_raw = np.packbits(np.ones(total, dtype=np.uint8), bitorder='little').tobytes()
    res_high = analyze_pwm(high_raw, sample_rate, channel=0)
    assert res_high.valid is False
    assert res_high.cycle_count == 0
    print("[SYNTHETIC_PASS] Static levels rejected as invalid PWM")


def test_synthetic_pwm_missing_pulse():
    sample_rate = 10_000_000.0
    period = 500
    n_cycles = 50
    total = period * n_cycles

    bits = np.zeros(total, dtype=np.uint8)
    for c in range(n_cycles):
        if c == 20:
            # Drop the pulse entirely (stay at 0)
            continue
        base = c * period
        bits[base : base + 200] = 1

    raw = np.packbits(bits, bitorder='little').tobytes()
    res = analyze_pwm(raw, sample_rate, channel=0)

    # Missing pulse must be detected and result marked INVALID
    assert res.missing_pulse_count >= 1
    assert res.valid is False
    print("\n[SYNTHETIC_PASS] Missing pulse detected and marked invalid as required")


def test_synthetic_pwm_extra_edge():
    sample_rate = 10_000_000.0
    period = 500
    n_cycles = 30
    total = period * n_cycles

    bits = np.zeros(total, dtype=np.uint8)
    for c in range(n_cycles):
        base = c * period
        bits[base : base + 200] = 1
        if c == 15:
            # Inject a notch/extra edge inside the high pulse
            bits[base + 80 : base + 100] = 0

    raw = np.packbits(bits, bitorder='little').tobytes()
    res = analyze_pwm(raw, sample_rate, channel=0)

    # Extra edges must be counted
    assert res.extra_edge_count >= 1
    print("[SYNTHETIC_PASS] Extra edges classified and counted")


def test_synthetic_pwm_resolution_clamping():
    sample_rate = 20_000_000.0 # 50 ns resolution
    period = 1000
    total = period * 20

    bits = np.zeros(total, dtype=np.uint8)
    for c in range(20):
        base = c * period
        bits[base : base + 500] = 1

    raw = np.packbits(bits, bitorder='little').tobytes()
    # Request 10 ns glitch threshold on a 50 ns resolution capture
    res = analyze_pwm(raw, sample_rate, channel=0, glitch_threshold_ns=10.0)

    assert res.measurement_resolution_ns == 50.0
    assert any("below sampling resolution" in w for w in res.warnings)
    print("[SYNTHETIC_PASS] Glitch resolution clamping and warning verified")

