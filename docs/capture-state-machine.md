# ATK-DL16 Capture State Machine Specification

This document defines the formal capture state machine governing the `atk-dl16d` daemon and core library.

---

## 1. State Diagram

```mermaid
stateDiagram-v2
    [*] --> DISCONNECTED

    DISCONNECTED --> OPEN: open(device)
    OPEN --> DEVICE_READY: query_device_info() [level, fw, speed]
    
    DEVICE_READY --> FLUSH_RX: start_capture(config)
    FLUSH_RX --> CONFIGURE: flush complete
    CONFIGURE --> SETTLE: send ParameterSetting (0x11)
    
    SETTLE --> ARM_TRIGGER: sleep 30ms (DAC voltage settling)
    ARM_TRIGGER --> CAPTURING: send SimpleTrigger (0x12) & Ack received
    
    CAPTURING --> DRAINING: Order 3 (TriggerOffset) & Order 5 (Progress 100%)
    CAPTURING --> STOPPING: user cancel / timeout / stop (0x15)
    
    DRAINING --> COMPLETE: Order 6 (Complete) received
    STOPPING --> COMPLETE: Order 6 (Complete) or flush drained
    
    COMPLETE --> DEVICE_READY: data saved & cropped
    
    CAPTURING --> ERROR: USB_ERROR / TIMEOUT / RX_DESYNC
    DRAINING --> ERROR: USB_ERROR / TIMEOUT
    CONFIGURE --> ERROR: PROTOCOL_ERROR
    
    ERROR --> DEVICE_READY: reset_capture_state()
    ERROR --> DISCONNECTED: device unplugged
    DEVICE_READY --> DISCONNECTED: close()
```

---

## 2. State Definitions & Transitions

| State | Description | Entry Action | Valid Next States |
| :--- | :--- | :--- | :--- |
| **`DISCONNECTED`** | No device handle acquired. Device is unreferenced or closed. | Release interface, close libusb device. | `OPEN`, `DISCONNECTED` |
| **`OPEN`** | Device handle acquired via `libusb_open()`, interface 0 claimed. | Set auto kernel detach, claim interface 0. | `DEVICE_READY`, `DISCONNECTED`, `ERROR` |
| **`DEVICE_READY`** | Device enumerated, MCU/FPGA versions identified, idle. | Clear internal queues, unlock capture pipeline. | `FLUSH_RX`, `DISCONNECTED` |
| **`FLUSH_RX`** | Pre-capture sanitization: reading and discarding stale USB buffers. | Loop `ReadSynchronous()` until buffer empty (max 50ms). | `CONFIGURE`, `ERROR` |
| **`CONFIGURE`** | Sending `ParameterSetting` (`0x11`) with sample rate, depth, threshold. | Send framed `0x11` command via Bulk OUT. | `SETTLE`, `ERROR` |
| **`SETTLE`** | Mandatory hardware DAC threshold voltage stabilization period. | Non-blocking or timer sleep of exactly **30 ms**. | `ARM_TRIGGER`, `ERROR` |
| **`ARM_TRIGGER`** | Sending `SimpleTrigger` (`0x12`) with channel triggers & instant flag. | Send framed `0x12` command and verify Ack (Order 4, status 3). | `CAPTURING`, `ERROR` |
| **`CAPTURING`** | Hardware actively recording into RAM/FIFO, streaming USB Bulk IN. | Start `ThreadRead` / Async Bulk IN transfers. | `DRAINING`, `STOPPING`, `ERROR` |
| **`DRAINING`** | Buffer capture finished or trigger reached 100%, draining residual samples. | Ingest remaining Bulk IN transfers until Order 6 received. | `COMPLETE`, `ERROR` |
| **`STOPPING`** | Aborting capture early or responding to client stop request. | Send framed `Stop` (`0x15`) command via Bulk OUT. | `COMPLETE`, `ERROR` |
| **`COMPLETE`** | Capture successful. Pre-trigger sample cropping applied. Store finalized. | Generate metadata, compute final sample range, release capture lock. | `DEVICE_READY` |
| **`ERROR`** | Exceptional condition encountered. | Cancel pending transfers, log error with code. | `DEVICE_READY`, `DISCONNECTED` |

---

## 3. Sequence Details & Implicit Hardware Dependencies

### 3.1 Why 30 ms Settle Delay is Mandatory
As audited in `Session::OrderStart` ([`pv/data/session.cpp:123`](file:///F:/Project/ATK-Logic-Mcp/upstream/atk-logic/pv/data/session.cpp#L123)):
```cpp
reft = usb->ParameterSetting((quint8*)setBytes.data(), setBytes.size());
if (reft) {
    QThread::msleep(30); // 两个指令之间要延迟一下，不然FPGA的电压没调整好
    reft = usb->SimpleTrigger((quint8*)dataBytes.data(), dataBytes.size());
}
```
The DL16 utilizes high-speed comparator circuits whose threshold voltages are programmed by a digital-to-analog converter (DAC) controlled by the FPGA. When `ParameterSetting` is issued, the DAC output slews to the target voltage (e.g. 1.6V or 3.3V). If trigger acquisition is armed immediately, the input comparator may false-trigger on the intermediate voltage slope. The 30 ms wait allows the threshold voltage and decoupling filters to fully stabilize.

### 3.2 Pre-Capture Flush Routine
Before any configuration, `FLUSH_RX` must drain the MCU/FPGA FIFO:
```text
while (ReadSynchronous(buffer, timeout=5ms) > 0) {
    discard(buffer);
}
```
This ensures no leftover packets from a prior aborted capture corrupt the stream parser.

### 3.3 Buffer Cropping & Trigger Alignment
When `Order 3` (`TriggerOffset`) arrives:
1. Extract `vernierTriggerPosition` ($T_{\text{offset}}$, 40-bit uint).
2. For each channel $i \in [0, \text{channels}-1]$:
   - Extract total channel bytes $C_i$.
   - Compute start sample offset:
     $$\text{startOffset}_i = \begin{cases} (C_i \times 8) - \text{TriggerSamplingDepth} - T_{\text{offset}}, & \text{if } C_i \times 8 > \text{SamplingDepth} \\ 0, & \text{otherwise} \end{cases}$$
3. When saving or presenting samples:
   - Discard the first $\lfloor \text{startOffset}_i / 8 \rfloor$ bytes.
   - Adjust bit index by $\text{startOffset}_i \pmod 8$.
   - The trigger event aligns deterministically at index $\text{TriggerSamplingDepth}$.

### 3.4 Data Integrity & Order 4 ACK Verification
To eliminate false-positive capture completions, the pipeline enforces fail-closed evidence validation:
1. **Order 4 Trigger ACK**: After issuing `SimpleTrigger` (`0x12`), the driver blocks for `Order 4` (`status == 3`, `CMD_SIMPLE_TRIGGER`). Failure to receive this confirmation within 500ms aborts capture with `ErrorCode::ProtocolError`.
2. **Order 6 Completion**: Buffer mode capture must terminate with a valid `Order 6` completion token from the hardware before finalizing.
3. **Channel Completeness**: Each enabled channel must deliver $\ge 90\%$ of requested sample depth. Incomplete transfers flag `DataIntegrity::Incomplete` and return `ErrorCode::IncompleteCapture`.
4. **Hardware Capacity Enforcement**: Captures exceeding hardware RAM (128 MB for DL16, 448 MB for DL16 Plus) fail immediately with `DataIntegrity::Overflow` rather than corrupting memory.
