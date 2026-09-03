#pragma once

#include <cstdint>
#include <string_view>

namespace atkdl16 {

// USB Device Identifiers
constexpr uint16_t VENDOR_ID = 0x1A86;   // QinHeng / WCH
constexpr uint16_t PRODUCT_ID = 0xFFCC;  // DL16 / DL16 Plus Logic Analyzer

// USB Endpoints
constexpr uint8_t EP_BULK_OUT = 0x02;    // Host to MCU/FPGA (TO_MCU_EP)
constexpr uint8_t EP_BULK_IN  = 0x81;    // FPGA to Host (TO_PC_EP)

// Transfer Constraints
constexpr size_t USB_BLOCK_SIZE = 2048;            // 4-way interleaved FPGA block size
constexpr size_t READ_TRANSFER_BUFFER_SIZE = 16384; // 16 KB bulk transfer buffer
constexpr size_t MAX_READ_TRANSFERS = 40;           // Transfer pool size
constexpr size_t WRITE_TRANSFER_LENGTH = 4;
constexpr int DEFAULT_USB_TIMEOUT_MS = 150;
constexpr int CAPTURE_READ_TIMEOUT_MS = 1200;

// Mandatory Hardware Timing
constexpr int FPGA_DAC_SETTLING_DELAY_MS = 30; // Wait 30ms between ParameterSetting and SimpleTrigger

// Protocol Delimiters
constexpr uint8_t TX_FRAME_START = 0x0A;
constexpr uint8_t TX_FRAME_END   = 0x0B;
constexpr uint8_t RX_FRAME_START = 0x0A;
constexpr uint8_t RX_FRAME_END   = 0x0B;
constexpr uint8_t RX_FRAME_SEP   = 0x00;
constexpr size_t  TX_HEADER_OFFSET = 8; // 8 bytes of 0x00 padding

// TX Command Codes
enum class CommandCode : uint8_t {
    GetDeviceData    = 0x10,
    ParameterSetting = 0x11,
    SimpleTrigger    = 0x12,
    Stop             = 0x15,
    PWM              = 0x17,
    Exit             = 0x18,

    // Internal MCU commands (Do not expose to user/MCP)
    McuEnterBootloader = 0x80,
    McuGetVersion      = 0x81,
    McuEnterUpdate     = 0x82,
    McuSendData        = 0x83,
    McuRestart         = 0x84,
    FpgaEnterUpdate    = 0x85,
    FpgaSendData       = 0x86,
    FpgaResetActive    = 0x87,
    FpgaResetState     = 0x88
};

// RX Message Order Types
enum class RxMessageType : uint8_t {
    Unknown       = 0,
    ChannelData   = 1,
    DeviceInfo    = 2,
    TriggerOffset = 3,
    Ack           = 4,
    Progress      = 5,
    Complete      = 6
};

// Trigger Types per Channel
enum class TriggerType : uint8_t {
    LowLevel   = 0,
    RisingEdge = 1,
    FallingEdge= 2,
    DoubleEdge = 3,
    HighLevel  = 4,
    None       = 7
};

// Capture Operating Mode
enum class CaptureMode : uint8_t {
    Buffer = 0,
    Stream = 1
};

// Device Model
enum class DeviceModel : uint8_t {
    Unknown  = 0,
    DL16     = 1,
    DL16Plus = 2
};

// USB Link Speed
enum class UsbSpeed : uint8_t {
    Unknown = 0,
    Usb20   = 2,
    Usb30   = 3
};

// Channel Limits
constexpr size_t TOTAL_CHANNELS = 16;
constexpr size_t MAX_PLUS_1GHZ_CHANNELS = 8;

// Stream Bandwidth Limit (320 Mbps = 40 MB/s sustained)
constexpr uint64_t MAX_STREAM_BANDWIDTH_BPS = 320'000'000ULL;

} // namespace atkdl16
