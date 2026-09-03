#pragma once

#include "types.h"
#include "constants.h"
#include <vector>
#include <cstdint>

namespace atkdl16 {

// Compute CRC-32 using the exact upstream table and algorithm
uint32_t compute_crc32(const uint8_t* data, size_t length);

// 4-way bank demultiplexing from FPGA to sequential PC sample order
// len must be an integer multiple of 2048
void convert_to_pc(const void* src, void* dst, size_t len);

// 4-way bank multiplexing from sequential PC order to FPGA format
// len must be an integer multiple of 2048
void convert_to_device(const void* src, void* dst, size_t len);

// Frequency <-> 1-based hardware code conversion
uint8_t sample_rate_to_code(uint64_t rate_hz);
uint64_t code_to_sample_rate(uint8_t code);

// Voltage threshold encoding (-5.0V to +5.0V -> byte)
uint8_t encode_threshold(double voltage);
double decode_threshold(uint8_t byte);

// Frame construction: applies 8-byte 0x00 padding, 0x0A, code, len, payload, 0x0B, CRC32,
// zero-padding to multiple of 2048, and convert_to_device() transformation.
std::vector<uint8_t> build_device_frame(CommandCode code, const uint8_t* payload, size_t payload_len);

// High-level command frame builders ready for Bulk OUT transmission
std::vector<uint8_t> build_get_device_data_frame();
std::vector<uint8_t> build_parameter_setting_frame(const CaptureConfig& config);
std::vector<uint8_t> build_simple_trigger_frame(const CaptureConfig& config);
std::vector<uint8_t> build_stop_frame();

} // namespace atkdl16
