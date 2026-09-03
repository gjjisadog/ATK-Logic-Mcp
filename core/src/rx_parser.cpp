#include "atkdl16/rx_parser.h"
#include "atkdl16/rle.h"
#include <cstring>
#include <algorithm>

namespace atkdl16 {

void RxParser::reset() {
    m_buffer.clear();
    m_read_index = 0;
    m_message_queue.clear();
    m_desync_count = 0;
}

void RxParser::push_bytes(const uint8_t* data, size_t length) {
    if (!data || length == 0) {
        return;
    }
    m_buffer.insert(m_buffer.end(), data, data + length);
    process_buffer();
}

std::optional<RxMessage> RxParser::pop_message() {
    if (m_message_queue.empty()) {
        return std::nullopt;
    }
    RxMessage msg = std::move(m_message_queue.front());
    m_message_queue.erase(m_message_queue.begin());
    return msg;
}

void RxParser::process_buffer() {
    const size_t buf_size = m_buffer.size();

    while (m_read_index < buf_size) {
        // Look for frame start delimiter 0x0A
        if (m_buffer[m_read_index] != RX_FRAME_START) {
            ++m_read_index;
            ++m_desync_count;
            continue;
        }

        // Need at least 4 bytes to read header, order, and 16-bit length
        if (m_read_index + 4 > buf_size) {
            break; // Wait for more bytes
        }

        uint8_t order = m_buffer[m_read_index + 1];
        if (order < 1 || order > 6) {
            // Not a valid order, false start
            ++m_read_index;
            ++m_desync_count;
            continue;
        }

        uint16_t payload_len = static_cast<uint16_t>(m_buffer[m_read_index + 2]) |
                              (static_cast<uint16_t>(m_buffer[m_read_index + 3]) << 8);

        // Frame total size = 1 (0x0A) + 1 (order) + 2 (length) + payload_len + 1 (0x00) + 1 (0x0B)
        size_t total_frame_len = 4 + payload_len + 2;

        if (m_read_index + total_frame_len > buf_size) {
            break; // Partial frame, wait for next transfer
        }

        // Check footer: 0x00 followed by 0x0B
        size_t sep_index = m_read_index + 4 + payload_len;
        size_t footer_index = sep_index + 1;

        if (m_buffer[sep_index] == RX_FRAME_SEP && m_buffer[footer_index] == RX_FRAME_END) {
            // Valid frame found
            RxMessage msg;
            msg.type = static_cast<RxMessageType>(order);
            if (payload_len > 0) {
                msg.payload.assign(
                    m_buffer.begin() + (m_read_index + 4),
                    m_buffer.begin() + (m_read_index + 4 + payload_len)
                );
            }
            m_message_queue.push_back(std::move(msg));
            m_read_index += total_frame_len;
        } else {
            // Bad footer, desync recovery
            ++m_read_index;
            ++m_desync_count;
        }
    }

    // Compact buffer if consumed part is large
    if (m_read_index > 32768) {
        m_buffer.erase(m_buffer.begin(), m_buffer.begin() + m_read_index);
        m_read_index = 0;
    }
}

std::optional<ChannelDataMsg> RxParser::parse_channel_data(const RxMessage& msg, bool is_rle) {
    if (msg.type != RxMessageType::ChannelData || msg.payload.size() < 2) {
        return std::nullopt;
    }

    ChannelDataMsg result;
    result.channel_id = msg.payload[0];

    const uint8_t* raw_samples = msg.payload.data() + 2;
    size_t raw_len = msg.payload.size() - 2;

    if (is_rle) {
        auto rle_res = decompress_rle(raw_samples, raw_len);
        if (!rle_res.success) {
            return std::nullopt;
        }
        result.data = std::move(rle_res.data);
    } else {
        result.data.assign(raw_samples, raw_samples + raw_len);
    }

    return result;
}

std::optional<DeviceInfoMsg> RxParser::parse_device_info(const RxMessage& msg) {
    if (msg.type != RxMessageType::DeviceInfo || msg.payload.size() < 9) {
        return std::nullopt;
    }

    DeviceInfoMsg info;
    info.fpga_status_ok = (msg.payload[2] == 1);
    info.usb_speed = (msg.payload[3] == 3) ? UsbSpeed::Usb30 : UsbSpeed::Usb20;
    info.fpga_version = static_cast<int>(msg.payload[5]) * 100 + static_cast<int>(msg.payload[6]);
    info.min_client_version = static_cast<int>(msg.payload[7]) * 100 + static_cast<int>(msg.payload[8]);

    if (msg.payload.size() > 9) {
        info.model_name.assign(
            reinterpret_cast<const char*>(msg.payload.data() + 9),
            msg.payload.size() - 9
        );
        // Trim trailing nulls or whitespace
        while (!info.model_name.empty() && (info.model_name.back() == '\0' || info.model_name.back() == ' ')) {
            info.model_name.pop_back();
        }
    }
    return info;
}

static uint64_t read_uint40_le(const uint8_t* ptr) {
    uint64_t val = 0;
    val |= static_cast<uint64_t>(ptr[0]);
    val |= static_cast<uint64_t>(ptr[1]) << 8;
    val |= static_cast<uint64_t>(ptr[2]) << 16;
    val |= static_cast<uint64_t>(ptr[3]) << 24;
    val |= static_cast<uint64_t>(ptr[4]) << 32;
    return val;
}

std::optional<TriggerOffsetMsg> RxParser::parse_trigger_offset(const RxMessage& msg, int channel_count, bool is_rle) {
    if (msg.type != RxMessageType::TriggerOffset || msg.payload.size() < 7) {
        return std::nullopt;
    }

    TriggerOffsetMsg result;
    const uint8_t* p = msg.payload.data() + 2;
    size_t remaining = msg.payload.size() - 2;

    if (remaining < 5) {
        return std::nullopt;
    }
    result.trigger_sample_offset = read_uint40_le(p);
    p += 5;
    remaining -= 5;

    // Per-channel byte counts (5 bytes each)
    size_t expected_ch_bytes = static_cast<size_t>(channel_count) * 5;
    if (remaining >= expected_ch_bytes) {
        result.channel_byte_counts.reserve(channel_count);
        for (int i = 0; i < channel_count; ++i) {
            result.channel_byte_counts.push_back(read_uint40_le(p));
            p += 5;
        }
        remaining -= expected_ch_bytes;
    }

    if (is_rle && remaining >= 1) {
        uint8_t flags = *p;
        result.exceed_capacity = (flags & 0x01) != 0;
        result.exceed_bandwidth = (flags & 0x02) != 0;
    }

    return result;
}

std::optional<AckMsg> RxParser::parse_ack(const RxMessage& msg) {
    if (msg.type != RxMessageType::Ack || msg.payload.size() < 3) {
        return std::nullopt;
    }
    AckMsg ack;
    ack.command_code = msg.payload[2];
    ack.status = (msg.payload.size() > 3) ? msg.payload[3] : 0;
    return ack;
}

std::optional<ProgressMsg> RxParser::parse_progress(const RxMessage& msg) {
    if (msg.type != RxMessageType::Progress || msg.payload.size() < 7) {
        return std::nullopt;
    }
    ProgressMsg prog;
    prog.captured_samples = read_uint40_le(msg.payload.data() + 2);
    return prog;
}

std::optional<CompleteMsg> RxParser::parse_complete(const RxMessage& msg, bool is_buffer) {
    if (msg.type != RxMessageType::Complete) {
        return std::nullopt;
    }
    CompleteMsg comp;
    if (!is_buffer && msg.payload.size() >= 3) {
        comp.is_stream_capacity_exceeded = (msg.payload[2] == 1);
    }
    return comp;
}

} // namespace atkdl16
