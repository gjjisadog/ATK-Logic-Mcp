# ATK-Logic Upstream Codebase Audit & Architecture Analysis

## 1. Upstream Baseline Verification

| Attribute | Upstream Value | Verification Note |
| :--- | :--- | :--- |
| **Official Repository** | `https://github.com/alientek-openedv/atk-logic.git` | Official ALIENTEK GitHub repository |
| **Branch** | `master` | Primary production branch |
| **Reference Commit** | `0dff562d24436def2bec3791684f1911997b9e35` | Baseline specified in project brief |
| **Current Upstream HEAD** | `0dff562d24436def2bec3791684f1911997b9e35` | Verified via `git ls-remote` (May 13, 2024) |
| **Diff (Ref vs HEAD)** | None (0 commits, identical SHA) | Fully aligned with reference baseline |
| **License** | GNU General Public License v3.0 or later (GPL-3.0-or-later) | Stated in `COPYING` and `README.md` |

---

## 2. Upstream Architecture Overview

The upstream ATK-Logic software is built using Qt 5 / QML (GUI) with a C++ core interfacing with `libusb-1.0`.

### High-Level Layers in Upstream

```
┌─────────────────────────────────────────────────────────────┐
│                       Qt Quick / QML                        │
│   (SetContent.qml, Session.qml, ATabView.qml, Waveform UI)  │  <-- DISCARD COMPLETELY
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│                    Controller / Session                     │
│  (SessionController, Session, SessionConfig, SessionError)   │  <-- AUDIT & RE-ARCHITECT
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│               Data Management & Signal Engine               │
│               (Segment, Analysis, DecodeService)            │  <-- REIMPLEMENT HEADLESS
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│                  USB Transport / Threading                  │
│       (USBControl, usb_base, ThreadRead, ThreadWork)        │  <-- REIMPLEMENT IN C++17
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│                 Hardware (DL16 / DL16 Plus)                 │
│              WCH USB Controller + Xilinx FPGA               │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Code-Level Trace & Critical Findings

### 3.1 USB Transport & Hardware Interleaving

- **Source File**: [`pv/usb/usb_base.h`](file:///F:/Project/ATK-Logic-Mcp/upstream/atk-logic/pv/usb/usb_base.h), [`pv/usb/usb_base.cpp`](file:///F:/Project/ATK-Logic-Mcp/upstream/atk-logic/pv/usb/usb_base.cpp)
- **USB Device ID**:
  - `VENDOR_ID = 0x1A86` (WCH / QinHeng)
  - `PRODUCT_ID = 0xFFCC`
  - Defined in `pv/static/util.h:25-26`.
- **Endpoints**:
  - Bulk OUT: `0x02` (`TO_MCU_EP`)
  - Bulk IN: `0x81` (`TO_PC_EP`)
  - Defined in `pv/usb/usb_control.cpp:4-5`.
- **4-Way FPGA Bank De-interleaving (`logic_analyzer_convert_to_pc`)**:
  - Function: `logic_analyzer_convert_to_pc(const void* s, void* d, unsigned len)` in `pv/usb/usb_base.cpp:96-117`.
  - **Mechanism**: Data transfers arrive in 2048-byte blocks (1024 16-bit words). The FPGA memory architecture is 4-way interleaved across 4 internal 512-byte banks:
    - `src0 = src[0..511]` (Bank 0: samples $4j + 0$)
    - `src1 = src[512..1023]` (Bank 1: samples $4j + 1$)
    - `src2 = src[1024..1535]` (Bank 2: samples $4j + 2$)
    - `src3 = src[1536..2047]` (Bank 3: samples $4j + 3$)
  - The demultiplexer iterates $j \in [0, 255]$:
    ```cpp
    dst[k]   = src0[j];
    dst[k+1] = src1[j];
    dst[k+2] = src2[j];
    dst[k+3] = src3[j];
    ```
  - This reconstructs the continuous time sequence of 16-channel 16-bit words.
  - The reverse transformation is `logic_analyzer_convert_to_device` in `pv/usb/usb_base.cpp:119-140`.

---

### 3.2 TX Framing & Command Protocol

- **Source File**: [`pv/usb/usb_control.cpp`](file:///F:/Project/ATK-Logic-Mcp/upstream/atk-logic/pv/usb/usb_control.cpp), functions `Write(code, data, len)` and `SendToDevice`.
- **Framing Structure**:
  - Bytes 0–7: 8 bytes of `0x00` padding.
  - Byte 8: Frame Start delimiter `0x0A`.
  - Byte 9: Command Code (e.g. `0x10`, `0x11`, `0x12`, `0x15`, `0x17`, `0x18`).
  - Byte 10: Payload Length indicator `len - 1` (code + data length).
  - Bytes 11 .. (11 + payload_len - 1): Command payload.
  - Byte 11 + payload_len: Frame End delimiter `0x0B`.
  - Next 4 bytes: CRC-32 (little endian) of `[code, length, payload...]` using `gCRC32`.
  - Total frame is padded up to the nearest multiple of 2048 bytes with trailing zeros.
  - The padded buffer is passed through `logic_analyzer_convert_to_device()` and submitted as a Bulk OUT transfer to endpoint `0x02`.
- **Command Codes**:
  - `0x10`: `GetDeviceData` (Query FPGA device info)
  - `0x11`: `ParameterSetting` (Sample rate, mode, depth, threshold)
  - `0x12`: `SimpleTrigger` (Trigger condition and channel enables)
  - `0x15`: `Stop` (Halt capture)
  - `0x17`: `PWM` (PWM stimulus generator configuration)
  - `0x18`: `Exit` (Exit device mode)

---

### 3.3 RX Stream Demultiplexing & Framing

- **Source Files**: [`pv/data/analysis.cpp`](file:///F:/Project/ATK-Logic-Mcp/upstream/atk-logic/pv/data/analysis.cpp), [`pv/thread/thread_work.cpp`](file:///F:/Project/ATK-Logic-Mcp/upstream/atk-logic/pv/thread/thread_work.cpp).
- **Frame Format**:
  - Byte 0: Frame Start `0x0A`.
  - Byte 1: `order` (Message Type, values 1 to 6).
  - Bytes 2–3: `uint16_t length` (payload length, little-endian).
  - Bytes 4 .. (4 + length - 1): Payload data.
  - Byte 4 + length: `0x00`.
  - Byte 4 + length + 1: Frame End `0x0B`.
- **Message Orders**:
  1. `order = 1`: `ChannelData`
     - Payload byte 0: `channelID` (0..15).
     - Payload byte 1: Channel padding / reserved.
     - Payload bytes 2..: Bit samples for that single channel!
     - 8 samples per byte, bit 0 is earlier, bit 7 is later (LSB first in time).
     - If RLE enabled: parsed in `(count, byte_val)` pairs.
  2. `order = 2`: `DeviceInfo`
     - Byte 2: FPGA status (must be `1` for normal).
     - Byte 3: USB Mode (`2` = USB 2.0, `3` = USB 3.0).
     - Bytes 5–6: FPGA firmware version (`byte[5] * 100 + byte[6]`).
     - Bytes 7–8: Minimum software version (`byte[7] * 100 + byte[8]`).
     - Bytes 9..: Device model string (e.g. "ATK-DL16").
  3. `order = 3`: `TriggerOffset`
     - Bytes 0–4 (5 bytes, LE): `vernierTriggerPosition` (trigger offset in samples).
     - Bytes 5 .. 5 + channel_count * 5: Per-channel byte count (`temp`).
     - Used to calculate pre-trigger data crop: `startOffset = temp * 8 - triggerDepth - vernierTriggerPosition`.
     - In RLE mode: Trailing status byte: bit 0 = capacity exceeded, bit 1 = bandwidth exceeded.
  4. `order = 4`: `Ack`
     - Byte 2: Acknowledged command code.
     - Byte 3: If `order == 0x12`, byte 3 must be `3` to indicate trigger armed successfully.
  5. `order = 5`: `Progress`
     - Bytes 2–6 (5 bytes, LE): Capture progress sample count.
     - Ratio `progress / samplingDepth` gives capture percentage (0–100%).
  6. `order = 6`: `Complete`
     - Indicates transfer completion.
     - In Stream mode, byte 2 == 1 signifies capacity exceeded.

---

### 3.4 Hardware Identification: DL16 vs. DL16 Plus

- **Source File**: [`pv/thread/connect.cpp:70-76`](file:///F:/Project/ATK-Logic-Mcp/upstream/atk-logic/pv/thread/connect.cpp#L70-L76).
- **MCU Version Query**:
  - Sent via raw Bulk OUT `[0x0A, 0x81, ...]` (512 bytes).
  - Response starts with `[0x0A, 0x81, 0x01, 0x61, ...]`.
  - Byte 4, 5: MCU Firmware Version (`byte[4] * 10 + byte[5]`).
  - Byte 6: Hardware / Device Version.
  - Byte 8: **`level`** flag:
    - `level == 0`: **DL16 Standard** (Max 250 MHz in buffer, max 100 MHz stream, 1 Gbit buffer).
    - `level == 1`: **DL16 Plus** (Max 1 GHz in buffer for $\le 8$ ch, 500 MHz for 16 ch, 3.5 Gbit buffer).

---

### 3.5 Hardware Settling Delay (30 ms)

- **Source File**: [`pv/data/session.cpp:121-123`](file:///F:/Project/ATK-Logic-Mcp/upstream/atk-logic/pv/data/session.cpp#L121-L123).
  ```cpp
  reft = usb->ParameterSetting((quint8*)setBytes.data(), setBytes.size());
  if (reft) {
      QThread::msleep(30); // 两个指令之间要延迟一下，不然FPGA的电压没调整好
      reft = usb->SimpleTrigger((quint8*)dataBytes.data(), dataBytes.size());
  ```
  - **Reason**: The FPGA threshold comparator voltage is set via DAC. Sending the trigger/capture command immediately without a 30 ms settling delay causes invalid triggers due to threshold settling transients. This delay is mandatory.

---

### 3.6 Windows Driver Stack Audit

- **Driver Architecture**:
  - The DL16 USB interface utilizes Microsoft OS Descriptors (WCID) reporting `WinUSB` compatibility.
  - On Windows 8, 10, and 11, Windows automatically matches the device to `WinUSB.sys` upon insertion.
  - The upstream ATK-Logic Windows application links against `libusb-1.0` and uses the WinUSB backend.
  - **Coexistence Guarantee**: Our headless daemon `atk-dl16d` also uses `libusb-1.0` with the native WinUSB backend. No custom or conflicting kernel driver (e.g. libusbK or libusb-win32 filter) is required. The official GUI and `atk-dl16d` can be run alternately without any driver re-installation or Zadig switching.
  - **Process Exclusivity**: USB interface 0 can only be claimed by one process at a time. Process-level mutual exclusion (`device lock`) is enforced.
