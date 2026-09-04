import pytest
import zipfile
import numpy as np
from pathlib import Path
from analysis.edge import bit_to_samples, extract_edges
from analysis.pwm import analyze_pwm
from analysis.complementary import analyze_complementary_pair
from analysis.three_phase import analyze_three_phase
from analysis.decoders import decode_uart, decode_i2c, decode_spi


def test_golden_pwm_atkdl():
    """
    Golden Comparison test against official ATK-Logic capture:
    runtime/test/pwm_10M_30_25.atkdl
    Expected: 10 MHz PWM, 30% duty cycle, 200 MHz sample rate.
    """
    atkdl_path = Path("tests/golden/pwm_10M_30_25.atkdl")
    if not atkdl_path.exists():
        atkdl_path = Path("upstream/atk-logic/runtime/test/pwm_10M_30_25.atkdl")
    assert atkdl_path.exists(), f"Golden capture file missing at {atkdl_path}"

    with zipfile.ZipFile(atkdl_path, 'r') as z:
        # Read channel 0 binary samples
        raw_data = z.read("0/0-0.bin")

    # 1,048,576 bytes = 8,388,608 samples @ 200 MHz = 41.94 ms
    assert len(raw_data) == 1_048_576

    # Analyze first 200,000 samples (1.0 ms authentic capture window)
    meas = analyze_pwm(raw_data, sample_rate=200_000_000, channel=0, max_samples=200_000)

    assert meas.valid is True
    assert meas.cycle_count >= 9900

    # Golden frequency: ~10.0 MHz (real physical signal generator crystal clock ~10.025 MHz)
    assert abs(meas.frequency_mean_hz - 10_000_000.0) < 50_000.0, f"Expected ~10 MHz, got {meas.frequency_mean_hz}"

    # Golden duty cycle: 30.0% (allow < 0.1% delta)
    assert abs(meas.duty_cycle_mean - 0.30) < 0.005, f"Expected 30% duty, got {meas.duty_cycle_mean * 100}%"

    # Verify initial level and transition timestamps
    init_lvl, edges, levels = extract_edges(raw_data, sample_rate=200_000_000, max_samples=200)
    assert len(edges) >= 10
    # Period is 20 samples (6 high, 14 low)
    period = edges[2] - edges[0]
    assert period == 20, f"Expected period 20 samples, got {period}"


def test_complementary_analysis():
    # Construct 100 kHz complementary PWM @ 10 MHz sample rate (100 samples per period)
    # Target: 50% duty, deadtime = 10 samples (1 us = 1000 ns)
    # High-side ON: samples 10..45 (35 samples)
    # Low-side ON:  samples 55..95 (40 samples)
    sample_rate = 10_000_000.0
    period = 100
    n_cycles = 50
    total_samples = period * n_cycles

    h_bits = np.zeros(total_samples, dtype=np.uint8)
    l_bits = np.zeros(total_samples, dtype=np.uint8)

    for c in range(n_cycles):
        base = c * period
        h_bits[base + 10 : base + 45] = 1
        l_bits[base + 55 : base + 95] = 1

    h_bytes = np.packbits(h_bits, bitorder='little').tobytes()
    l_bytes = np.packbits(l_bits, bitorder='little').tobytes()

    res = analyze_complementary_pair(h_bytes, l_bytes, sample_rate)
    assert res.valid is True
    assert res.has_overlap is False
    assert res.shoot_through_risk is False
    assert abs(res.deadtime_min_ns - 1000.0) < 100.0 # 10 samples @ 10 MHz = 1000 ns


def test_shoot_through_overlap():
    sample_rate = 10_000_000.0
    total_samples = 200
    h_bits = np.zeros(total_samples, dtype=np.uint8)
    l_bits = np.zeros(total_samples, dtype=np.uint8)

    # Overlap between samples 50 and 60
    h_bits[20:60] = 1
    l_bits[50:90] = 1

    h_bytes = np.packbits(h_bits, bitorder='little').tobytes()
    l_bytes = np.packbits(l_bits, bitorder='little').tobytes()

    res = analyze_complementary_pair(h_bytes, l_bytes, sample_rate)
    assert res.has_overlap is True
    assert res.shoot_through_risk is True
    assert res.overlap_count >= 1
    assert res.max_overlap_duration_ns > 0


def test_three_phase_analysis():
    # 3-phase 10 kHz @ 10 MHz sample rate (1000 samples per period), synchronized carriers
    sample_rate = 10_000_000.0
    period = 1000
    n_cycles = 20
    total = period * n_cycles

    u = np.zeros(total, dtype=np.uint8)
    v = np.zeros(total, dtype=np.uint8)
    w = np.zeros(total, dtype=np.uint8)

    for c in range(n_cycles):
        base = c * period
        u[base : base + 500] = 1
        v[base : base + 500] = 1
        w[base : base + 500] = 1

    u_b = np.packbits(u, bitorder='little').tobytes()
    v_b = np.packbits(v, bitorder='little').tobytes()
    w_b = np.packbits(w, bitorder='little').tobytes()

    res = analyze_three_phase(u_b, v_b, w_b, sample_rate)
    assert res.valid is True
    assert res.carrier.carrier_is_synchronized is True
    assert res.modulation.is_balanced is True


def test_uart_decoder():
    # Encode ASCII "OK\n" at 115200 baud, 1 MHz sample rate
    sample_rate = 1_000_000.0
    baud = 115200
    bit_len = int(round(sample_rate / baud)) # ~9 samples per bit

    # Line idle is High
    bits = [1] * 20

    def send_byte(val):
        nonlocal bits
        # Start bit (0)
        bits += [0] * bit_len
        # 8 data bits (LSB first)
        for b in range(8):
            bit = (val >> b) & 1
            bits += [bit] * bit_len
        # Stop bit (1)
        bits += [1] * bit_len

    send_byte(ord('A'))
    send_byte(ord('B'))
    bits += [1] * 30

    raw = np.packbits(np.array(bits, dtype=np.uint8), bitorder='little').tobytes()
    decoded = decode_uart(raw, sample_rate, baud_rate=baud)

    assert len(decoded) == 2
    assert decoded[0]["char"] == 'A'
    assert decoded[1]["char"] == 'B'
    assert decoded[0]["framing_error"] is False


def test_i2c_decoder():
    sample_rate = 1_000_000.0 # 1 us per sample
    scl = []
    sda = []

    def idle(n=10):
        scl.extend([1] * n)
        sda.extend([1] * n)

    def start():
        scl.extend([1] * 5)
        sda.extend([1] * 2 + [0] * 3)
        scl.extend([0] * 5)
        sda.extend([0] * 5)

    def stop():
        scl.extend([0] * 5)
        sda.extend([0] * 5)
        scl.extend([1] * 5)
        sda.extend([0] * 2 + [1] * 3)

    def send_bit(b):
        # SCL Low
        scl.extend([0] * 5)
        sda.extend([b] * 5)
        # SCL High (sample)
        scl.extend([1] * 5)
        sda.extend([b] * 5)

    def send_byte(val, ack=True):
        for i in range(7, -1, -1):
            bit = (val >> i) & 1
            send_bit(bit)
        send_bit(0 if ack else 1) # ACK bit

    idle()
    start()
    send_byte(0xA0) # Address 0x50, Write (0)
    send_byte(0x55) # Data byte 0x55
    stop()
    idle()

    scl_bytes = np.packbits(np.array(scl, dtype=np.uint8), bitorder='little').tobytes()
    sda_bytes = np.packbits(np.array(sda, dtype=np.uint8), bitorder='little').tobytes()

    events = decode_i2c(scl_bytes, sda_bytes, sample_rate)
    types = [e["type"] for e in events]
    assert "START" in types
    assert "ADDRESS" in types
    assert "DATA" in types
    assert "STOP" in types

    addr_ev = next(e for e in events if e["type"] == "ADDRESS")
    assert addr_ev["address"] == 0x50
    assert addr_ev["is_read"] is False
    assert addr_ev["ack"] is True

    data_ev = next(e for e in events if e["type"] == "DATA")
    assert data_ev["data"] == 0x55
    assert data_ev["ack"] is True


def test_spi_decoder():
    sample_rate = 1_000_000.0
    clk = []
    mosi = []
    miso = []
    cs = []

    def idle(n=10):
        clk.extend([0] * n)
        mosi.extend([0] * n)
        miso.extend([0] * n)
        cs.extend([1] * n)

    idle()

    # CS falls
    cs.extend([0] * 5)
    clk.extend([0] * 5)
    mosi.extend([0] * 5)
    miso.extend([0] * 5)

    # Transfer byte 0xD2 (11010010) on MOSI, 0x4B (01001011) on MISO
    mosi_val = 0xD2
    miso_val = 0x4B

    for i in range(7, -1, -1):
        m_bit = (mosi_val >> i) & 1
        s_bit = (miso_val >> i) & 1
        # CPOL=0, CPHA=0: sample on rising edge
        # Clock low, setup data
        clk.extend([0] * 5)
        mosi.extend([m_bit] * 5)
        miso.extend([s_bit] * 5)
        cs.extend([0] * 5)
        # Clock high, sample data
        clk.extend([1] * 5)
        mosi.extend([m_bit] * 5)
        miso.extend([s_bit] * 5)
        cs.extend([0] * 5)

    # Return clk to 0, CS rises
    clk.extend([0] * 5)
    mosi.extend([0] * 5)
    miso.extend([0] * 5)
    cs.extend([1] * 5)
    idle()

    clk_b = np.packbits(np.array(clk, dtype=np.uint8), bitorder='little').tobytes()
    mosi_b = np.packbits(np.array(mosi, dtype=np.uint8), bitorder='little').tobytes()
    miso_b = np.packbits(np.array(miso, dtype=np.uint8), bitorder='little').tobytes()
    cs_b = np.packbits(np.array(cs, dtype=np.uint8), bitorder='little').tobytes()

    transfers = decode_spi(clk_b, mosi_b, miso_b, cs_b, sample_rate, cpol=0, cpha=0)
    assert len(transfers) == 1
    assert transfers[0]["mosi"] == 0xD2
    assert transfers[0]["miso"] == 0x4B
