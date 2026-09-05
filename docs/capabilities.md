# ATK-DL16 & DL16 Plus Capabilities Matrix & Validation Rules

This document specifies the hardware limits, validation rules, and capability matrix for the ATK-DL16 and ATK-DL16 Plus logic analyzers, verified from upstream `qml/session/content/SetContent.qml` and `pv/controller/session_controller.cpp`.

---

## 1. Hardware Matrix Comparison

| Capability | ATK-DL16 (Standard) | ATK-DL16 Plus | Source Reference |
| :--- | :--- | :--- | :--- |
| **Identification** | `level == 0` | `level == 1` | `pv/thread/connect.cpp:73` |
| **Total Channels** | 16 | 16 | `pv/usb/usb_control.h` |
| **Max Sample Rate (Buffer)** | 250 MHz (all channels) | **1 GHz** ($\le 8$ ch), 500 MHz (16 ch) | `SetContent.qml:1233-1244` |
| **Max Sample Rate (Stream)** | 100 MHz (3 ch), 20 MHz (16 ch) | 100 MHz (3 ch), 20 MHz (16 ch) | `SetContent.qml:254-257` |
| **Internal RAM Buffer** | 1 Gbit (128 MB) | 3.5 Gbit (448 MB) | `SetContent.qml:1272-1275` |
| **RLE Buffer Depth Limit** | Up to 10 Gbits equivalent | Up to 35 Gbits equivalent | `SetContent.qml:1275` |
| **Stream Bandwidth Cap** | 320 Mbps (40 MB/s continuous) | 320 Mbps (40 MB/s continuous) | `SetContent.qml:1225` |
| **Threshold Voltage Range** | -5.0 V to +5.0 V | -5.0 V to +5.0 V | `Session.qml:110` |
| **Threshold Resolution** | 0.1 V (step 100 mV) | 0.1 V (step 100 mV) | `session_controller.cpp:250` |
| **Trigger Types** | Immediate, Rising, Falling, High, Low, Double Edge | Immediate, Rising, Falling, High, Low, Double Edge | `session_controller.cpp:282-307` |

---

## 2. Sample Rate & Channel Combinations

### 2.1 Buffer Mode (`mode == "buffer"`)

In Buffer Mode, samples are captured directly into on-device high-speed SRAM/DRAM and dumped to the host via USB after capture completion.

| Sample Rate | Frequency Code | DL16 Channels | DL16 Plus Channels |
| :--- | :---: | :---: | :---: |
| 1 MHz | 1 | 1 .. 16 | 1 .. 16 |
| 2 MHz | 2 | 1 .. 16 | 1 .. 16 |
| 4 MHz | 3 | 1 .. 16 | 1 .. 16 |
| 5 MHz | 4 | 1 .. 16 | 1 .. 16 |
| 10 MHz | 5 | 1 .. 16 | 1 .. 16 |
| 20 MHz | 6 | 1 .. 16 | 1 .. 16 |
| 25 MHz | 7 | 1 .. 16 | 1 .. 16 |
| 40 MHz | 8 | 1 .. 16 | 1 .. 16 |
| 50 MHz | 9 | 1 .. 16 | 1 .. 16 |
| 100 MHz | 10 | 1 .. 16 | 1 .. 16 |
| 200 MHz | 11 | 1 .. 16 | 1 .. 16 |
| 250 MHz | 12 | 1 .. 16 | 1 .. 16 |
| 500 MHz | 13 | **Unsupported** | 1 .. 16 |
| 1 GHz | 14 | **Unsupported** | **1 .. 8 channels only** |

### 2.2 Stream Mode (`mode == "stream"`) [EXPERIMENTAL]

> [!WARNING]
> Stream mode is currently marked as **Experimental / Unverified on Hardware**. Buffer mode is the primary production mode for deterministic analysis and HIL verification.

In Stream Mode, data flows continuously over USB Bulk IN to the host. The total aggregate bit rate must not exceed **320 Mbps** ($3.2 \times 10^8 \text{ bits/s}$), corresponding to ~40 MB/s of sustained USB bulk bandwidth:
$$\text{Rate} \times \text{EnabledChannels} \le 320{,}000{,}000$$

| Enabled Channels | Maximum Allowed Sample Rate | Bandwidth Utilization |
| :---: | :---: | :---: |
| 1 .. 3 channels | 100 MHz | $100 \text{ MHz} \times 3 = 300 \text{ Mbps} \le 320 \text{ Mbps}$ |
| 4 .. 8 channels | 25 MHz (or 40 MHz for $\le 8$ ch) | $40 \text{ MHz} \times 8 = 320 \text{ Mbps}$ |
| 9 .. 12 channels | 25 MHz | $25 \text{ MHz} \times 12 = 300 \text{ Mbps} \le 320 \text{ Mbps}$ |
| 13 .. 16 channels | 20 MHz | $20 \text{ MHz} \times 16 = 320 \text{ Mbps}$ |

---

## 3. Threshold Voltage Validation

- Target voltage $V_{\text{th}}$ must satisfy $-5.0 \le V_{\text{th}} \le 5.0$.
- Encoded as signed byte: $\text{sign\_bit} \mid \text{round}(\lvert V_{\text{th}} \rvert \times 10)$.
- Common standard thresholds:
  - 1.2V Logic: 0.6 V
  - 1.8V Logic: 0.9 V
  - 2.5V Logic: 1.25 V (encoded as 1.3V / 13)
  - 3.3V Logic: 1.65 V (encoded as 1.6V / 16 or 1.7V / 17)
  - 5.0V Logic: 2.5 V (encoded as 2.5V / 25)

---

## 4. Trigger Validation Rules

1. At least one trigger condition must be specified unless `is_instantly = true`.
2. Channels specified in triggers must be included in the enabled channels list.
3. Trigger types supported per channel:
   - `immediate` / `none`
   - `rising`
   - `falling`
   - `high`
   - `low`
   - `double_edge` / `either`
4. Pre-trigger position percentage must be between 0% and 100% (default 10% or 50%).

---

## 5. HIL Assertion Schema & DK9 PWM Physical Semantics

### 5.1 Deterministic Assertion Schema
Automated Hardware-in-the-Loop (HIL) testing enforces a strict schema under `tests/hil/dk9_openloop_pwm.yaml`:
- **Allowed Keys**: `pwm_carrier`, `duty_cycle`, `deadtime`, `shoot_through_protection`, `three_phase_modulation`.
- **Deprecated / Rejected Keys**: Old ambiguous keys (`pwm_frequency`, `three_phase_balance`) are rejected fail-closed with `HilSpecError`.
- **Capture Mode**: Must be `buffer`. Stream mode captures are rejected for trusted HIL assertions.
- **Complete Execution**: All declared assertions are tracked; if any declared assertion is unexecuted, the test fails closed with `HIL_FAIL`.

### 5.2 Center-Aligned ePWM Physical Semantics
In power electronics inverters (such as Hybrid30K driven by TI C2000 TMS320F28P65 in center-aligned Up-Down count mode):
- **Output Edge Shifts**: Rising and falling edges on phases U, V, W are triggered by compare registers (`CMPA`/`CMPB`). As duty cycles vary dynamically or differ between phases, output edges shift inherently relative to each other even when time-base counters (`TBCTR`) are 100% phase-locked.
- **Separation of Carrier Frequency and Phase**:
  - Carrier switching frequencies ($f_{sw,U}, f_{sw,V}, f_{sw,W}$) and frequency consistency (`carrier_frequency_diff_max_hz`) are measurable from output pin transitions.
  - Carrier phase synchronization is reported as `"NOT_MEASURED"` across output pins. It can only be evaluated when a dedicated hardware carrier reference channel (e.g. EPWM11 / CarrierSync pulsed at `TBCTR = 0`) is connected.
- **Output-Edge Period Variation RMS**: Under dynamic sinusoidal modulation, period jitter measured on output pins reflects switching edge variation under modulation (`max_output_edge_period_variation_rms_ns`), distinct from internal oscillator clock jitter.
- **Per-Channel Modulation Duty Extraction**: Duty cycle sequences are extracted independently for each phase using its own edges, avoiding cross-channel contamination.
- **Directed Phase Sequence Contract**: Modulation envelopes must verify directed phase sequence (`phase_sequence: "UVW"`). Reversal (e.g. $U \to W \to V$) fails closed with `PHASE_SEQUENCE_MISMATCH`, decoupling envelope symmetry from phase direction.
- **Deadband Bounds (Min & Max)**: Complementary switching pairs strictly enforce both `deadtime_min_ns >= min_allowed_ns` (shoot-through prevention) and `deadtime_max_ns <= max_allowed_ns` (distortion limitation) across both rising and falling deadtimes.

---

## 6. Host-Device Evidence & CLI JSON Escaping Contract

- **RFC 8259 Compliance**: All CLI JSON outputs (`--json` flag) enforce RFC 8259 compliance via `atkdl16::json_escape()`. Characters including `"`, `\`, `\b`, `\f`, `\n`, `\r`, `\t`, and control characters `0x00..0x1F` are escaped. Windows paths (e.g. `D:\a\ATK-Logic-Mcp\captures`) parse cleanly via `json.loads` without JSONDecodeError.
- **Stream Write-State Validation**: Binary sample files (`.bits`), transition tables (`edges.json`), and metadata (`meta.json`) validate file stream integrity via `.good()`, `.fail()`, `.bad()`, `.flush()`, and `.close()`. Any disk write or permission failure immediately returns `false` and sets `ErrorCode::ArtifactWriteError`.
- **Order 3 (TriggerOffset) Policy**: In Buffer mode, Order 3 (`TriggerOffset`) carries vernier trigger offset and per-channel sample byte counts for pre-trigger cropping. Missing Order 3 logs an explicit warning without stalling capture if complete sample depth is verified across all channels; duplicate Order 3 packets are detected and rejected.

