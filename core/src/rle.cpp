#include "atkdl16/rle.h"
#include <cstring>

namespace atkdl16 {

RleResult decompress_rle(const uint8_t* compressed_data, size_t length, size_t max_output_size) {
    RleResult res;
    if (!compressed_data || length == 0) {
        res.success = true;
        return res;
    }

    // RLE data must be pairs of (count, byte_value)
    if (length % 2 != 0) {
        res.success = false;
        res.error_message = "Malformed RLE input: odd byte length (truncated sequence)";
        return res;
    }

    // First pass: compute decompressed length and validate against max_output_size
    size_t total_decompressed = 0;
    for (size_t i = 0; i < length; i += 2) {
        uint8_t count = compressed_data[i];
        total_decompressed += count;
        if (total_decompressed > max_output_size) {
            res.success = false;
            res.error_message = "RLE expansion overflow: decompressed size exceeds safety limit";
            return res;
        }
    }

    // Second pass: unpack into output vector
    res.data.resize(total_decompressed);
    size_t write_pos = 0;
    for (size_t i = 0; i < length; i += 2) {
        uint8_t count = compressed_data[i];
        uint8_t val   = compressed_data[i + 1];
        if (count > 0) {
            std::memset(res.data.data() + write_pos, val, count);
            write_pos += count;
        }
    }

    res.success = true;
    res.compressed_bytes_consumed = length;
    res.decompressed_size = total_decompressed;
    return res;
}

std::vector<uint8_t> compress_rle(const uint8_t* raw_data, size_t length) {
    std::vector<uint8_t> result;
    if (!raw_data || length == 0) {
        return result;
    }

    size_t i = 0;
    while (i < length) {
        uint8_t current_val = raw_data[i];
        size_t run_len = 1;
        while (i + run_len < length && raw_data[i + run_len] == current_val && run_len < 255) {
            ++run_len;
        }
        result.push_back(static_cast<uint8_t>(run_len));
        result.push_back(current_val);
        i += run_len;
    }

    return result;
}

} // namespace atkdl16
