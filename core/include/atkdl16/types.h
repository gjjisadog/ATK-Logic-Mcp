#pragma once

#include "constants.h"
#include <cstdint>
#include <string>
#include <vector>
#include <map>
#include <optional>

namespace atkdl16 {

// Error Codes
enum class ErrorCode {
    Ok = 0,
    DeviceNotFound,
    DeviceBusy,
    UsbOpenFailed,
    UsbClaimFailed,
    UsbTransferError,
    DeviceDisconnected,
    InvalidCaptureConfig,
    UnsupportedSampleRate,
    UnsupportedChannelCombination,
    UnsupportedTrigger,
    InvalidThreshold,
    RxDesync,
    ProtocolError,
    CaptureTimeout,
    CapacityExceeded,
    BandwidthExceeded,
    IncompleteCapture,
    AnalysisInvalid,
    ArtifactWriteError
};

inline const char* to_string(ErrorCode code) {
    switch (code) {
        case ErrorCode::Ok: return "OK";
        case ErrorCode::DeviceNotFound: return "DEVICE_NOT_FOUND";
        case ErrorCode::DeviceBusy: return "DEVICE_BUSY";
        case ErrorCode::UsbOpenFailed: return "USB_OPEN_FAILED";
        case ErrorCode::UsbClaimFailed: return "USB_CLAIM_FAILED";
        case ErrorCode::UsbTransferError: return "USB_TRANSFER_ERROR";
        case ErrorCode::DeviceDisconnected: return "DEVICE_DISCONNECTED";
        case ErrorCode::InvalidCaptureConfig: return "INVALID_CAPTURE_CONFIG";
        case ErrorCode::UnsupportedSampleRate: return "UNSUPPORTED_SAMPLE_RATE";
        case ErrorCode::UnsupportedChannelCombination: return "UNSUPPORTED_CHANNEL_COMBINATION";
        case ErrorCode::UnsupportedTrigger: return "UNSUPPORTED_TRIGGER";
        case ErrorCode::InvalidThreshold: return "INVALID_THRESHOLD";
        case ErrorCode::RxDesync: return "RX_DESYNC";
        case ErrorCode::ProtocolError: return "PROTOCOL_ERROR";
        case ErrorCode::CaptureTimeout: return "CAPTURE_TIMEOUT";
        case ErrorCode::CapacityExceeded: return "CAPACITY_EXCEEDED";
        case ErrorCode::BandwidthExceeded: return "BANDWIDTH_EXCEEDED";
        case ErrorCode::IncompleteCapture: return "INCOMPLETE_CAPTURE";
        case ErrorCode::AnalysisInvalid: return "ANALYSIS_INVALID";
        case ErrorCode::ArtifactWriteError: return "ARTIFACT_WRITE_ERROR";
        default: return "UNKNOWN_ERROR";
    }
}

struct Error {
    ErrorCode code{ErrorCode::Ok};
    std::string message;
    bool recoverable{false};
    std::string suggested_action;

    bool is_ok() const noexcept { return code == ErrorCode::Ok; }
    explicit operator bool() const noexcept { return is_ok(); }

    static Error ok() { return {ErrorCode::Ok, "Success", true, ""}; }
};

// Device Information
struct DeviceInfo {
    DeviceModel model{DeviceModel::Unknown};
    std::string model_name;
    uint16_t vid{VENDOR_ID};
    uint16_t pid{PRODUCT_ID};
    UsbSpeed usb_speed{UsbSpeed::Unknown};
    int mcu_firmware_version{0};
    int fpga_firmware_version{0};
    int hardware_version{0};
    int device_level{0}; // 0 = DL16, 1 = DL16 Plus
    int channel_count{16};
    int port_number{0};
    bool is_busy{false};
};

// Capture State Machine States
enum class CaptureState {
    Disconnected,
    Open,
    DeviceReady,
    FlushRx,
    Configure,
    Settle,
    ArmTrigger,
    Capturing,
    Draining,
    Stopping,
    Complete,
    Error
};

// Trigger Configuration
struct ChannelTrigger {
    uint8_t channel{0};
    TriggerType type{TriggerType::None};
};

struct TriggerConfig {
    bool is_instantly{false};           // If true, trigger immediately
    double position_percent{10.0};       // Pre-trigger percent (0.0 to 100.0)
    std::vector<ChannelTrigger> triggers; // Per-channel trigger definitions
};

// Capture Configuration
struct CaptureConfig {
    CaptureMode mode{CaptureMode::Buffer};
    uint64_t sample_rate{20'000'000};    // Frequency in Hz (e.g. 20 MHz)
    uint64_t duration_ms{20};            // Duration in ms
    double threshold_voltage{1.6};       // Threshold in Volts (-5.0V to +5.0V)
    std::vector<uint8_t> enabled_channels{0, 1}; // Enabled channel indices (0..15)
    std::map<uint8_t, std::string> channel_names; // Optional channel labels
    TriggerConfig trigger;
    bool enable_rle{false};

    // Derived helpers
    uint64_t total_samples() const {
        return (sample_rate / 1000ULL) * duration_ms;
    }
    uint64_t trigger_sample_index() const {
        return static_cast<uint64_t>(total_samples() * (trigger.position_percent / 100.0));
    }
};

// Data Integrity Level
enum class DataIntegrity {
    Unknown,
    Complete,
    Incomplete,
    Overflow
};

inline const char* to_string(DataIntegrity di) {
    switch (di) {
        case DataIntegrity::Complete: return "COMPLETE";
        case DataIntegrity::Incomplete: return "INCOMPLETE";
        case DataIntegrity::Overflow: return "OVERFLOW";
        default: return "UNKNOWN";
    }
}

// Edge definition for deterministic analysis
struct Edge {
    uint64_t sample_index{0};
    uint8_t level{0}; // 0 or 1
};

// Per-channel Edge List
struct ChannelEdges {
    uint8_t channel{0};
    uint8_t initial_level{0};
    std::vector<Edge> edges;
};

// Capture Summary Result
struct CaptureResult {
    std::string capture_id;
    bool success{false};
    ErrorCode error_code{ErrorCode::Ok};
    std::string error_message;
    std::vector<std::string> warnings;
    CaptureConfig config;

    DataIntegrity data_integrity{DataIntegrity::Unknown};
    uint64_t requested_samples{0};
    std::map<uint8_t, uint64_t> actual_samples_per_channel;
    uint64_t minimum_actual_samples{0};
    uint64_t actual_samples{0}; // Minimum valid across enabled channels
    uint64_t trigger_offset{0};
    bool trigger_offset_received{false};
    bool trigger_ack_received{false};
    bool capture_complete_received{false};
    bool capacity_exceeded{false};
    bool bandwidth_exceeded{false};

    std::map<uint8_t, ChannelEdges> channel_edges;
};

} // namespace atkdl16
