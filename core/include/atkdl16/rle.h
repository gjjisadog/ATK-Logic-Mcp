#pragma once

#include <vector>
#include <cstdint>
#include <string>

namespace atkdl16 {

struct RleResult {
    bool success{false};
    std::string error_message;
    size_t compressed_bytes_consumed{0};
    size_t decompressed_size{0};
    std::vector<uint8_t> data;
};

// Decompresses run-length encoded payload pairs (count, byte_val)
// max_output_size prevents unbounded memory allocation on malicious/corrupted USB data.
RleResult decompress_rle(const uint8_t* compressed_data, size_t length, size_t max_output_size = 64 * 1024 * 1024);

// Compresses sample bytes into RLE pairs (useful for synthetic test generators)
std::vector<uint8_t> compress_rle(const uint8_t* raw_data, size_t length);

} // namespace atkdl16
