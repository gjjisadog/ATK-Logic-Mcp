import pytest
from pathlib import Path
from atk_dl16_mcp.server import (
    logic_status,
    logic_capture,
    logic_measure_pwm,
    logic_inspect,
    logic_assert,
    logic_decode,
    logic_measure_pair,
    logic_measure_three_phase
)

GOLDEN_ATKDL = "tests/golden/pwm_10M_30_25.atkdl"


def test_mcp_logic_status():
    status = logic_status()
    assert isinstance(status, dict)
    assert "connected" in status


def test_mcp_logic_measure_pwm_golden():
    res = logic_measure_pwm(capture_id=GOLDEN_ATKDL, channel=0, max_samples=200_000)
    assert res["evidence_source"] == "GOLDEN_FILE"
    assert res["data_integrity"] == "COMPLETE"
    assert res["mode"] == "buffer"
    assert res["sample_rate"] == 200_000_000.0
    assert "measurement" in res
    assert res["measurement"]["valid"] is True
    assert res["valid"] is True
    assert abs(res["frequency_mean_hz"] - 10_000_000.0) < 50_000.0
    assert abs(res["duty_cycle_mean"] - 0.30) < 0.01


def test_mcp_logic_inspect_golden():
    res = logic_inspect(capture_id=GOLDEN_ATKDL, channel=0, start_sample=0, max_edges=20)
    assert res["evidence_source"] == "GOLDEN_FILE"
    assert res["data_integrity"] == "COMPLETE"
    assert res["channel"] == 0
    assert res["returned_edges_count"] == 20
    assert len(res["edges"]) == 20


def test_mcp_logic_capture_json_contract():
    # Call logic_capture: will invoke CLI with --json and return structured JSON
    res = logic_capture(channels=[0, 1], duration_ms=10)
    assert isinstance(res, dict)
    assert "success" in res
    assert "evidence_source" in res
    assert "data_integrity" in res
    assert "capture_complete_received" in res
    assert "warnings" in res


def test_mcp_logic_assert_pass():
    res = logic_assert(
        capture_id=GOLDEN_ATKDL,
        channel=0,
        freq_min_hz=9_900_000.0,
        freq_max_hz=10_100_000.0,
        duty_min=0.28,
        duty_max=0.32
    )
    assert res["passed"] is True
    assert res["evidence_source"] == "GOLDEN_FILE"
    assert res["data_integrity"] == "COMPLETE"
    assert len(res["failures"]) == 0


def test_mcp_logic_assert_fail():
    res = logic_assert(
        capture_id=GOLDEN_ATKDL,
        channel=0,
        freq_min_hz=15_000_000.0, # Will fail (10 MHz != 15 MHz)
        duty_min=0.45             # Will fail (30% != 45%)
    )
    assert res["passed"] is False
    assert res["evidence_source"] == "GOLDEN_FILE"
    assert res["data_integrity"] == "COMPLETE"
    assert len(res["failures"]) >= 1
