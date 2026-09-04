import pytest
import numpy as np
from analysis.three_phase import analyze_three_phase


def test_synthetic_three_phase_sinusoidal_modulation():
    """
    Simulate authentic 3-phase inverter synchronous ePWM (e.g. TI TMS320F28P65):
    - Carrier: Synchronized 16 kHz (switching period = 62.5 us)
    - Sample rate: 10 MHz (625 samples per switching cycle)
    - Modulation: 50 Hz fundamental sine wave with 120-degree phase displacement:
        U(t) = 0.5 + 0.35 * sin(2*pi*50*t)
        V(t) = 0.5 + 0.35 * sin(2*pi*50*t - 2*pi/3)
        W(t) = 0.5 + 0.35 * sin(2*pi*50*t + 2*pi/3)
    Duration: 40 ms (2 full 50 Hz cycles, 640 switching cycles = 400,000 samples)
    """
    sample_rate = 10_000_000.0
    f_sw = 16_000.0
    f_mod = 50.0
    period_samples = int(round(sample_rate / f_sw)) # 625 samples
    n_cycles = 640
    total = period_samples * n_cycles

    u_bits = np.zeros(total, dtype=np.uint8)
    v_bits = np.zeros(total, dtype=np.uint8)
    w_bits = np.zeros(total, dtype=np.uint8)

    t_span = np.arange(n_cycles) / f_sw

    # Sinusoidal duty cycle references (centered around 0.5)
    du = 0.50 + 0.35 * np.sin(2.0 * np.pi * f_mod * t_span)
    dv = 0.50 + 0.35 * np.sin(2.0 * np.pi * f_mod * t_span - 2.0 * np.pi / 3.0)
    dw = 0.50 + 0.35 * np.sin(2.0 * np.pi * f_mod * t_span + 2.0 * np.pi / 3.0)

    # Generate edge positions within each synchronous carrier period
    for k in range(n_cycles):
        base = k * period_samples
        # Synchronous carriers: all periods start at 'base'
        u_high_samples = int(round(du[k] * period_samples))
        v_high_samples = int(round(dv[k] * period_samples))
        w_high_samples = int(round(dw[k] * period_samples))

        u_bits[base : base + u_high_samples] = 1
        v_bits[base : base + v_high_samples] = 1
        w_bits[base : base + w_high_samples] = 1

    u_b = np.packbits(u_bits, bitorder='little').tobytes()
    v_b = np.packbits(v_bits, bitorder='little').tobytes()
    w_b = np.packbits(w_bits, bitorder='little').tobytes()

    res = analyze_three_phase(u_b, v_b, w_b, sample_rate)

    # 1. Carrier assertions: carriers MUST be synchronous (0 offset)
    assert res.carrier.carrier_is_synchronized is True
    assert abs(res.carrier.carrier_frequency_mean_hz - 16000.0) < 50.0
    assert res.carrier.carrier_frequency_diff_max_hz < 10.0

    # 2. Modulation assertions: fundamental envelope must be ~50 Hz, balanced 120 deg
    assert res.modulation.is_constant_duty is False
    assert abs(res.modulation.fundamental_frequency_hz - 50.0) < 3.0
    assert res.modulation.phase_balance_error_deg < 10.0
    assert res.modulation.is_balanced is True
    assert res.valid is True
    print("\n[SYNTHETIC_PASS] Three-phase synchronized carrier + 120 deg modulation envelope verified")


def test_synthetic_three_phase_constant_duty_diagnostic():
    """
    Diagnostic mode: Inverter configured with constant 50% open-loop duty on all 3 phases.
    """
    sample_rate = 10_000_000.0
    f_sw = 16_000.0
    period_samples = int(round(sample_rate / f_sw))
    n_cycles = 50
    total = period_samples * n_cycles

    u_bits = np.zeros(total, dtype=np.uint8)
    v_bits = np.zeros(total, dtype=np.uint8)
    w_bits = np.zeros(total, dtype=np.uint8)

    high_samples = period_samples // 2

    for k in range(n_cycles):
        base = k * period_samples
        u_bits[base : base + high_samples] = 1
        v_bits[base : base + high_samples] = 1
        w_bits[base : base + high_samples] = 1

    u_b = np.packbits(u_bits, bitorder='little').tobytes()
    v_b = np.packbits(v_bits, bitorder='little').tobytes()
    w_b = np.packbits(w_bits, bitorder='little').tobytes()

    res = analyze_three_phase(u_b, v_b, w_b, sample_rate)

    assert res.carrier.carrier_is_synchronized is True
    assert res.modulation.is_constant_duty is True
    assert res.modulation.is_balanced is True
    assert res.valid is True
    print("[SYNTHETIC_PASS] Three-phase constant duty diagnostic mode verified")
