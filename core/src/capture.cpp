#include "atkdl16/capture.h"
#include "atkdl16/capability.h"
#include "atkdl16/protocol.h"
#include <chrono>
#include <thread>
#include <sstream>
#include <iomanip>
#include <random>

namespace atkdl16 {

static std::string generate_capture_id() {
    auto now = std::chrono::system_clock::now();
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();
    static std::mt19937 gen(std::random_device{}());
    std::uniform_int_distribution<int> dis(1000, 9999);

    std::ostringstream ss;
    ss << "cap_" << ms << "_" << dis(gen);
    return ss.str();
}

CaptureEngine::CaptureEngine(Device& device)
    : m_device(device), m_state(CaptureState::Disconnected) {}

CaptureEngine::~CaptureEngine() {
    stop();
}

void CaptureEngine::stop() {
    m_stop_requested = true;
    if (m_state == CaptureState::Capturing || m_state == CaptureState::Draining) {
        m_state = CaptureState::Stopping;
        auto stop_frame = build_stop_frame();
        m_device.transport().write_bulk(EP_BULK_OUT, stop_frame.data(), stop_frame.size(), 100);
    }
}

CaptureResult CaptureEngine::execute_capture(const CaptureConfig& config,
                                            const std::string& capture_dir,
                                            ProgressCallback on_progress,
                                            int timeout_sec) {
    CaptureResult result;
    result.capture_id = generate_capture_id();
    result.config = config;
    result.success = false;
    m_stop_requested = false;

    if (!m_device.is_open()) {
        result.error_message = "Logic analyzer device is not open";
        return result;
    }

    // 1. Hardware Capability Validation
    auto val_err = CapabilityManager::validate(m_device.info(), config);
    if (!val_err) {
        result.error_message = "Capability validation failed: " + val_err.message;
        return result;
    }

    // 2. FLUSH_RX State
    m_state = CaptureState::FlushRx;
    m_device.wake_fpga();
    m_device.flush_buffers();

    // 3. CONFIGURE State
    m_state = CaptureState::Configure;
    auto set_frame = build_parameter_setting_frame(config);
    auto err = m_device.transport().write_bulk(EP_BULK_OUT, set_frame.data(), set_frame.size(), 200);
    if (!err) {
        m_state = CaptureState::Error;
        result.error_message = "Failed to send ParameterSetting command: " + err.message;
        return result;
    }

    // 4. SETTLE State (Mandatory 30ms hardware DAC settling delay)
    m_state = CaptureState::Settle;
    std::this_thread::sleep_for(std::chrono::milliseconds(FPGA_DAC_SETTLING_DELAY_MS));

    // 5. ARM_TRIGGER State: Send SimpleTrigger and wait for Order 4 ACK (cmd 0x12, status == 3)
    m_state = CaptureState::ArmTrigger;
    auto trig_frame = build_simple_trigger_frame(config);
    err = m_device.transport().write_bulk(EP_BULK_OUT, trig_frame.data(), trig_frame.size(), 200);
    if (!err) {
        m_state = CaptureState::Error;
        result.error_code = ErrorCode::UsbTransferError;
        result.error_message = "Failed to send SimpleTrigger command: " + err.message;
        return result;
    }

    SampleStore store(config);
    RxParser parser;
    std::vector<uint8_t> raw_buf(READ_TRANSFER_BUFFER_SIZE);
    std::vector<uint8_t> conv_buf(READ_TRANSFER_BUFFER_SIZE);

    // 6. CAPTURING State: Unified RX message dispatch loop
    m_state = CaptureState::Capturing;

    std::map<uint8_t, uint64_t> channel_start_offsets;
    bool received_trigger_offset = false;
    bool capture_complete = false;
    uint64_t target_samples = config.total_samples();
    uint64_t last_progress_samples = 0;

    auto start_time = std::chrono::steady_clock::now();
    auto timeout_duration = std::chrono::seconds(timeout_sec);

    while (!m_stop_requested && !capture_complete) {
        // Check timeout
        auto elapsed = std::chrono::steady_clock::now() - start_time;
        if (elapsed > timeout_duration) {
            stop();
            m_state = CaptureState::Error;
            result.error_code = ErrorCode::CaptureTimeout;
            result.error_message = "Capture timed out waiting for trigger or completion";
            return result;
        }

        size_t actual_len = 0;
        err = m_device.transport().read_bulk(EP_BULK_IN, raw_buf.data(), raw_buf.size(), actual_len, 200);
        if (!err && err.code == ErrorCode::DeviceDisconnected) {
            m_state = CaptureState::Error;
            result.error_code = ErrorCode::DeviceDisconnected;
            result.error_message = "Device disconnected during capture";
            return result;
        }

        if (actual_len >= USB_BLOCK_SIZE) {
            size_t aligned_len = (actual_len / USB_BLOCK_SIZE) * USB_BLOCK_SIZE;
            if (conv_buf.size() < aligned_len) {
                conv_buf.resize(aligned_len);
            }
            convert_to_pc(raw_buf.data(), conv_buf.data(), aligned_len);
            parser.push_bytes(conv_buf.data(), aligned_len);

            // Process parsed frames
            while (parser.has_message()) {
                auto msg_opt = parser.pop_message();
                if (!msg_opt) break;
                const auto& msg = *msg_opt;

                switch (msg.type) {
                    case RxMessageType::ChannelData: {
                        auto ch_data = RxParser::parse_channel_data(msg, config.enable_rle);
                        if (ch_data) {
                            store.append_channel_data(ch_data->channel_id, ch_data->data.data(), ch_data->data.size());
                        }
                        break;
                    }
                    case RxMessageType::TriggerOffset: {
                        if (received_trigger_offset) {
                            result.warnings.push_back("Duplicate TriggerOffset (Order 3) packet received");
                            break;
                        }
                        auto trig_opt = RxParser::parse_trigger_offset(msg, TOTAL_CHANNELS, config.enable_rle);
                        if (trig_opt) {
                            result.trigger_offset = trig_opt->trigger_sample_offset;
                            result.trigger_offset_received = true;

                            if (trig_opt->exceed_capacity) {
                                result.capacity_exceeded = true;
                                result.data_integrity = DataIntegrity::Overflow;
                                result.error_code = ErrorCode::CapacityExceeded;
                                result.error_message = "Capture exceeded on-device memory capacity";
                                m_state = CaptureState::Error;
                                return result;
                            }
                            if (trig_opt->exceed_bandwidth) {
                                result.bandwidth_exceeded = true;
                                result.data_integrity = DataIntegrity::Overflow;
                                result.error_code = ErrorCode::BandwidthExceeded;
                                result.error_message = "Capture exceeded USB streaming bandwidth limit";
                                m_state = CaptureState::Error;
                                return result;
                            }

                            for (size_t i = 0; i < trig_opt->channel_byte_counts.size(); ++i) {
                                uint64_t total_bits = trig_opt->channel_byte_counts[i] * 8ULL;
                                if (total_bits > target_samples) {
                                    uint64_t trig_depth = config.trigger_sample_index();
                                    if (total_bits > (trig_depth + result.trigger_offset)) {
                                        channel_start_offsets[static_cast<uint8_t>(i)] =
                                            total_bits - trig_depth - result.trigger_offset;
                                    }
                                }
                            }
                            received_trigger_offset = true;
                            m_state = CaptureState::Draining;
                        }
                        break;
                    }
                    case RxMessageType::Progress: {
                        auto prog = RxParser::parse_progress(msg);
                        if (prog) {
                            last_progress_samples = prog->captured_samples;
                            if (on_progress) {
                                uint8_t pct = static_cast<uint8_t>(std::min(100ULL, (last_progress_samples * 100ULL) / target_samples));
                                on_progress(last_progress_samples, target_samples, pct);
                            }
                        }
                        break;
                    }
                    case RxMessageType::Ack: {
                        auto ack_opt = RxParser::parse_ack(msg);
                        if (ack_opt && ack_opt->command_code == static_cast<uint8_t>(CommandCode::SimpleTrigger)) {
                            if (ack_opt->status == 3) {
                                result.trigger_ack_received = true;
                            } else {
                                m_state = CaptureState::Error;
                                result.error_code = ErrorCode::ProtocolError;
                                result.error_message = "SimpleTrigger ACK rejected by FPGA with status " + std::to_string(ack_opt->status);
                                return result;
                            }
                        }
                        break;
                    }
                    case RxMessageType::Complete: {
                        capture_complete = true;
                        result.capture_complete_received = true;
                        m_state = CaptureState::Complete;
                        break;
                    }
                    default:
                        break;
                }
            }
        }
    }

    if (!capture_complete) {
        result.capture_complete_received = false;
        result.data_integrity = DataIntegrity::Incomplete;
        result.success = false;
        result.error_code = m_stop_requested ? ErrorCode::Ok : ErrorCode::CaptureTimeout;
        result.error_message = m_stop_requested ? "Capture was cancelled by user before completion" : "Capture timed out before Order 6 Complete received";
        m_state = CaptureState::Error;
        return result;
    }

    if (!result.trigger_ack_received) {
        result.data_integrity = DataIntegrity::Incomplete;
        result.success = false;
        result.error_code = ErrorCode::ProtocolError;
        result.error_message = "Capture completed without SimpleTrigger confirmation (Order 4 ACK)";
        m_state = CaptureState::Error;
        return result;
    }

    // 7. Post-processing: Crop and Align Samples
    if (received_trigger_offset) {
        store.apply_start_offsets(channel_start_offsets);
    } else if (config.mode == CaptureMode::Buffer && config.trigger.position_percent > 0.0 && !config.trigger.is_instantly) {
        result.warnings.push_back("TriggerOffset packet (Order 3) not received; pre-trigger offset alignment skipped");
    }
    store.finalize_samples(target_samples);

    // 8. Verify data completeness across all enabled channels
    result.requested_samples = target_samples;
    uint64_t min_samples = UINT64_MAX;
    bool all_channels_complete = true;

    for (uint8_t ch : config.enabled_channels) {
        uint64_t ch_samples = store.sample_count(ch);
        result.actual_samples_per_channel[ch] = ch_samples;
        if (ch_samples < min_samples) {
            min_samples = ch_samples;
        }
        if (ch_samples < target_samples) {
            all_channels_complete = false;
            result.warnings.push_back("Channel " + std::to_string(ch) + " is incomplete: received " +
                                      std::to_string(ch_samples) + " / " + std::to_string(target_samples) + " samples");
        }
    }
    result.minimum_actual_samples = (min_samples == UINT64_MAX) ? 0 : min_samples;
    result.actual_samples = result.minimum_actual_samples;

    if (!all_channels_complete) {
        result.data_integrity = DataIntegrity::Incomplete;
        result.success = false;
        result.error_code = ErrorCode::IncompleteCapture;
        result.error_message = "Capture incomplete: one or more channels did not receive requested sample count";
        m_state = CaptureState::Error;
        return result;
    }

    result.data_integrity = DataIntegrity::Complete;
    result.channel_edges = store.extract_all_edges();

    bool save_ok = store.save_to_directory(capture_dir, result.capture_id, &result);
    if (!save_ok) {
        result.data_integrity = DataIntegrity::Incomplete;
        result.success = false;
        result.error_code = ErrorCode::ArtifactWriteError;
        result.error_message = "Failed to write capture artifacts to directory: " + capture_dir;
        m_state = CaptureState::Error;
        return result;
    }

    result.success = true;

    m_state = CaptureState::DeviceReady;
    return result;
}

} // namespace atkdl16
