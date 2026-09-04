# ATK-DL16 Headless Logic Analyzer System & MCP Server

A high-performance, non-GUI, deterministic control system and Model Context Protocol (MCP) server for the **ALIENTEK ATK-DL16** and **ATK-DL16 Plus** USB logic analyzers.

Derived from the official [alientek-openedv/atk-logic](https://github.com/alientek-openedv/atk-logic) protocol baseline (commit `0dff562d24436def2bec3791684f1911997b9e35`), this project provides:
1. A modern, safe C++17 core library (`libatkdl16_core`) built with RAII and `libusb-1.0`.
2. A fast headless CLI tool (`atk-dl16.exe`) for automation, scripting, and CI pipelines.
3. A deterministic digital waveform analysis suite (NumPy-accelerated) for cycle-by-cycle PWM, deadtime, shoot-through detection, and three-phase motor inverter analysis.
4. An autonomous MCP server (`atk-dl16-mcp`) allowing AI assistants (Claude, Codex, Antigravity) to directly control logic captures, inspect signals, and execute HIL test assertions.

---

## 1. System Architecture

```text
┌─────────────────────────────────────────────────────────┐
│                   Codex / AI Agent                      │
└───────────────────────────┬─────────────────────────────┘
                            │ Model Context Protocol (stdio)
                            ▼
┌─────────────────────────────────────────────────────────┐
│              atk-dl16-mcp (Python Server)               │
│   logic_status | logic_capture | logic_measure_pwm      │
│   logic_measure_pair | logic_assert | logic_decode      │
└─────────────┬───────────────────────────┬───────────────┘
              │                           │
              ▼ (Waveform analysis)       ▼ (Subprocess / CLI)
┌───────────────────────────┐   ┌─────────────────────────┐
│     analysis/ Engine      │   │       atk-dl16 CLI      │
│  - PWM & Jitter           │   │  list | info | capture  │
│  - Deadtime & Overlap     │   │  inspect                │
│  - 3-Phase Balance        │   └────────────┬────────────┘
│  - UART / I2C / SPI       │                │
└───────────────────────────┘                ▼
                                ┌─────────────────────────┐
                                │      atkdl16_core       │
                                │  - 4-way deinterleave   │
                                │  - Formal State Machine │
                                │  - 30ms Settle Delay    │
                                │  - Pre-trigger crop     │
                                └────────────┬────────────┘
                                             │ WinUSB / libusb
                                             ▼
                                ┌─────────────────────────┐
                                │ ATK-DL16 / DL16 Plus HW │
                                └─────────────────────────┘
```

---

## 2. Capabilities Matrix

| Feature | ATK-DL16 Standard | ATK-DL16 Plus |
| :--- | :--- | :--- |
| **Hardware Identification** | `level == 0` | `level == 1` |
| **Max Sample Rate (Buffer)** | 250 MHz (16 channels) | **1 GHz** ($\le 8$ ch), 500 MHz (16 ch) |
| **Max Sample Rate (Stream)** *(Experimental)* | 100 MHz ($\le 3$ ch), 20 MHz (16 ch) | 100 MHz ($\le 3$ ch), 20 MHz (16 ch) |
| **On-Device RAM Depth** | 1 Gbit (128 MB) | 3.5 Gbit (448 MB) |
| **Stream Bandwidth Cap** *(Experimental)* | 320 Mbps (~40 MB/s sustained) | 320 Mbps (~40 MB/s sustained) |
| **Comparator Threshold** | -5.0 V to +5.0 V (100 mV step) | -5.0 V to +5.0 V (100 mV step) |
| **Trigger Types** | Immediate, Rising, Falling, High, Low, Double Edge | Immediate, Rising, Falling, High, Low, Double Edge |

---

## 3. Building the Project

### Prerequisites
- **Windows 10/11 x64** or **Linux**
- **CMake 3.20+**
- **MSVC 2022** (Windows) or **GCC / Clang** (Linux)
- **Python 3.10+** (with `numpy`, `pytest`, `pyyaml`, `mcp`)

### Compilation
```powershell
# Configure CMake
cmake -B build -G "Visual Studio 17 2022" -A x64

# Build core library, CLI, and test suite in Release mode
cmake --build build --config Release

# Run C++ Unit & Synthetic Tests
.\build\Release\atkdl16_tests.exe
```

### Python Analysis & Tests
```powershell
# Install Python requirements
pip install numpy pyyaml pytest mcp pydantic

# Run Python analysis and golden comparison tests
python -m pytest tests/ -v
```

---

## 4. Headless CLI (`atk-dl16`)

The standalone CLI executable is located at `build/Release/atk-dl16.exe`.

### Enumerate Connected Devices
```powershell
.\build\Release\atk-dl16.exe list
```
*Output:*
```text
Connected ATK-DL16 Devices (1 found):
  [0] Port: 1, VID: 0x1a86, PID: 0xffcc
```

### Query Device Information
```powershell
.\build\Release\atk-dl16.exe info
```
*Output:*
```text
Device Information:
  Model:               ATK-DL16
  Hardware Level:      DL16 Standard
  USB Link Speed:      USB 2.0
  MCU Firmware:        v1.0
  FPGA Firmware:       v1.02
  Channels:            16
```

### Capture Waveform
```powershell
.\build\Release\atk-dl16.exe capture `
  --channels 0,1 `
  --sample-rate 20M `
  --duration 20ms `
  --threshold 1.6 `
  --trigger ch0:rising `
  --out captures
```

### Inspect Stored Edges
```powershell
.\build\Release\atk-dl16.exe inspect --id <capture_id>
```

---

## 5. MCP Server Integration

To connect the ATK-DL16 MCP server to Claude Desktop or Antigravity, add the following to your `mcp_servers` configuration:

```json
{
  "mcpServers": {
    "atk-dl16": {
      "command": "python",
      "args": [
        "F:/Project/ATK-Logic-Mcp/run_mcp_server.py"
      ],
      "env": {
        "PYTHONPATH": "F:/Project/ATK-Logic-Mcp"
      }
    }
  }
}
```

### Exposed MCP Tools

1. **`logic_status()`**: Returns connected hardware status, model, firmware, link speed, and lock state.
2. **`logic_capture(...)`**: Performs logic capture on specified channels, sample rate, duration, and threshold voltage.
3. **`logic_measure_pwm(capture_id, channel, ...)`**: Deterministically computes cycle-by-cycle frequency, duty cycle, high/low time, jitter, and glitches.
4. **`logic_measure_pair(capture_id, high_ch, low_ch)`**: Analyzes half-bridge switching pairs: rising/falling deadtime, minimum deadtime, and shoot-through overlap detection.
5. **`logic_measure_three_phase(capture_id, u, v, w)`**: Analyzes 3-phase motor drive inverter signals: 120-degree phase shifts, frequency balance, and symmetry.
6. **`logic_inspect(capture_id, channel, start, max_edges)`**: Returns exact transition edge timestamps and logic levels.
7. **`logic_assert(capture_id, channel, ...)`**: Automated HIL assertion runner evaluating design tolerances (e.g. `freq_min`, `duty_max`, `min_deadtime_ns`, `no_overlap`).
8. **`logic_decode(capture_id, protocol, ...)`**: Decodes UART, I2C, and SPI serial communication streams.

---

## 6. Test Architecture & Evidence Integrity

ATK-Logic-Mcp enforces strict, non-fakeable evidence validation across three distinct test tiers:

```text
tests/
├── contract/        # End-to-end mocked HIL & negative fail-closed contract tests
├── synthetic/       # Pure software simulated signals ([SYNTHETIC_PASS] / [SYNTHETIC_FAIL])
├── golden/          # In-repo official reference capture regression ([GOLDEN_PASS])
└── hil/             # Real hardware-in-the-loop validation (HIL_PASS / HIL_FAIL / HIL_NOT_RUN)
```

### 6.1 Strict HIL State Model
The HIL runner (`tests/hil/run_dk9_hil.py`) will **never** fake a hardware pass with synthetic signals:
- **`HIL_PASS`**: Real physical ATK-DL16 device detected, capture completed with verified Order 4 ACK, 100% channel sample completeness (`actual_samples >= requested_samples`), data integrity verified (`COMPLETE`), and all physical waveform assertions passed.
- **`HIL_FAIL`**: Physical hardware capture completed, but physical assertions failed (e.g. shoot-through detected, frequency out of range), or spec validation failed.
- **`HIL_NOT_RUN`**: Hardware is absent, busy, unready, or capture was aborted. Exits with clean diagnostics (`evidence_source: NONE`) without masquerading as a test pass.

```powershell
# Run HIL verification (safe on machines with or without connected hardware)
python tests/hil/run_dk9_hil.py
```

### 6.2 Fail-Closed Evidence Validation & PWM Semantics
All MCP assertions (`logic_assert`), CLI `--json` output, and capture workflows enforce fail-closed integrity:
- **Unified RX Dispatch**: After issuing `SimpleTrigger` (`0x12`), the capture engine ingests all RX messages (`ChannelData`, `TriggerOffset`, `Progress`) while verifying `Order 4` ACK (`status == 3`), preventing premature ACK race conditions and data loss.
- **100% Channel Completeness**: Every channel must deliver 100% of requested depth. Incomplete captures immediately flag `data_integrity != "COMPLETE"`.
- **Evidence Provenance**: All assertion results explicitly report their evidence source (`REAL_HARDWARE`, `GOLDEN_FILE`, or `SAVED_CAPTURE`). Uncertified files cannot masquerade as golden fixtures.
- **Center-Aligned ePWM Physical Semantics**: In center-aligned symmetric PWM (e.g. TI TMS320F28P65 Up-Down count), output edge distance between phases shifts with duty cycle. Carrier phase is reported as `"NOT_MEASURED"` across output pins; carrier phase synchronization requires a dedicated reference channel (e.g. EPWM11 / CarrierSync).
- **PWM Metrics Split**: Signal validity separates statistical readiness (`measurement_valid`, cycles $\ge 5$) from pulse integrity (`anomaly_free`, zero missing pulses, extra edges, glitches, or period outliers). `valid = measurement_valid and anomaly_free`.

### 6.3 Self-Contained Golden Regression
The repository archives an authentic ATK-DL16 capture fixture at `tests/golden/pwm_10M_30_25.atkdl` along with verified metadata, ensuring full regression testing without external clones or internet dependencies.

```powershell
# Run entire test suite (synthetic, golden, decoders, MCP)
python -m pytest tests/ -v
```

---

## 7. Driver Model & Coexistence

On Windows 10 and 11, the ATK-DL16 uses Microsoft OS Descriptors (WCID) to bind natively to `WinUSB.sys`.
- Both the official ATK-Logic GUI and this headless system use standard `WinUSB`.
- **No Zadig driver switching is required.**
- The system includes multi-process device locking to prevent concurrent access conflicts. If the GUI is active, the system cleanly reports `DeviceBusy` with guidance to close the GUI.

---

## 8. License

This project is licensed under the **GNU General Public License v3.0 or later** (GPL-3.0-or-later) to maintain full compatibility with the upstream [alientek-openedv/atk-logic](https://github.com/alientek-openedv/atk-logic) codebase.
