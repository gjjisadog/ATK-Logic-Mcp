#include "atkdl16/protocol.h"
#include <cstring>
#include <cmath>
#include <algorithm>
#include <stdexcept>

namespace atkdl16 {

// Upstream CRC-32 lookup table (from pv/static/util.cpp:150-168)
static const uint32_t CRC32_TABLE[256] = {
    0x00000000, 0x77073096, 0xee0e612c, 0x990951ba, 0x076dc419, 0x706af48f, 0xe963a535, 0x9e6495a3,
    0x0edb8832, 0x79dcb8a4, 0xe0d5e91e, 0x97d2d988, 0x09b64c2b, 0x7eb17cbd, 0xe7b82d07, 0x90bf1d91,
    0x1db71064, 0x6ab020f2, 0xf3b97148, 0x84be41de, 0x1adad47d, 0x6ddde4eb, 0xf4d4b551, 0x83d385c7,
    0x136c9856, 0x646ba8c0, 0xfd62f97a, 0x8a65c9ec, 0x14015c4f, 0x63066cd9, 0xfa0f3d63, 0x8d080df5,
    0x3b6e20c8, 0x4c69105e, 0xd56041e4, 0xa2677172, 0x3c03e4d1, 0x4b04d447, 0xd20d85fd, 0xa50ab56b,
    0x35b5a8fa, 0x42b2986c, 0xdbbbc9d6, 0xacbcf940, 0x32d86ce3, 0x45df5c75, 0xdcd60dcf, 0xabd13d59,
    0x26d930ac, 0x51de003a, 0xc8d75180, 0xbfd06116, 0x21b4f4b5, 0x56b3c423, 0xcfba9599, 0xb8bda50f,
    0x2802b89e, 0x5f058808, 0xc60cd9b2, 0xb10be924, 0x2f6f7c87, 0x58684c11, 0xc1611dab, 0xb6662d3d,
    0x76dc4190, 0x01db7106, 0x98d220bc, 0xefd5102a, 0x71b18589, 0x06b6b51f, 0x9fbfe4a5, 0xe8b8d433,
    0x7807c9a2, 0x0f00f934, 0x9609a88e, 0xe10e9818, 0x7f6a0dbb, 0x086d3d2d, 0x91646c97, 0xe6635c01,
    0x6b6b51f4, 0x1c6c6162, 0x856530d8, 0xf262004e, 0x6c0695ed, 0x1b01a57b, 0x8208f4c1, 0xf50fc457,
    0x65b0d9c6, 0x12b7e950, 0x8bbeb8ea, 0xfcb9887c, 0x62dd1ddf, 0x15da2d49, 0x8cd37cf3, 0xfbd44c65,
    0x4db26158, 0x3ab551ce, 0xa3bc0074, 0xd4bb30e2, 0x4adfa541, 0x3dd895d7, 0xa4d1c46d, 0xd3d6f4fb,
    0x4369e96a, 0x346ed9fc, 0xad678846, 0xda60b8d0, 0x44042d73, 0x33031de5, 0xaa0a4c5f, 0xdd0d7cc9,
    0x5005713c, 0x270241aa, 0xbe0b1010, 0xc90c2086, 0x5768b525, 0x206f85b3, 0xb966d409, 0xce61e49f,
    0x5edef90e, 0x29d9c998, 0xb0d09822, 0xc7d7a8b4, 0x59b33d17, 0x2eb40d81, 0xb7bd5c3b, 0xc0ba6cad,
    0xedb88320, 0x9abfb3b6, 0x03b6e20c, 0x74b1d29a, 0xead54739, 0x9dd277af, 0x04db2615, 0x73dc1683,
    0xe3630b12, 0x94643b84, 0x0d6d6a3e, 0x7a6a5aa8, 0xe40ecf0b, 0x9309ff9d, 0x0a00ae27, 0x7d079eb1,
    0xf00f9344, 0x8708a3d2, 0x1e01f268, 0x6906c2fe, 0xf762575d, 0x806567cb, 0x196c3671, 0x6e6b06e7,
    0xfed41b76, 0x89d32be0, 0x10da7a5a, 0x67dd4acc, 0xf9b9df6f, 0x8ebeeff9, 0x17b7be43, 0x60b08ed5,
    0xd6d6a3e8, 0xa1d1937e, 0x38d8c2c4, 0x4fdff252, 0xd1bb67f1, 0xa6bc5767, 0x3fb506dd, 0x48b2364b,
    0xd80d2bda, 0xaf0a1b4c, 0x36034af6, 0x41047a60, 0xdf60efc3, 0xa867df55, 0x316e8eef, 0x4669be79,
    0xcb61b38c, 0xbc66831a, 0x256fd2a0, 0x5268e236, 0xcc0c7795, 0xbb0b4703, 0x220216b9, 0x5505262f,
    0xc5ba3bbe, 0xb2bd0b28, 0x2bb45a92, 0x5cb36a04, 0xc2d7ffa7, 0xb5d0cf31, 0x2cd99e8b, 0x5bdeae1d,
    0x9b64c2b0, 0xec63f226, 0x756aa39c, 0x026d930a, 0x9c0906a9, 0xeb0e363f, 0x72076785, 0x05005713,
    0x95bf4a82, 0xe2b87a14, 0x7bb12bae, 0x0cb61b38, 0x92d28e9b, 0xe5d5be0d, 0x7cdcefb7, 0x0bdbdf21,
    0x86d3d2d4, 0xf1d4e242, 0x68ddb3f8, 0x1fda836e, 0x81be16cd, 0xf6b9265b, 0x6fb077e1, 0x18b74777,
    0x88085ae6, 0xff0f6a70, 0x66063bca, 0x11010b5c, 0x8f659eff, 0xf862ae69, 0x616bffd3, 0x166ccf45,
    0xa00ae278, 0xd70dd2ee, 0x4e048354, 0x3903b3c2, 0xa7672661, 0xd06016f7, 0x4969474d, 0x3e6e77db,
    0xaed16a4a, 0xd9d65adc, 0x40df0b66, 0x37d83bf0, 0xa9bcae53, 0xdebb9ec5, 0x47b2cf7f, 0x30b5ffe9,
    0xbdbdf21c, 0xcabac28a, 0x53b39330, 0x24b4a3a6, 0xbad03605, 0xcdd70693, 0x54de5729, 0x23d967bf,
    0xb3667a2e, 0xc4614ab8, 0x5d681b02, 0x2a6f2b94, 0xb40bbe37, 0xc30c8ea1, 0x5a05df1b, 0x2d02ef8d,
};

uint32_t compute_crc32(const uint8_t* data, size_t length) {
    if (length == 0 || data == nullptr) {
        return 0xFFFFFFFF;
    }
    uint32_t crc = 0;
    for (size_t i = 0; i < length; ++i) {
        crc = CRC32_TABLE[(crc ^ data[i]) & 0xFF] ^ (crc >> 8);
    }
    return crc ^ 0xFFFFFFFF;
}

void convert_to_pc(const void* s, void* d, size_t len) {
    const uint8_t* src = static_cast<const uint8_t*>(s);
    uint16_t* dst = static_cast<uint16_t*>(d);

    for (size_t i = 0; i < len; i += USB_BLOCK_SIZE) {
        const uint16_t* src0 = reinterpret_cast<const uint16_t*>(&src[i]);
        const uint16_t* src1 = reinterpret_cast<const uint16_t*>(&src[i + 512]);
        const uint16_t* src2 = reinterpret_cast<const uint16_t*>(&src[i + 1024]);
        const uint16_t* src3 = reinterpret_cast<const uint16_t*>(&src[i + 1536]);

        for (unsigned j = 0, k = 0; j < 256; ++j, k += 4) {
            dst[k]     = src0[j];
            dst[k + 1] = src1[j];
            dst[k + 2] = src2[j];
            dst[k + 3] = src3[j];
        }
        dst += 1024;
    }
}

void convert_to_device(const void* s, void* d, size_t len) {
    const uint16_t* src = static_cast<const uint16_t*>(s);
    uint8_t* dst = static_cast<uint8_t*>(d);

    for (size_t i = 0; i < len; i += USB_BLOCK_SIZE) {
        uint16_t* dst0 = reinterpret_cast<uint16_t*>(&dst[i]);
        uint16_t* dst1 = reinterpret_cast<uint16_t*>(&dst[i + 512]);
        uint16_t* dst2 = reinterpret_cast<uint16_t*>(&dst[i + 1024]);
        uint16_t* dst3 = reinterpret_cast<uint16_t*>(&dst[i + 1536]);

        for (unsigned j = 0, k = 0; j < 256; ++j, k += 4) {
            dst0[j] = src[k];
            dst1[j] = src[k + 1];
            dst2[j] = src[k + 2];
            dst3[j] = src[k + 3];
        }
        src += 1024;
    }
}

uint8_t sample_rate_to_code(uint64_t rate_hz) {
    switch (rate_hz) {
        case 1'000'000ULL:    return 1;
        case 2'000'000ULL:    return 2;
        case 4'000'000ULL:    return 3;
        case 5'000'000ULL:    return 4;
        case 10'000'000ULL:   return 5;
        case 20'000'000ULL:   return 6;
        case 25'000'000ULL:   return 7;
        case 40'000'000ULL:   return 8;
        case 50'000'000ULL:   return 9;
        case 100'000'000ULL:  return 10;
        case 200'000'000ULL:  return 11;
        case 250'000'000ULL:  return 12;
        case 500'000'000ULL:  return 13;
        case 1'000'000'000ULL:return 14;
        default:
            return 0; // Unsupported
    }
}

uint64_t code_to_sample_rate(uint8_t code) {
    switch (code) {
        case 1:  return 1'000'000ULL;
        case 2:  return 2'000'000ULL;
        case 3:  return 4'000'000ULL;
        case 4:  return 5'000'000ULL;
        case 5:  return 10'000'000ULL;
        case 6:  return 20'000'000ULL;
        case 7:  return 25'000'000ULL;
        case 8:  return 40'000'000ULL;
        case 9:  return 50'000'000ULL;
        case 10: return 100'000'000ULL;
        case 11: return 200'000'000ULL;
        case 12: return 250'000'000ULL;
        case 13: return 500'000'000ULL;
        case 14: return 1'000'000'000ULL;
        default: return 0;
    }
}

uint8_t encode_threshold(double voltage) {
    uint8_t b = 0;
    if (voltage < 0.0) {
        b |= 0x80;
    }
    double abs_v = std::abs(voltage);
    int rounded = static_cast<int>(std::round(abs_v * 10.0));
    rounded = std::clamp(rounded, 0, 50); // -5.0V to +5.0V
    b |= static_cast<uint8_t>(rounded & 0x7F);
    return b;
}

double decode_threshold(uint8_t byte) {
    bool is_negative = (byte & 0x80) != 0;
    double val = (byte & 0x7F) / 10.0;
    return is_negative ? -val : val;
}

static void append_int_le(std::vector<uint8_t>& buf, uint64_t value, size_t num_bytes) {
    for (size_t i = 0; i < num_bytes; ++i) {
        buf.push_back(static_cast<uint8_t>((value >> (i * 8)) & 0xFF));
    }
}

std::vector<uint8_t> build_device_frame(CommandCode code, const uint8_t* payload, size_t payload_len) {
    // 1. Build intermediate code buffer: [code, length, payload...]
    // length field is payload_len + 1 (i.e. code byte + payload bytes)
    std::vector<uint8_t> new_code;
    new_code.reserve(2 + payload_len);
    new_code.push_back(static_cast<uint8_t>(code));
    new_code.push_back(static_cast<uint8_t>(payload_len + 1));
    if (payload && payload_len > 0) {
        new_code.insert(new_code.end(), payload, payload + payload_len);
    }

    // 2. Compute CRC-32 over intermediate code buffer
    uint32_t crc = compute_crc32(new_code.data(), new_code.size());

    // 3. Assemble raw frame with 8-byte 0x00 padding, header 0x0A, footer 0x0B, CRC32
    // total raw length = 8 (padding) + 1 (0x0A) + new_code.size() + 1 (0x0B) + 4 (CRC)
    size_t raw_len = TX_HEADER_OFFSET + 1 + new_code.size() + 1 + sizeof(uint32_t);

    // 4. Pad up to multiple of 2048 bytes
    size_t padded_len = ((raw_len + USB_BLOCK_SIZE - 1) / USB_BLOCK_SIZE) * USB_BLOCK_SIZE;
    std::vector<uint8_t> raw_buffer(padded_len, 0x00);

    // Fill raw buffer
    raw_buffer[TX_HEADER_OFFSET] = TX_FRAME_START; // 0x0A at index 8
    std::memcpy(&raw_buffer[TX_HEADER_OFFSET + 1], new_code.data(), new_code.size());
    size_t footer_idx = TX_HEADER_OFFSET + 1 + new_code.size();
    raw_buffer[footer_idx] = TX_FRAME_END; // 0x0B
    std::memcpy(&raw_buffer[footer_idx + 1], &crc, sizeof(uint32_t));

    // 5. Convert to 4-way interleaved device format
    std::vector<uint8_t> converted_buffer(padded_len, 0x00);
    convert_to_device(raw_buffer.data(), converted_buffer.data(), padded_len);

    return converted_buffer;
}

std::vector<uint8_t> build_get_device_data_frame() {
    return build_device_frame(CommandCode::GetDeviceData, nullptr, 0);
}

std::vector<uint8_t> build_parameter_setting_frame(const CaptureConfig& config) {
    std::vector<uint8_t> payload;
    payload.reserve(13);

    // Byte 0: Mode flags (Bit 7: Buffer, Bit 6: RLE)
    uint8_t mode_flags = 0;
    if (config.mode == CaptureMode::Buffer) {
        mode_flags |= 128; // 0x80
    }
    if (config.enable_rle) {
        mode_flags |= 64;  // 0x40
    }
    payload.push_back(mode_flags);

    // Byte 1: Threshold voltage
    payload.push_back(encode_threshold(config.threshold_voltage));

    // Byte 2: Sample rate code (1-based)
    uint8_t rate_code = sample_rate_to_code(config.sample_rate);
    if (rate_code == 0) {
        rate_code = 6; // Default to 20 MHz if unknown
    }
    payload.push_back(rate_code);

    // Bytes 3..7 (5 bytes): SamplingDepth in samples
    uint64_t depth = config.total_samples();
    append_int_le(payload, depth, 5);

    // Bytes 8..12 (5 bytes): TriggerSamplingDepth in samples
    uint64_t trigger_pos = config.trigger_sample_index();
    append_int_le(payload, trigger_pos, 5);

    return build_device_frame(CommandCode::ParameterSetting, payload.data(), payload.size());
}

std::vector<uint8_t> build_simple_trigger_frame(const CaptureConfig& config) {
    std::vector<uint8_t> payload;
    payload.reserve(9);

    // Map channels to trigger types
    std::vector<bool> enabled(TOTAL_CHANNELS, false);
    for (uint8_t ch : config.enabled_channels) {
        if (ch < TOTAL_CHANNELS) {
            enabled[ch] = true;
        }
    }

    std::vector<TriggerType> triggers(TOTAL_CHANNELS, TriggerType::None);
    for (const auto& trig : config.trigger.triggers) {
        if (trig.channel < TOTAL_CHANNELS) {
            triggers[trig.channel] = trig.type;
        }
    }

    // Bytes 0..7: 16 channels in pairs (8 bytes)
    for (size_t i = 0; i < TOTAL_CHANNELS; i += 2) {
        uint8_t b = 0;
        // Even channel (i)
        if (enabled[i]) {
            b |= 128; // Enable bit 7
            switch (triggers[i]) {
                case TriggerType::RisingEdge:  b |= 16; break;
                case TriggerType::HighLevel:   b |= 64; break;
                case TriggerType::FallingEdge: b |= 32; break;
                case TriggerType::LowLevel:    break; // 0
                case TriggerType::DoubleEdge:  b |= (16 | 32); break;
                default:                       b |= (16 | 32 | 64); break; // Ignore
            }
        }

        // Odd channel (i + 1)
        if (enabled[i + 1]) {
            b |= 8; // Enable bit 3
            switch (triggers[i + 1]) {
                case TriggerType::RisingEdge:  b |= 1; break;
                case TriggerType::HighLevel:   b |= 4; break;
                case TriggerType::FallingEdge: b |= 2; break;
                case TriggerType::LowLevel:    break; // 0
                case TriggerType::DoubleEdge:  b |= (1 | 2); break;
                default:                       b |= (1 | 2 | 4); break; // Ignore
            }
        }
        payload.push_back(b);
    }

    // Byte 8: isInstantly (1 = instant, 0 = wait for trigger)
    payload.push_back(config.trigger.is_instantly ? 1 : 0);

    return build_device_frame(CommandCode::SimpleTrigger, payload.data(), payload.size());
}

std::vector<uint8_t> build_stop_frame() {
    return build_device_frame(CommandCode::Stop, nullptr, 0);
}

} // namespace atkdl16
