import pytest
import numpy as np
from analysis.three_phase import analyze_three_phase


def test_synthetic_three_phase_basic():
    # 10 kHz PWM @ 10 MHz sample rate (1000 samples per period)
    sample_rate = 10_000_000.0
    period = 1000
    n_cycles = 20
    total = period * n_cycles

    u = np.zeros(total, dtype=np.uint8)
    v = np.zeros(total, dtype=np.uint8)
    w = np.zeros(total, dtype=np.uint8)

    for c in range(n_cycles):
        base = c * period
        u[(base + 0) % total : (base + 500) % total] = 1
        v[(base + 333) % total : (base + 833) % total] = 1
        w[(base + 667) % total : (base + 1167) % total] = 1

    u_b = np.packbits(u, bitorder='little').tobytes()
    v_b = np.packbits(v, bitorder='little').tobytes()
    w_b = np.packbits(w, bitorder='little').tobytes()

    res = analyze_three_phase(u_b, v_b, w_b, sample_rate)
    assert res.valid is True
    print("\n[SYNTHETIC_PASS] Three-phase analysis ran successfully")
