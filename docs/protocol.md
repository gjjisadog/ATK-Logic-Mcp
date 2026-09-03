# ATK-DL16 USB Communication Protocol Specification

This document formally specifies the hardware USB communication protocol for the ALIENTEK ATK-DL16 and ATK-DL16 Plus logic analyzers, audited directly from the official upstream reference codebase (`alientek-openedv/atk-logic`, commit `0dff562d24436def2bec3791684f1911997b9e35`).

---

## 1. USB Transport Layer

### 1.1 Device Identification
- **USB Vendor ID (VID)**: `0x1A86` (QinHeng / WCH)
- **USB Product ID (PID)**: `0xFFCC`
- **Class / Subclass / Protocol**: Vendor Specific (claims interface 0)
- **Driver Model (Windows)**: WinUSB (automatically matched via WCID descriptor on Windows 8/10/11)

### 1.2 USB Endpoints
- **EP 0x02 (Bulk OUT)**: Host to Device (Command & Configuration stream, `TO_MCU_EP`).
- **EP 0x81 (Bulk IN)**: Device to Host (Capture stream, status, and telemetry, `TO_PC_EP`).

### 1.3 Transfer Chunk Sizes
- Bulk IN transfer buffer size: 16,384 bytes (16 KB) or 32,768 bytes (32 KB).
- Interleaving Block Unit: **2048 bytes**. Every transfer submitted to the protocol converter must be an integer multiple of 2048 bytes.

---

## 2. FPGA 4-Way Bank Interleaving & Conversion

### 2.1 The 2048-Byte FPGA Interleave Format
The FPGA capture engine writes samples into four internal 512-byte banks (Bank 0, Bank 1, Bank 2, Bank 3) to achieve high throughput without requiring high-frequency single-port memory.
When sending data over USB Bulk IN:
- Bytes `[0 .. 511]` (256 16-bit words) come from Bank 0.
- Bytes `[512 .. 1023]` (256 16-bit words) come from Bank 1.
- Bytes `[1024 .. 1535]` (256 16-bit words) come from Bank 2.
- Bytes `[1536 .. 2047]` (256 16-bit words) come from Bank 3.

### 2.2 Conversion Algorithm (`convert_to_pc`)
To restore sequential sample order, every 2048-byte block is demultiplexed:
```text
For each 2048-byte block:
    src0 = (uint16_t*)(&buf[0])      // 256 samples
    src1 = (uint16_t*)(&buf[512])    // 256 samples
    src2 = (uint16_t*)(&buf[1024])   // 256 samples
    src3 = (uint16_t*)(&buf[1536])   // 256 samples

    For j = 0 .. 255:
        dst[4*j + 0] = src0[j]
        dst[4*j + 1] = src1[j]
        dst[4*j + 2] = src2[j]
        dst[4*j + 3] = src3[j]
```
The reverse process (`convert_to_device`) is applied to host command frames before sending over Bulk OUT.

---

## 3. Host-to-Device Command Framing (TX)

### 3.1 Frame Structure
Logic analyzer configuration commands sent to EP 0x02 follow this exact framing:

| Byte Offset | Length | Field | Description |
| :--- | :--- | :--- | :--- |
| `0 .. 7` | 8 bytes | `Padding` | Leading zeros (`0x00`) |
| `8` | 1 byte | `Header` | Fixed frame start delimiter: `0x0A` |
| `9` | 1 byte | `CommandCode` | Command ID (`0x10`, `0x11`, `0x12`, `0x15`, `0x17`, `0x18`) |
| `10` | 1 byte | `Length` | `payload_length + 1` (length of CommandCode + Payload) |
| `11 .. 10 + N` | N bytes | `Payload` | Command-specific payload |
| `11 + N` | 1 byte | `Footer` | Fixed frame end delimiter: `0x0B` |
| `12 + N .. 15 + N`| 4 bytes | `CRC32` | 32-bit CRC (LE) computed over `[CommandCode, Length, Payload...]` |
| `16 + N .. Pad` | Var | `ZeroPad` | Zero padding to make total transfer size a multiple of 2048 bytes |

The entire padded frame is then transformed using `convert_to_device()` prior to submission to `libusb_bulk_transfer()`.

### 3.2 CRC-32 Algorithm
Standard reversed polynomial `0xEDB88320`, initialized with `0`, final XOR with `0xFFFFFFFF`:
```c
uint32_t crc = 0;
for (int i = 0; i < len; ++i) {
    crc = table[(crc ^ buf[i]) & 0xFF] ^ (crc >> 8);
}
return crc ^ 0xFFFFFFFF;
```

---

## 4. TX Command Reference

### 4.1 `0x10`: GetDeviceData
- **Purpose**: Queries FPGA state, USB link speed, firmware versions, and model name.
- **Payload**: None (Length = 1, Payload = empty).

### 4.2 `0x11`: ParameterSetting
- **Purpose**: Configures sample rate, capture mode, buffer depth, trigger position, and threshold voltage.
- **Payload Length**: 13 bytes.
- **Field Layout**:

| Byte Offset | Type | Name | Description |
| :--- | :--- | :--- | :--- |
| `0` | `uint8` | `ModeFlags` | Bit 7: `isBuffer` (1 = Buffer mode, 0 = Stream mode)<br>Bit 6: `isRLE` (1 = Enable RLE compression)<br>Bits 5..0: Reserved (0) |
| `1` | `uint8` | `Threshold` | Voltage threshold encoding:<br>Bit 7: Sign (1 = negative, 0 = positive)<br>Bits 6..0: $\text{round}(\lvert V \rvert \times 10)$, range -5.0V to +5.0V (e.g. 1.6V $\to$ 16 / `0x10`) |
| `2` | `uint8` | `SampleRateCode`| Sample rate index (1-based, see Rate Table below) |
| `3 .. 7` | `uint40_le` | `SamplingDepth` | Total sample count to capture ($5 \text{ bytes}, \text{Depth} = \text{Rate} \times \text{Duration}$) |
| `8 .. 12` | `uint40_le` | `TriggerPosition`| Sample index of trigger point ($5 \text{ bytes}, \text{Depth} \times \text{Percent} / 100$) |

#### Sample Rate Index Table:
| Code | Frequency | Max Channels (DL16) | Max Channels (DL16 Plus) |
| :---: | :---: | :---: | :---: |
| 1 | 1 MHz | 16 | 16 |
| 2 | 2 MHz | 16 | 16 |
| 3 | 4 MHz | 16 | 16 |
| 4 | 5 MHz | 16 | 16 |
| 5 | 10 MHz | 16 | 16 |
| 6 | 20 MHz | 16 | 16 |
| 7 | 25 MHz | 16 (Buffer) / 12 (Stream) | 16 (Buffer) / 12 (Stream) |
| 8 | 40 MHz | 16 (Buffer) / 8 (Stream) | 16 (Buffer) / 8 (Stream) |
| 9 | 50 MHz | 16 (Buffer) | 16 (Buffer) |
| 10 | 100 MHz | 16 (Buffer) / 3 (Stream) | 16 (Buffer) / 3 (Stream) |
| 11 | 200 MHz | 16 (Buffer) | 16 (Buffer) |
| 12 | 250 MHz | 16 (Buffer) | 16 (Buffer) |
| 13 | 500 MHz | Unsupported | 16 (Buffer) |
| 14 | 1 GHz | Unsupported | $\le 8$ (Buffer) |

### 4.3 `0x12`: SimpleTrigger
- **Purpose**: Configures channel enable flags, trigger conditions per channel, and armed state.
- **Payload Length**: 9 bytes.
- **Field Layout**:
  - Bytes `0 .. 7` (8 bytes): Channel trigger pairs. Byte $k$ configures channels $2k$ (even, upper nibble) and $2k+1$ (odd, lower nibble).
    - **Even channel ($2k$)**:
      - Bit 7: Channel Enable (`1` = enabled, `0` = disabled)
      - Bits 6..4: Trigger condition:
        - `000`: Low Level
        - `001`: Rising Edge
        - `010`: Falling Edge
        - `011`: Double Edge (Both edges)
        - `100`: High Level
        - `111`: None / Ignore (Default)
    - **Odd channel ($2k+1$)**:
      - Bit 3: Channel Enable (`1` = enabled, `0` = disabled)
      - Bits 2..0: Trigger condition:
        - `000`: Low Level
        - `001`: Rising Edge
        - `010`: Falling Edge
        - `011`: Double Edge
        - `100`: High Level
        - `111`: None / Ignore (Default)
  - Byte `8`: `isInstantly` (`1` = capture immediately without waiting for trigger condition, `0` = wait for trigger condition).

### 4.4 `0x15`: Stop
- **Purpose**: Halts active capture or drains buffer.
- **Payload**: None (Length = 1, Payload = empty).

---

## 5. Device-to-Host Response Framing (RX)

### 5.1 RX Frame Structure
Bulk IN data on EP 0x81 (after `convert_to_pc()`) is composed of back-to-back variable-length frames:

| Byte Offset | Length | Field | Description |
| :--- | :--- | :--- | :--- |
| `0` | 1 byte | `Header` | Fixed frame start delimiter: `0x0A` |
| `1` | 1 byte | `Order` | Message Type (`1` to `6`) |
| `2 .. 3` | 2 bytes | `Length` | 16-bit payload length (LE) |
| `4 .. 3 + Length` | Length bytes | `Payload` | Message payload |
| `4 + Length` | 1 byte | `Delimiter` | `0x00` |
| `5 + Length` | 1 byte | `Footer` | Fixed frame end delimiter: `0x0B` |

### 5.2 Message Types (`RxMessageType`)

```cpp
enum class RxMessageType : uint8_t {
    ChannelData   = 1,
    DeviceInfo    = 2,
    TriggerOffset = 3,
    Ack           = 4,
    Progress      = 5,
    Complete      = 6
};
```

#### Order 1: ChannelData
- `Payload[0]`: `channel_id` (0 .. 15)
- `Payload[1]`: Channel padding / reserved
- `Payload[2 .. N-1]`: Sample data bytes for this single channel.
  - **Raw Mode**: Each byte holds 8 digital samples for `channel_id`.
    - Bit 0: Earliest sample $t_0$
    - Bit 7: Latest sample $t_7$
  - **RLE Mode**: Composed of `[count, value]` byte pairs.
    - Repeated count $C = \text{Payload}[2i]$, sample byte $V = \text{Payload}[2i+1]$.
    - Expands to $C$ identical copies of byte $V$.

#### Order 2: DeviceInfo
- `Payload[2]`: FPGA Status (1 = Normal, others = Error)
- `Payload[3]`: USB link mode (`2` = USB 2.0, `3` = USB 3.0)
- `Payload[5..6]`: FPGA Firmware Version (`Payload[5] * 100 + Payload[6]`)
- `Payload[7..8]`: Minimum Client Version (`Payload[7] * 100 + Payload[8]`)
- `Payload[9 .. N-1]`: Model Name ASCII string (e.g. `"ATK-DL16"`)

#### Order 3: TriggerOffset
- `Payload[2 .. 6]` (5 bytes, LE): `trigger_sample_offset` ($T_{\text{offset}}$)
- `Payload[7 .. 7 + 5 * 16]`: Per-channel total sample byte count array ($C_0 .. C_{15}$, 5 bytes each, LE).
  - Used for pre-trigger cropping:
    $$\text{startOffset}_i = (C_i \times 8) - \text{TriggerSamplingDepth} - T_{\text{offset}}$$
- In RLE mode, status byte at end:
  - Bit 0 (`0x01`): `isExceedCapacity`
  - Bit 1 (`0x02`): `isExceedBandwidth`

#### Order 4: Ack
- `Payload[2]`: Command code being acknowledged (e.g. `0x12` or `0x15`).
- If `Payload[2] == 0x12`, `Payload[3]` must equal `3` (Trigger Armed Confirmation).

#### Order 5: Progress
- `Payload[2 .. 6]` (5 bytes, LE): Current capture progress in samples.
- Percentage: $\min\left(\frac{\text{samples}}{\text{SamplingDepth}} \times 100, 100\right)$.

#### Order 6: Complete
- Sent when hardware finishes dumping all capture buffers.
- Signals transition to `COMPLETE` state.
