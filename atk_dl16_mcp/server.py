import os
import sys
import json
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional

# Add project root to sys.path so analysis module is found
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from mcp.server.mcpserver import MCPServer
from analysis.edge import bit_to_samples, extract_edges
from analysis.pwm import analyze_pwm
from analysis.complementary import analyze_complementary_pair
from analysis.three_phase import analyze_three_phase
from analysis.decoders import decode_uart, decode_i2c, decode_spi

server = MCPServer("atk-dl16-mcp")

CLI_PATH = ROOT_DIR / "build" / "Release" / "atk-dl16.exe"


def _find_cli() -> Optional[Path]:
    if CLI_PATH.exists():
        return CLI_PATH
    cand = ROOT_DIR / "build" / "atk-dl16.exe"
    if cand.exists():
        return cand
    cand2 = ROOT_DIR / "bin" / "atk-dl16.exe"
    if cand2.exists():
        return cand2
    return None


def _is_certified_golden(cap_path: Path) -> bool:
    """Verify that file is an authenticated golden reference fixture with matching manifest."""
    try:
        resolved = cap_path.resolve()
        golden_dir = (ROOT_DIR / "tests" / "golden").resolve()
        if golden_dir in resolved.parents or resolved.parent == golden_dir:
            meta_json = resolved.parent / f"{resolved.stem}_meta.json"
            if meta_json.exists():
                with open(meta_json, "r") as f:
                    manifest = json.load(f)
                    if manifest.get("archive_filename") == resolved.name:
                        return True
    except Exception:
        pass
    return False


def _load_capture_channel_bits(capture_id: str, channel: int) -> tuple[bytes, dict]:
    """Helper to load channel raw bits and meta from capture directory or .atkdl archive."""
    cap_path = Path(capture_id)
    if not cap_path.exists():
        if (ROOT_DIR / capture_id).exists():
            cap_path = ROOT_DIR / capture_id
        elif (ROOT_DIR / "captures" / capture_id).exists():
            cap_path = ROOT_DIR / "captures" / capture_id

    # Case 1: .atkdl archive
    if cap_path.suffix.lower() == ".atkdl":
        import zipfile
        with zipfile.ZipFile(cap_path, 'r') as z:
            ch_file = f"{channel}/{channel}-0.bin"
            if ch_file not in z.namelist():
                candidates = [f for f in z.namelist() if f.startswith(f"{channel}/") and f.endswith(".bin")]
                if not candidates:
                    raise FileNotFoundError(f"Channel {channel} not found in {cap_path}")
                ch_file = candidates[0]
            raw_bytes = z.read(ch_file)

            # Parse authentic sample rate and setTime from set.ini
            sr = None
            set_time_ms = None
            if "set.ini" in z.namelist():
                set_ini_str = z.read("set.ini").decode("utf-8", errors="ignore")
                for line in set_ini_str.split("\n"):
                    if "settingData=" in line:
                        try:
                            s_json = line.split("settingData=")[1].strip()
                            parsed = json.loads(s_json)
                            sr = parsed.get("setHz")
                            set_time_ms = parsed.get("setTime")
                        except Exception:
                            pass
            if sr is None or float(sr) <= 0:
                raise ValueError(f"ANALYSIS_INVALID: cannot determine valid sample rate from {cap_path.name}: missing setHz in set.ini")

            sr = float(sr)
            is_golden = _is_certified_golden(cap_path)

            if set_time_ms and set_time_ms > 0:
                expected_samples = int(sr * (set_time_ms / 1000.0))
                expected_bytes = (expected_samples + 7) // 8
                duration_ms = int(set_time_ms)
                if len(raw_bytes) < expected_bytes:
                    data_integrity = "INCOMPLETE"
                elif len(raw_bytes) == expected_bytes:
                    data_integrity = "COMPLETE"
                else:  # len(raw_bytes) > expected_bytes
                    if is_golden:
                        raw_bytes = raw_bytes[:expected_bytes]
                        data_integrity = "COMPLETE"
                    else:
                        data_integrity = "UNKNOWN"
            else:
                duration_ms = int((len(raw_bytes) * 8 / sr) * 1000)
                data_integrity = "UNKNOWN"

            meta = {
                "sample_rate": sr,
                "duration_ms": duration_ms,
                "mode": "buffer",
                "data_integrity": data_integrity,
                "evidence_source": "GOLDEN_FILE" if is_golden else "SAVED_CAPTURE"
            }
            return raw_bytes, meta

    # Case 2: standard capture directory
    meta_file = cap_path / "meta.json"
    if not meta_file.exists():
        raise FileNotFoundError(f"Capture directory {cap_path} not found or missing meta.json")

    with open(meta_file, "r") as f:
        meta = json.load(f)

    # Delete fail-open defaults (Section 10)
    data_integrity = meta.get("data_integrity", "UNKNOWN")
    evidence_source = meta.get("evidence_source", "UNKNOWN")

    # Sample rate must be valid (Section 11)
    sr = meta.get("sample_rate")
    if sr is None or float(sr) <= 0:
        raise ValueError(f"ANALYSIS_INVALID: capture {capture_id} missing or invalid sample_rate")
    sample_rate = float(sr)

    ch_file = cap_path / f"ch{channel:02d}.bits"
    if not ch_file.exists():
        raise FileNotFoundError(f"Channel {channel} data file {ch_file.name} not found")

    with open(ch_file, "rb") as f:
        raw_bytes = f.read()

    # Section 12: Check actual file bits against metadata sample counts
    actual_file_bits = len(raw_bytes) * 8
    channel_counts = meta.get("sample_counts", {})
    expected_ch_samples = channel_counts.get(str(channel))
    if expected_ch_samples is not None and actual_file_bits < expected_ch_samples:
        data_integrity = "ARTIFACT_INCOMPLETE"
    elif meta.get("actual_samples") is not None and actual_file_bits < meta["actual_samples"]:
        data_integrity = "ARTIFACT_INCOMPLETE"
    elif meta.get("requested_samples") is not None and actual_file_bits < meta["requested_samples"]:
        data_integrity = "ARTIFACT_INCOMPLETE"

    meta["data_integrity"] = data_integrity
    meta["evidence_source"] = evidence_source
    meta["sample_rate"] = sample_rate

    return raw_bytes, meta


@server.tool()
def logic_status() -> Dict[str, Any]:
    """
    Query the status and capabilities of the connected ATK-DL16 / DL16 Plus logic analyzer.
    Returns model name, hardware version, MCU/FPGA firmware, link speed, and lock state.
    """
    cli = _find_cli()
    if not cli:
        return {"connected": False, "error": "CLI executable atk-dl16.exe not built yet"}

    # Run list
    p_list = subprocess.run([str(cli), "list"], capture_output=True, text=True)
    if p_list.returncode != 0 or "0 found" in p_list.stdout:
        return {
            "connected": False,
            "device_count": 0,
            "message": "No ATK-DL16 device detected on USB bus. Ensure device is plugged in."
        }

    # Run info
    p_info = subprocess.run([str(cli), "info"], capture_output=True, text=True)
    if p_info.returncode != 0:
        err_msg = p_info.stderr.strip() or p_info.stdout.strip()
        is_busy = "in use" in err_msg or "claimed" in err_msg or "DeviceBusy" in err_msg
        return {
            "connected": True,
            "ready": False,
            "is_busy": is_busy,
            "lock_owner": "ATK-Logic GUI or another process" if is_busy else "None",
            "message": err_msg
        }

    # Parse stdout of info
    lines = p_info.stdout.strip().split("\n")
    info_dict = {"connected": True, "ready": True, "is_busy": False}
    for line in lines:
        if ":" in line:
            k, v = line.split(":", 1)
            info_dict[k.strip().lower().replace(" ", "_")] = v.strip()

    return info_dict


@server.tool()
def logic_capture(
    channels: List[int] = [0, 1],
    sample_rate_hz: int = 20_000_000,
    duration_ms: int = 20,
    threshold_voltage: float = 1.6,
    trigger: str = "immediate",
    mode: str = "buffer"
) -> Dict[str, Any]:
    """
    Perform a logic capture on specified channels and save waveforms to disk.
    Propagates complete machine-readable evidence contract via CLI --json flag.
    """
    cli = _find_cli()
    if not cli:
        return {
            "success": False,
            "error_code": "CLI_NOT_FOUND",
            "message": "CLI executable atk-dl16.exe not found",
            "evidence_source": "REAL_HARDWARE",
            "data_integrity": "UNKNOWN",
            "capture_complete_received": False,
            "warnings": []
        }

    ch_str = ",".join(str(c) for c in channels)
    out_dir = str(ROOT_DIR / "captures")

    cmd = [
        str(cli), "capture",
        "--channels", ch_str,
        "--sample-rate", str(sample_rate_hz),
        "--duration", f"{duration_ms}ms",
        "--threshold", str(threshold_voltage),
        "--trigger", trigger,
        "--mode", mode,
        "--out", out_dir,
        "--json"
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    try:
        data = json.loads(proc.stdout)
        return data
    except Exception as ex:
        return {
            "success": False,
            "error_code": "CLI_JSON_PARSE_ERROR",
            "message": f"Failed to parse JSON from CLI stdout: {ex}",
            "evidence_source": "REAL_HARDWARE",
            "data_integrity": "UNKNOWN",
            "capture_complete_received": False,
            "raw_stdout": proc.stdout,
            "warnings": [proc.stderr.strip()] if proc.stderr.strip() else []
        }


@server.tool()
def logic_measure_pwm(
    capture_id: str,
    channel: int = 0,
    glitch_threshold_ns: float = 10.0,
    max_samples: Optional[int] = None
) -> Dict[str, Any]:
    """
    Compute cycle-by-cycle frequency, duty cycle, jitter, and glitch metrics for a PWM channel.
    """
    raw_bytes, meta = _load_capture_channel_bits(capture_id, channel)
    sample_rate = float(meta["sample_rate"])

    meas = analyze_pwm(
        raw_bytes=raw_bytes,
        sample_rate=sample_rate,
        channel=channel,
        max_samples=max_samples,
        glitch_threshold_ns=glitch_threshold_ns
    )
    res = {
        "capture_id": capture_id,
        "evidence_source": meta.get("evidence_source", "UNKNOWN"),
        "data_integrity": meta.get("data_integrity", "UNKNOWN"),
        "mode": meta.get("mode", "buffer"),
        "sample_rate": sample_rate,
        "measurement": meas.to_dict()
    }
    res.update(meas.to_dict())
    return res


@server.tool()
def logic_measure_pair(
    capture_id: str,
    high_channel: int = 0,
    low_channel: int = 1,
    max_samples: Optional[int] = None
) -> Dict[str, Any]:
    """
    Analyze a complementary PWM half-bridge pair (e.g. EPWMxA and EPWMxB).
    Measures deadtime, shoot-through risk, and overlap intervals.
    """
    h_bytes, meta = _load_capture_channel_bits(capture_id, high_channel)
    l_bytes, _ = _load_capture_channel_bits(capture_id, low_channel)
    sample_rate = float(meta["sample_rate"])

    meas = analyze_complementary_pair(
        high_raw_bytes=h_bytes,
        low_raw_bytes=l_bytes,
        sample_rate=sample_rate,
        high_channel=high_channel,
        low_channel=low_channel,
        max_samples=max_samples
    )
    res = {
        "capture_id": capture_id,
        "evidence_source": meta.get("evidence_source", "UNKNOWN"),
        "data_integrity": meta.get("data_integrity", "UNKNOWN"),
        "mode": meta.get("mode", "buffer"),
        "sample_rate": sample_rate,
        "measurement": meas.to_dict()
    }
    res.update(meas.to_dict())
    return res


@server.tool()
def logic_measure_three_phase(
    capture_id: str,
    u_channel: int = 0,
    v_channel: int = 1,
    w_channel: int = 2,
    max_samples: Optional[int] = None
) -> Dict[str, Any]:
    """
    Analyze a 3-phase PWM system (U, V, W legs).
    Measures fundamental modulation frequency, phase shifts, and symmetry.
    """
    u_bytes, meta = _load_capture_channel_bits(capture_id, u_channel)
    v_bytes, _ = _load_capture_channel_bits(capture_id, v_channel)
    w_bytes, _ = _load_capture_channel_bits(capture_id, w_channel)
    sample_rate = float(meta["sample_rate"])

    meas = analyze_three_phase(
        u_raw=u_bytes,
        v_raw=v_bytes,
        w_raw=w_bytes,
        sample_rate=sample_rate,
        u_channel=u_channel,
        v_channel=v_channel,
        w_channel=w_channel,
        max_samples=max_samples
    )
    res = {
        "capture_id": capture_id,
        "evidence_source": meta.get("evidence_source", "UNKNOWN"),
        "data_integrity": meta.get("data_integrity", "UNKNOWN"),
        "mode": meta.get("mode", "buffer"),
        "sample_rate": sample_rate,
        "measurement": meas.to_dict()
    }
    res.update(meas.to_dict())
    return res


@server.tool()
def logic_inspect(
    capture_id: str,
    channel: int = 0,
    start_sample: int = 0,
    max_edges: int = 50
) -> Dict[str, Any]:
    """
    Inspect raw transition edges and signal levels for a channel in a capture.
    """
    raw_bytes, meta = _load_capture_channel_bits(capture_id, channel)
    sample_rate = float(meta["sample_rate"])

    init_lvl, edges, levels = extract_edges(raw_bytes, sample_rate)

    mask = (edges >= start_sample)
    filtered_edges = edges[mask][:max_edges]
    filtered_levels = levels[mask][:max_edges]

    edge_list = []
    for s_idx, lvl in zip(filtered_edges, filtered_levels):
        edge_list.append({
            "sample_index": int(s_idx),
            "timestamp_s": float(s_idx / sample_rate),
            "timestamp_us": float(s_idx / sample_rate * 1e6),
            "level": int(lvl)
        })

    meas = {
        "channel": channel,
        "sample_rate": sample_rate,
        "total_edges": len(edges),
        "initial_level": init_lvl,
        "start_sample": start_sample,
        "returned_edges_count": len(edge_list),
        "edges": edge_list
    }
    res = {
        "capture_id": capture_id,
        "evidence_source": meta.get("evidence_source", "UNKNOWN"),
        "data_integrity": meta.get("data_integrity", "UNKNOWN"),
        "mode": meta.get("mode", "buffer"),
        "sample_rate": sample_rate,
        "measurement": meas
    }
    res.update(meas)
    return res


@server.tool()
def logic_assert(
    capture_id: str,
    channel: int = 0,
    freq_min_hz: Optional[float] = None,
    freq_max_hz: Optional[float] = None,
    duty_min: Optional[float] = None,
    duty_max: Optional[float] = None,
    max_jitter_ns: Optional[float] = None,
    max_glitches: Optional[int] = None,
    no_overlap_with_channel: Optional[int] = None,
    min_deadtime_ns: Optional[float] = None
) -> Dict[str, Any]:
    """
    Automated HIL testing assertion evaluator.
    Validates PWM signals and complementary switching pairs against design tolerances.
    """
    try:
        raw_bytes, meta = _load_capture_channel_bits(capture_id, channel)
    except Exception as ex:
        return {
            "passed": False,
            "reason": f"LOAD_ERROR: {ex}",
            "evidence_source": "UNKNOWN",
            "data_integrity": "UNKNOWN",
            "failures": [str(ex)],
            "pwm_summary": None,
            "pair_summary": None
        }

    sample_rate = meta.get("sample_rate")
    if sample_rate is None or float(sample_rate) <= 0:
        return {
            "passed": False,
            "reason": "ANALYSIS_INVALID: missing or invalid sample_rate",
            "evidence_source": meta.get("evidence_source", "UNKNOWN"),
            "data_integrity": meta.get("data_integrity", "UNKNOWN"),
            "failures": ["Sample rate is missing or invalid"],
            "pwm_summary": None,
            "pair_summary": None
        }
    sample_rate = float(sample_rate)

    integrity = meta.get("data_integrity", "UNKNOWN")
    evidence_source = meta.get("evidence_source", "UNKNOWN")
    mode = meta.get("mode", "buffer")

    # Section 33: Stream mode fail closed in logic_assert
    if mode != "buffer":
        return {
            "passed": False,
            "reason": f"EXPERIMENTAL_CAPTURE_MODE: mode '{mode}' is unverified on hardware",
            "evidence_source": evidence_source,
            "data_integrity": integrity,
            "failures": [f"Capture mode '{mode}' is experimental and rejected for trusted assertions"],
            "pwm_summary": None,
            "pair_summary": None
        }

    # Section 10: Fail closed on capture data integrity
    if integrity not in ("COMPLETE", "PASS"):
        return {
            "passed": False,
            "reason": f"INCOMPLETE_CAPTURE: data integrity is {integrity}",
            "evidence_source": evidence_source,
            "data_integrity": integrity,
            "failures": [f"Capture data is incomplete or corrupted ({integrity})"],
            "pwm_summary": None,
            "pair_summary": None
        }

    # Fail closed on untrusted evidence source
    if evidence_source in ("UNKNOWN", "NONE"):
        return {
            "passed": False,
            "reason": f"UNTRUSTED_EVIDENCE_SOURCE: evidence source is {evidence_source}",
            "evidence_source": evidence_source,
            "data_integrity": integrity,
            "failures": [f"Evidence source {evidence_source} is untrusted"],
            "pwm_summary": None,
            "pair_summary": None
        }

    pwm = analyze_pwm(raw_bytes, sample_rate, channel)
    failures = []

    if not pwm.valid:
        return {
            "passed": False,
            "reason": f"Signal invalid: {pwm.message}",
            "evidence_source": evidence_source,
            "data_integrity": integrity,
            "failures": [f"Signal invalid: {pwm.message}"],
            "pwm_summary": pwm.to_dict(),
            "pair_summary": None
        }

    if freq_min_hz is not None and pwm.frequency_mean_hz < freq_min_hz:
        failures.append(f"Frequency {pwm.frequency_mean_hz:.1f} Hz < min {freq_min_hz:.1f} Hz")
    if freq_max_hz is not None and pwm.frequency_mean_hz > freq_max_hz:
        failures.append(f"Frequency {pwm.frequency_mean_hz:.1f} Hz > max {freq_max_hz:.1f} Hz")

    if duty_min is not None and pwm.duty_cycle_mean < duty_min:
        failures.append(f"Duty cycle {pwm.duty_cycle_mean * 100:.2f}% < min {duty_min * 100:.2f}%")
    if duty_max is not None and pwm.duty_cycle_mean > duty_max:
        failures.append(f"Duty cycle {pwm.duty_cycle_mean * 100:.2f}% > max {duty_max * 100:.2f}%")

    if max_jitter_ns is not None and pwm.jitter_rms_ns > max_jitter_ns:
        failures.append(f"Jitter RMS {pwm.jitter_rms_ns:.2f} ns > max {max_jitter_ns:.2f} ns")

    if max_glitches is not None and pwm.glitch_count > max_glitches:
        failures.append(f"Glitch count {pwm.glitch_count} > max allowed {max_glitches}")

    # Check complementary pair assertions if requested
    pair_result = None
    if no_overlap_with_channel is not None or min_deadtime_ns is not None:
        other_ch = no_overlap_with_channel if no_overlap_with_channel is not None else 1
        other_bytes, _ = _load_capture_channel_bits(capture_id, other_ch)
        pair = analyze_complementary_pair(raw_bytes, other_bytes, sample_rate, channel, other_ch)
        pair_result = pair.to_dict()

        if no_overlap_with_channel is not None and pair.has_overlap:
            failures.append(f"Shoot-through overlap detected with channel {other_ch} ({pair.overlap_count} occurrences)")
        if min_deadtime_ns is not None and pair.deadtime_min_ns < min_deadtime_ns:
            failures.append(f"Deadtime {pair.deadtime_min_ns:.1f} ns < required min {min_deadtime_ns:.1f} ns")

    passed = len(failures) == 0
    return {
        "passed": passed,
        "evidence_source": evidence_source,
        "data_integrity": integrity,
        "failures": failures,
        "pwm_summary": pwm.to_dict(),
        "pair_summary": pair_result
    }


@server.tool()
def logic_decode(
    capture_id: str,
    protocol: str = "uart",
    rx_channel: int = 0,
    tx_channel: Optional[int] = None,
    scl_channel: int = 0,
    sda_channel: int = 1,
    clk_channel: int = 0,
    mosi_channel: int = 1,
    miso_channel: Optional[int] = None,
    cs_channel: Optional[int] = None,
    baud_rate: int = 115200,
    cpol: int = 0,
    cpha: int = 0
) -> Dict[str, Any]:
    """
    Decode serial protocol packets from captured waveforms (UART, I2C, SPI).
    """
    proto = protocol.lower()

    if proto == "uart":
        raw_bytes, meta = _load_capture_channel_bits(capture_id, rx_channel)
        sr = float(meta.get("sample_rate", 20_000_000))
        packets = decode_uart(raw_bytes, sr, baud_rate=baud_rate)
        return {
            "protocol": "uart",
            "channel": rx_channel,
            "baud_rate": baud_rate,
            "packet_count": len(packets),
            "packets": packets[:100] # Return up to 100 frames
        }

    elif proto == "i2c":
        scl_b, meta = _load_capture_channel_bits(capture_id, scl_channel)
        sda_b, _ = _load_capture_channel_bits(capture_id, sda_channel)
        sr = float(meta.get("sample_rate", 20_000_000))
        events = decode_i2c(scl_b, sda_b, sr)
        return {
            "protocol": "i2c",
            "scl_channel": scl_channel,
            "sda_channel": sda_channel,
            "event_count": len(events),
            "events": events[:200]
        }

    elif proto == "spi":
        clk_b, meta = _load_capture_channel_bits(capture_id, clk_channel)
        mosi_b, _ = _load_capture_channel_bits(capture_id, mosi_channel)
        miso_b = _load_capture_channel_bits(capture_id, miso_channel)[0] if miso_channel is not None else None
        cs_b = _load_capture_channel_bits(capture_id, cs_channel)[0] if cs_channel is not None else None
        sr = float(meta.get("sample_rate", 20_000_000))
        transfers = decode_spi(clk_b, mosi_b, miso_b, cs_b, sr, cpol=cpol, cpha=cpha)
        return {
            "protocol": "spi",
            "clk_channel": clk_channel,
            "mosi_channel": mosi_channel,
            "transfer_count": len(transfers),
            "transfers": transfers[:100]
        }

    return {"error": f"Unsupported protocol: {protocol}. Supported: uart, i2c, spi"}


if __name__ == "__main__":
    # Run the standard stdio MCP transport
    server.run(transport="stdio")
