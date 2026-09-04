import pytest
import numpy as np
from analysis.complementary import analyze_complementary_pair


def test_synthetic_complementary_clean():
    sample_rate = 10_000_000.0
    period = 200 # 50 kHz
    n_cycles = 50
    total = period * n_cycles

    # Deadtime: 20 samples = 2000 ns
    # High ON: 15..95 (80 samples)
    # Low ON: 115..195 (80 samples)
    h_bits = np.zeros(total, dtype=np.uint8)
    l_bits = np.zeros(total, dtype=np.uint8)

    for c in range(n_cycles):
        base = c * period
        h_bits[base + 15 : base + 95] = 1
        l_bits[base + 115 : base + 195] = 1

    h_bytes = np.packbits(h_bits, bitorder='little').tobytes()
    l_bytes = np.packbits(l_bits, bitorder='little').tobytes()

    res = analyze_complementary_pair(h_bytes, l_bytes, sample_rate)
    assert res.valid is True
    assert res.has_overlap is False
    assert res.shoot_through_risk is False
    assert abs(res.deadtime_min_ns - 2000.0) < 50.0
    print("\n[SYNTHETIC_PASS] Clean complementary pair verified")


def test_synthetic_shoot_through_detection():
    sample_rate = 10_000_000.0
    period = 200
    n_cycles = 10
    total = period * n_cycles

    # Overlap injected: High ON 10..100, Low ON 90..190 (overlap between 90 and 100)
    h_bits = np.zeros(total, dtype=np.uint8)
    l_bits = np.zeros(total, dtype=np.uint8)

    for c in range(n_cycles):
        base = c * period
        h_bits[base + 10 : base + 100] = 1
        l_bits[base + 90 : base + 190] = 1

    h_bytes = np.packbits(h_bits, bitorder='little').tobytes()
    l_bytes = np.packbits(l_bits, bitorder='little').tobytes()

    res = analyze_complementary_pair(h_bytes, l_bytes, sample_rate)
    assert res.has_overlap is True
    assert res.shoot_through_risk is True
    assert res.overlap_count >= 1
    assert res.max_overlap_duration_ns > 0
    print("[SYNTHETIC_PASS] Shoot-through overlap detected as hard risk")
