#pragma once

#include "constants.h"
#include "types.h"
#include <vector>
#include <cstdint>
#include <optional>
#include <functional>

namespace atkdl16 {

struct RxMessage {
    RxMessageType type{RxMessageType::Unknown};
    std::vector<uint8_t> payload;
};

// Strongly typed parsed message representations
struct ChannelDataMsg {
    uint8_t channel_id{0};
    std::vector<uint8_t> data;
};

struct DeviceInfoMsg {
    bool fpga_status_ok{false};
    UsbSpeed usb_speed{UsbSpeed::Unknown};
    int fpga_version{0};
    int min_client_version{0};
    std::string model_name;
};

struct TriggerOffsetMsg {
    uint64_t trigger_sample_offset{0};
    std::vector<uint64_t> channel_byte_counts; // 16 channel byte counts
    bool exceed_capacity{false};
    bool exceed_bandwidth{false};
};

struct AckMsg {
    uint8_t command_code{0};
    uint8_t status{0};
};

struct ProgressMsg {
    uint64_t captured_samples{0};
};

struct CompleteMsg {
    bool is_stream_capacity_exceeded{false};
};

class RxParser {
public:
    RxParser() = default;

    // Reset parser state and internal buffer
    void reset();

    // Ingest newly arrived linear (converted) bytes from USB Bulk IN
    // May emit zero, one, or multiple parsed messages
    void push_bytes(const uint8_t* data, size_t length);

    // Check if parsed messages are available
    bool has_message() const noexcept { return !m_message_queue.empty(); }

    // Retrieve next parsed message
    std::optional<RxMessage> pop_message();

    // Helper parsers for specific message types
    static std::optional<ChannelDataMsg> parse_channel_data(const RxMessage& msg, bool is_rle = false);
    static std::optional<DeviceInfoMsg> parse_device_info(const RxMessage& msg);
    static std::optional<TriggerOffsetMsg> parse_trigger_offset(const RxMessage& msg, int channel_count = 16, bool is_rle = false);
    static std::optional<AckMsg> parse_ack(const RxMessage& msg);
    static std::optional<ProgressMsg> parse_progress(const RxMessage& msg);
    static std::optional<CompleteMsg> parse_complete(const RxMessage& msg, bool is_buffer = true);

    // Diagnostics
    size_t buffered_bytes() const noexcept { return m_buffer.size() - m_read_index; }
    size_t desync_count() const noexcept { return m_desync_count; }

private:
    void process_buffer();

    std::vector<uint8_t> m_buffer;
    size_t m_read_index{0};
    std::vector<RxMessage> m_message_queue;
    size_t m_desync_count{0};
};

} // namespace atkdl16
