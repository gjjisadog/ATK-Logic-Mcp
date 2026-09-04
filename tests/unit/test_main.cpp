#include "test_harness.h"
#include "atkdl16/protocol.h"
#include "atkdl16/rx_parser.h"
#include "atkdl16/rle.h"
#include "atkdl16/capability.h"
#include "atkdl16/sample_store.h"
#include "atkdl16/device.h"
#include <iostream>
#include <vector>
#include <numeric>
#include <filesystem>

namespace fs = std::filesystem;
using namespace atkdl16;

// ============================================================================
// 1. Protocol Unit Tests
// ============================================================================

TEST_CASE(test_crc32_standard) {
    // Standard test vector: "123456789"
    const char* str = "123456789";
    uint32_t crc = compute_crc32(reinterpret_cast<const uint8_t*>(str), 9);
    // Upstream CRC32 on "123456789":
    // Initial 0, table poly 0xEDB88320: 0xD202D277 (3523400311)
    ASSERT_EQ(crc, 0xD202D277U);

    // Empty buffer check
    ASSERT_EQ(compute_crc32(nullptr, 0), 0xFFFFFFFF);
}

TEST_CASE(test_4way_interleave_roundtrip) {
    // 2048-byte buffer
    std::vector<uint16_t> original_pc(1024);
    for (size_t i = 0; i < original_pc.size(); ++i) {
        original_pc[i] = static_cast<uint16_t>(i * 3 + 7);
    }

    std::vector<uint8_t> device_buf(2048);
    convert_to_device(original_pc.data(), device_buf.data(), 2048);

    std::vector<uint16_t> reconstructed_pc(1024);
    convert_to_pc(device_buf.data(), reconstructed_pc.data(), 2048);

    for (size_t i = 0; i < 1024; ++i) {
        ASSERT_EQ(original_pc[i], reconstructed_pc[i]);
    }
}

TEST_CASE(test_threshold_encoding) {
    // 1.6V -> positive, round(16) -> 0x10 = 16
    uint8_t code_1v6 = encode_threshold(1.6);
    ASSERT_EQ(code_1v6, 16);
    ASSERT_TRUE(std::abs(decode_threshold(code_1v6) - 1.6) < 0.05);

    // 3.3V -> positive, round(33) -> 33
    uint8_t code_3v3 = encode_threshold(3.3);
    ASSERT_EQ(code_3v3, 33);
    ASSERT_TRUE(std::abs(decode_threshold(code_3v3) - 3.3) < 0.05);

    // -1.5V -> negative (0x80) | 15 -> 143
    uint8_t code_neg = encode_threshold(-1.5);
    ASSERT_EQ(code_neg, 128 + 15);
    ASSERT_TRUE(std::abs(decode_threshold(code_neg) - (-1.5)) < 0.05);
}

TEST_CASE(test_sample_rate_conversion) {
    ASSERT_EQ(sample_rate_to_code(1'000'000ULL), 1);
    ASSERT_EQ(sample_rate_to_code(20'000'000ULL), 6);
    ASSERT_EQ(sample_rate_to_code(250'000'000ULL), 12);
    ASSERT_EQ(sample_rate_to_code(1'000'000'000ULL), 14);

    ASSERT_EQ(code_to_sample_rate(1), 1'000'000ULL);
    ASSERT_EQ(code_to_sample_rate(6), 20'000'000ULL);
    ASSERT_EQ(code_to_sample_rate(12), 250'000'000ULL);
    ASSERT_EQ(code_to_sample_rate(14), 1'000'000'000ULL);
}

TEST_CASE(test_command_frame_building) {
    CaptureConfig config;
    config.mode = CaptureMode::Buffer;
    config.sample_rate = 20'000'000;
    config.duration_ms = 20;
    config.threshold_voltage = 1.6;
    config.enabled_channels = {0, 1};

    auto set_frame = build_parameter_setting_frame(config);
    ASSERT_EQ(set_frame.size() % USB_BLOCK_SIZE, 0); // Must be multiple of 2048

    auto trig_frame = build_simple_trigger_frame(config);
    ASSERT_EQ(trig_frame.size() % USB_BLOCK_SIZE, 0);

    auto stop_frame = build_stop_frame();
    ASSERT_EQ(stop_frame.size() % USB_BLOCK_SIZE, 0);

    // Verify roundtrip through convert_to_pc extracts correct frame header
    std::vector<uint8_t> decoded(set_frame.size());
    convert_to_pc(set_frame.data(), decoded.data(), set_frame.size());

    ASSERT_EQ(decoded[8], TX_FRAME_START); // 0x0A
    ASSERT_EQ(decoded[9], static_cast<uint8_t>(CommandCode::ParameterSetting)); // 0x11
    ASSERT_EQ(decoded[10], 14); // 13 bytes payload + 1
}

// ============================================================================
// 2. RX Parser Synthetic Tests
// ============================================================================

static std::vector<uint8_t> make_synthetic_rx_frame(uint8_t order, const std::vector<uint8_t>& payload) {
    std::vector<uint8_t> frame;
    frame.push_back(RX_FRAME_START); // 0x0A
    frame.push_back(order);
    uint16_t len = static_cast<uint16_t>(payload.size());
    frame.push_back(static_cast<uint8_t>(len & 0xFF));
    frame.push_back(static_cast<uint8_t>((len >> 8) & 0xFF));
    frame.insert(frame.end(), payload.begin(), payload.end());
    frame.push_back(RX_FRAME_SEP); // 0x00
    frame.push_back(RX_FRAME_END); // 0x0B
    return frame;
}

TEST_CASE(test_rx_parser_complete_frame) {
    RxParser parser;
    std::vector<uint8_t> payload = {0x00, 0x00, 0xAA, 0xBB, 0xCC};
    auto frame = make_synthetic_rx_frame(1, payload); // Order 1 (ChannelData)

    parser.push_bytes(frame.data(), frame.size());
    ASSERT_TRUE(parser.has_message());

    auto msg = parser.pop_message();
    ASSERT_TRUE(msg.has_value());
    ASSERT_EQ(static_cast<uint8_t>(msg->type), 1);
    ASSERT_EQ(msg->payload.size(), 5);
    ASSERT_EQ(msg->payload[2], 0xAA);
    ASSERT_FALSE(parser.has_message());
}

TEST_CASE(test_rx_parser_fragmented_frame) {
    RxParser parser;
    std::vector<uint8_t> payload = {0x01, 0x00, 0x11, 0x22, 0x33, 0x44, 0x55};
    auto frame = make_synthetic_rx_frame(1, payload);

    // Feed in two chunks
    size_t split = 4;
    parser.push_bytes(frame.data(), split);
    ASSERT_FALSE(parser.has_message()); // Incomplete

    parser.push_bytes(frame.data() + split, frame.size() - split);
    ASSERT_TRUE(parser.has_message()); // Now complete

    auto msg = parser.pop_message();
    ASSERT_TRUE(msg.has_value());
    ASSERT_EQ(msg->payload[3], 0x22);
}

TEST_CASE(test_rx_parser_multiple_frames_in_one_chunk) {
    RxParser parser;
    auto f1 = make_synthetic_rx_frame(4, {0x00, 0x00, 0x12, 0x03}); // Ack
    auto f2 = make_synthetic_rx_frame(6, {0x00, 0x00});             // Complete

    std::vector<uint8_t> stream;
    stream.insert(stream.end(), f1.begin(), f1.end());
    stream.insert(stream.end(), f2.begin(), f2.end());

    parser.push_bytes(stream.data(), stream.size());

    ASSERT_TRUE(parser.has_message());
    auto m1 = parser.pop_message();
    ASSERT_TRUE(m1.has_value());
    ASSERT_EQ(static_cast<uint8_t>(m1->type), 4);

    ASSERT_TRUE(parser.has_message());
    auto m2 = parser.pop_message();
    ASSERT_TRUE(m2.has_value());
    ASSERT_EQ(static_cast<uint8_t>(m2->type), 6);

    ASSERT_FALSE(parser.has_message());
}

TEST_CASE(test_rx_parser_garbage_recovery) {
    RxParser parser;
    std::vector<uint8_t> garbage = {0xFF, 0x55, 0x00, 0x12, 0x34};
    auto valid_frame = make_synthetic_rx_frame(5, {0x00, 0x00, 0x10, 0x20, 0x30, 0x40, 0x00});

    std::vector<uint8_t> stream;
    stream.insert(stream.end(), garbage.begin(), garbage.end());
    stream.insert(stream.end(), valid_frame.begin(), valid_frame.end());

    parser.push_bytes(stream.data(), stream.size());
    ASSERT_TRUE(parser.has_message());

    auto msg = parser.pop_message();
    ASSERT_TRUE(msg.has_value());
    ASSERT_EQ(static_cast<uint8_t>(msg->type), 5); // Progress
    ASSERT_TRUE(parser.desync_count() > 0);
}

// ============================================================================
// 3. RLE Decompression & Compression Tests
// ============================================================================

TEST_CASE(test_rle_basic_roundtrip) {
    std::vector<uint8_t> original;
    // 100 zeros, 50 ones, 200 of 0x55
    original.insert(original.end(), 100, 0x00);
    original.insert(original.end(), 50, 0xFF);
    original.insert(original.end(), 200, 0x55);

    auto compressed = compress_rle(original.data(), original.size());
    auto decomp = decompress_rle(compressed.data(), compressed.size());

    ASSERT_TRUE(decomp.success);
    ASSERT_EQ(decomp.data.size(), original.size());
    for (size_t i = 0; i < original.size(); ++i) {
        ASSERT_EQ(decomp.data[i], original[i]);
    }
}

TEST_CASE(test_rle_malformed_input) {
    // Odd byte count
    std::vector<uint8_t> bad_rle = {10, 0xFF, 5};
    auto res = decompress_rle(bad_rle.data(), bad_rle.size());
    ASSERT_FALSE(res.success);

    // Expansion limit exceeded
    std::vector<uint8_t> big_rle = {250, 0xAA, 250, 0xBB};
    auto res_limit = decompress_rle(big_rle.data(), big_rle.size(), 400);
    ASSERT_FALSE(res_limit.success); // 500 > 400
}

// ============================================================================
// 4. Capability Manager Tests
// ============================================================================

TEST_CASE(test_capability_dl16_standard) {
    DeviceInfo dev;
    dev.device_level = 0; // Standard DL16

    CaptureConfig cfg;
    cfg.mode = CaptureMode::Buffer;
    cfg.enabled_channels = {0, 1};
    cfg.duration_ms = 10;
    cfg.threshold_voltage = 1.6;

    // 250 MHz should PASS
    cfg.sample_rate = 250'000'000ULL;
    ASSERT_TRUE(CapabilityManager::validate(dev, cfg).is_ok());

    // 500 MHz should FAIL on standard DL16
    cfg.sample_rate = 500'000'000ULL;
    ASSERT_FALSE(CapabilityManager::validate(dev, cfg).is_ok());

    // 1 GHz should FAIL on standard DL16
    cfg.sample_rate = 1'000'000'000ULL;
    ASSERT_FALSE(CapabilityManager::validate(dev, cfg).is_ok());
}

TEST_CASE(test_capability_dl16_plus) {
    DeviceInfo dev;
    dev.device_level = 1; // DL16 Plus

    CaptureConfig cfg;
    cfg.mode = CaptureMode::Buffer;
    cfg.duration_ms = 5;
    cfg.threshold_voltage = 3.3;

    // 500 MHz on 16 channels should PASS
    cfg.enabled_channels = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15};
    cfg.sample_rate = 500'000'000ULL;
    ASSERT_TRUE(CapabilityManager::validate(dev, cfg).is_ok());

    // 1 GHz on 16 channels should FAIL (> 8 channels)
    cfg.sample_rate = 1'000'000'000ULL;
    ASSERT_FALSE(CapabilityManager::validate(dev, cfg).is_ok());

    // 1 GHz on 8 channels should PASS
    cfg.enabled_channels = {0, 1, 2, 3, 4, 5, 6, 7};
    ASSERT_TRUE(CapabilityManager::validate(dev, cfg).is_ok());
}

TEST_CASE(test_capability_stream_bandwidth) {
    DeviceInfo dev;
    dev.device_level = 0;

    CaptureConfig cfg;
    cfg.mode = CaptureMode::Stream;
    cfg.duration_ms = 10;

    // 20 MHz on 16 channels = 320 Mbps -> PASS
    cfg.enabled_channels = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15};
    cfg.sample_rate = 20'000'000ULL;
    ASSERT_TRUE(CapabilityManager::validate(dev, cfg).is_ok());

    // 40 MHz on 16 channels = 640 Mbps -> FAIL (exceeds 320 Mbps)
    cfg.sample_rate = 40'000'000ULL;
    ASSERT_FALSE(CapabilityManager::validate(dev, cfg).is_ok());

    // 100 MHz on 3 channels = 300 Mbps -> PASS
    cfg.enabled_channels = {0, 1, 2};
    cfg.sample_rate = 100'000'000ULL;
    ASSERT_TRUE(CapabilityManager::validate(dev, cfg).is_ok());
}

// ============================================================================
// 5. Sample Store & Edge Extraction Tests
// ============================================================================

TEST_CASE(test_sample_store_edges) {
    CaptureConfig cfg;
    cfg.enabled_channels = {0};
    cfg.sample_rate = 20'000'000;
    cfg.duration_ms = 1; // 20,000 samples

    SampleStore store(cfg);

    std::vector<uint8_t> raw_bytes(20, 0);
    raw_bytes[1] = 0xFC;
    raw_bytes[2] = 0xFF;
    raw_bytes[3] = 0x3F;

    store.append_channel_data(0, raw_bytes.data(), raw_bytes.size());
    store.finalize_samples(160);

    auto edges = store.extract_edges(0);
    ASSERT_EQ(edges.initial_level, 0);
    ASSERT_EQ(edges.edges.size(), 2);
    ASSERT_EQ(edges.edges[0].sample_index, 10);
    ASSERT_EQ(edges.edges[0].level, 1);
    ASSERT_EQ(edges.edges[1].sample_index, 30);
    ASSERT_EQ(edges.edges[1].level, 0);
}

TEST_CASE(test_device_open_hardware) {
    Device dev;
    auto err = dev.open();
    if (!err) {
        ASSERT_TRUE(err.code == ErrorCode::DeviceBusy || err.code == ErrorCode::DeviceNotFound ||
                    err.code == ErrorCode::ProtocolError || err.code == ErrorCode::UsbOpenFailed);
    } else {
        std::cout << "  (Hardware open SUCCESS: " << dev.info().model_name << ")" << std::endl;
        ASSERT_TRUE(dev.is_open());
        dev.close();
    }
}

TEST_CASE(test_capture_integrity_validation) {
    CaptureConfig cfg;
    cfg.enabled_channels = {0, 1};
    SampleStore store(cfg);

    // Channel 0 gets 128 bytes = 1024 samples
    std::vector<uint8_t> data0(128, 0xAA);
    store.append_channel_data(0, data0.data(), data0.size());

    // Channel 1 gets only 64 bytes = 512 samples
    std::vector<uint8_t> data1(64, 0x55);
    store.append_channel_data(1, data1.data(), data1.size());

    std::vector<std::string> warnings;
    auto integrity = store.validate_integrity(1000, warnings);
    ASSERT_TRUE(integrity == DataIntegrity::Incomplete);
    ASSERT_EQ(warnings.size(), 1);

    // Provide missing samples for channel 1
    store.append_channel_data(1, data1.data(), data1.size());
    warnings.clear();
    integrity = store.validate_integrity(1000, warnings);
    ASSERT_TRUE(integrity == DataIntegrity::Complete);
    ASSERT_EQ(warnings.size(), 0);
}

TEST_CASE(test_trigger_crop_sub_byte_offsets) {
    CaptureConfig cfg;
    cfg.enabled_channels = {0};

    // Test 1-bit, 3-bit, and 7-bit offsets
    for (uint64_t offset_bits : {1ULL, 3ULL, 7ULL, 13ULL}) {
        SampleStore store(cfg);
        // Append 10 bytes = 80 samples
        std::vector<uint8_t> data(10, 0xFF);
        store.append_channel_data(0, data.data(), data.size());
        ASSERT_EQ(store.sample_count(0), 80ULL);

        std::map<uint8_t, uint64_t> offsets = {{0, offset_bits}};
        store.apply_start_offsets(offsets);

        // Valid sample count must be exactly 80 - offset_bits
        uint64_t expected_samples = 80ULL - offset_bits;
        ASSERT_EQ(store.sample_count(0), expected_samples);
    }
}

TEST_CASE(test_order4_ack_trigger_validation) {
    // Construct Order 4 ACK packet payload: [0x00, 0x00, cmd=0x12, status=0x03, 0x00...]
    std::vector<uint8_t> ack_payload(16, 0x00);
    ack_payload[2] = static_cast<uint8_t>(CommandCode::SimpleTrigger); // 0x12
    ack_payload[3] = 0x03; // status 3 = Armed

    RxMessage msg;
    msg.type = RxMessageType::Ack;
    msg.payload = ack_payload;

    auto ack_opt = RxParser::parse_ack(msg);
    ASSERT_TRUE(ack_opt.has_value());
    ASSERT_EQ(ack_opt->command_code, static_cast<uint8_t>(CommandCode::SimpleTrigger));
    ASSERT_EQ(ack_opt->status, 3);

    // Negative case: wrong status
    ack_payload[3] = 0x02;
    msg.payload = ack_payload;
    auto bad_ack = RxParser::parse_ack(msg);
    ASSERT_TRUE(bad_ack.has_value());
    ASSERT_EQ(bad_ack->status, 2);
}

// ============================================================================
// Main Test Runner
// ============================================================================

int main() {
    std::cout << "========================================" << std::endl;
    std::cout << "  ATK-DL16 Unit & Synthetic Test Suite  " << std::endl;
    std::cout << "========================================" << std::endl;

    const auto& tests = atkdl16::test::get_registry();
    size_t passed = 0;

    for (const auto& t : tests) {
        std::cout << "[ RUN      ] " << t.name << std::endl;
        t.func();
        std::cout << "[       OK ] " << t.name << std::endl;
        ++passed;
    }

    std::cout << "========================================" << std::endl;
    std::cout << "  RESULTS: " << passed << "/" << tests.size() << " tests passed." << std::endl;
    std::cout << "========================================" << std::endl;

    return 0;
}
