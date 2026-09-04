import pytest
import zipfile
import json
from pathlib import Path
from analysis.pwm import analyze_pwm
from analysis.edge import extract_edges

GOLDEN_DIR = Path(__file__).resolve().parent
GOLDEN_ATKDL = GOLDEN_DIR / "pwm_10M_30_25.atkdl"
GOLDEN_META = GOLDEN_DIR / "pwm_10M_30_25_meta.json"


def test_golden_capture_in_repo():
    """
    Self-contained golden comparison test against official ATK-Logic capture
    archived directly inside the repository at tests/golden/pwm_10M_30_25.atkdl.
    """
    assert GOLDEN_ATKDL.exists(), f"Golden archive missing at {GOLDEN_ATKDL}"
    assert GOLDEN_META.exists(), f"Golden metadata missing at {GOLDEN_META}"

    with open(GOLDEN_META, "r") as f:
        meta = json.load(f)

    with zipfile.ZipFile(GOLDEN_ATKDL, "r") as z:
        # 1. Parse real sample rate from set.ini
        set_ini_str = z.read("set.ini").decode("utf-8", errors="ignore")
        assert "setHz" in set_ini_str
        sr = 0
        for line in set_ini_str.split("\n"):
            if "settingData=" in line:
                s_json = line.split("settingData=")[1].strip()
                parsed = json.loads(s_json)
                sr = parsed.get("setHz", 0)
        assert sr == 200_000_000, f"Expected 200 MHz in set.ini, got {sr}"

        # 2. Read Channel 0 raw binary samples
        raw_bytes = z.read("0/0-0.bin")

    assert len(raw_bytes) == meta["raw_byte_length"]

    # 3. Analyze PWM on authentic 1 ms capture window (200,000 samples @ 200 MHz)
    meas = analyze_pwm(raw_bytes, sample_rate=sr, channel=0, max_samples=200_000)

    # 4. Golden assertions
    assert meas.valid is True
    assert meas.cycle_count >= 9900
    assert abs(meas.frequency_mean_hz - 10_000_000.0) < 50_000.0 # Physical crystal oscillator center
    assert abs(meas.duty_cycle_mean - 0.30) < 0.005 # 30% nominal duty
    assert meas.missing_pulse_count == 0
    assert meas.extra_edge_count == 0

    # 5. Check exact edge intervals (period = 20 samples = 100 ns)
    init_lvl, edges, levels = extract_edges(raw_bytes, sr, max_samples=200)
    assert len(edges) >= 10
    period_samples = edges[2] - edges[0]
    assert period_samples == meta["expected_metrics"]["period_samples"]
    print("\n[GOLDEN_PASS] Golden capture verified against in-repo official fixture")
