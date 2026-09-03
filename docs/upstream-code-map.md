# Upstream Code Mapping & Architecture Translation

- **Upstream Repository**: `https://github.com/alientek-openedv/atk-logic.git`
- **Upstream Baseline Commit**: `0dff562d24436def2bec3791684f1911997b9e35` (branch `master`)
- **License**: GNU General Public License v3.0 or later (GPL-3.0-or-later)

This document details how upstream logic and algorithms map to our new headless C++17 core (`core/`), daemon (`daemon/`), CLI (`cli/`), analysis engine (`analysis/`), and MCP server (`mcp/`).

---

## 1. Upstream to Headless Architecture Map

| Our File / Component | Upstream Source File(s) | Upstream Commit | Reuse Type | Key Changes & Improvements |
| :--- | :--- | :---: | :---: | :--- |
| `core/include/atkdl16/constants.h` | `pv/static/util.h`, `pv/usb/usb_control.h` | `0dff562d` | Adapted | Centralized protocol enums, commands, VID/PID, timing delays. Eliminates raw numeric literals (`0x11`, `0x12`, `0x0A`, `0x0B`). |
| `core/src/usb_transport.cpp` | `pv/usb/usb_base.cpp`, `pv/usb/usb_control.cpp` | `0dff562d` | Adapted | Modern C++17 RAII wrapper for `libusb_context`, `libusb_device_handle`, and asynchronous `libusb_transfer` pools. Replaces Qt `QThread` and naked `new[]` with `std::vector` and smart pointers. |
| `core/src/device.cpp` | `pv/thread/connect.cpp`, `pv/usb/usb_control.cpp` | `0dff562d` | Adapted | Headless device enumeration, MCU version query (0x81), FPGA status query (0x10 / order 2), DL16 vs DL16 Plus identification via `level`. File-based device lock. |
| `core/src/protocol.cpp` | `pv/usb/usb_control.cpp`, `pv/controller/session_controller.cpp` | `0dff562d` | Adapted | Frame encoding for `0x11` (ParameterSetting), `0x12` (SimpleTrigger), `0x15` (Stop). 4-way 2048-byte FPGA bank interleaving (`convert_to_device`). CRC-32 computation. |
| `core/src/rx_parser.cpp` | `pv/data/analysis.cpp`, `pv/thread/thread_work.cpp` | `0dff562d` | Adapted | Robust stateful stream reassembly for USB Bulk IN chunks. Frame sync on `0x0A` ... `0x00 0x0B`. Supports partial frames, multiple frames per USB buffer, and resynchronization on corrupted headers. |
| `core/src/rle.cpp` | `pv/thread/thread_work.cpp:172-182` | `0dff562d` | Adapted | Decompresses run-length encoded `(count, byte_value)` streams. Includes bounds-checking and memory overflow prevention. |
| `core/src/sample_store.cpp` | `pv/data/segment.cpp`, `pv/thread/thread_work.cpp:251-279` | `0dff562d` | Adapted | Compact bit-packed sample store (8 samples per byte, LSB first). Pre-trigger cropping based on Order 3 `vernierTriggerPosition`. Export to binary files, JSON metadata, and edge lists. |
| `core/src/capability.cpp` | `qml/session/content/SetContent.qml` | `0dff562d` | Behavior Reference | Validates sample rates, channels, buffer depth, and threshold limits based on device model (DL16 vs DL16 Plus) and mode. |
| `core/src/capture.cpp` | `pv/data/session.cpp:108-150`, `pv/thread/thread_work.cpp` | `0dff562d` | Adapted | Implements formal capture state machine: FLUSH_RX $\to$ CONFIGURE $\to$ SETTLE (30ms) $\to$ ARM_TRIGGER $\to$ CAPTURING $\to$ DRAINING $\to$ COMPLETE. |
| `analysis/edge.py` | `pv/data/segment.cpp:GetDataEnd` | `0dff562d` | Behavior Reference | Vectorized NumPy transition/edge extraction from bit-packed sample streams. |
| `analysis/pwm.py` | Custom deterministic algorithm | — | Original | Measures frequency, period, duty cycle, high time, low time, and jitter from extracted edge timestamps. |
| `analysis/complementary.py` | Custom deterministic algorithm | — | Original | Analyzes complementary PWM pairs: rising/falling deadtime, minimum/maximum/average deadtime, overlap detection (shoot-through risk), and missing pulses. |
| `analysis/phase.py` | Custom deterministic algorithm | — | Original | Analyzes three-phase PWM (U/V/W): phase balance, frequency consistency, symmetry, and startup behavior. |
| `mcp/server.py` | Custom MCP server | — | Original | Thin MCP server providing `logic_status`, `logic_capture`, `logic_measure_pwm`, `logic_measure_pair`, `logic_measure_three_phase`, `logic_inspect`, `logic_assert`. |
