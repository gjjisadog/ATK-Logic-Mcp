#include "atkdl16/capability.h"
#include <set>
#include <algorithm>

namespace atkdl16 {

static const std::set<uint64_t> STANDARD_SAMPLE_RATES = {
    1'000'000ULL,
    2'000'000ULL,
    4'000'000ULL,
    5'000'000ULL,
    10'000'000ULL,
    20'000'000ULL,
    25'000'000ULL,
    40'000'000ULL,
    50'000'000ULL,
    100'000'000ULL,
    200'000'000ULL,
    250'000'000ULL
};

static const std::set<uint64_t> PLUS_EXTENDED_RATES = {
    500'000'000ULL,
    1'000'000'000ULL
};

bool CapabilityManager::is_sample_rate_valid(DeviceModel model, size_t channel_count, CaptureMode mode, uint64_t rate_hz) {
    if (mode == CaptureMode::Stream) {
        // Stream bandwidth rule: rate * channels <= 320 Mbps
        if (rate_hz * channel_count > MAX_STREAM_BANDWIDTH_BPS) {
            return false;
        }
        return STANDARD_SAMPLE_RATES.count(rate_hz) > 0;
    }

    // Buffer mode
    if (STANDARD_SAMPLE_RATES.count(rate_hz) > 0) {
        return true;
    }

    if (model == DeviceModel::DL16Plus) {
        if (rate_hz == 500'000'000ULL) {
            return true; // Supported on all channels
        }
        if (rate_hz == 1'000'000'000ULL) {
            return channel_count <= MAX_PLUS_1GHZ_CHANNELS;
        }
    }

    return false;
}

uint64_t CapabilityManager::get_max_sample_rate(DeviceModel model, size_t channel_count, CaptureMode mode) {
    if (mode == CaptureMode::Stream) {
        if (channel_count <= 3) return 100'000'000ULL;
        if (channel_count <= 8) return 40'000'000ULL;
        if (channel_count <= 12) return 25'000'000ULL;
        return 20'000'000ULL;
    }

    // Buffer mode
    if (model == DeviceModel::DL16Plus) {
        return (channel_count <= MAX_PLUS_1GHZ_CHANNELS) ? 1'000'000'000ULL : 500'000'000ULL;
    }
    return 250'000'000ULL;
}

uint64_t CapabilityManager::get_max_depth_samples(DeviceModel model, size_t channel_count, CaptureMode mode, bool rle) {
    if (mode == CaptureMode::Stream) {
        return 1'000'000'000ULL; // Practically unbounded, host-memory limited
    }

    // Buffer mode memory limits
    uint64_t base_bits = (model == DeviceModel::DL16Plus) ? 3'500'000'000ULL : 1'000'000'000ULL;
    if (rle) {
        base_bits *= 10ULL; // Upstream RLE virtual expansion factor
    }
    return (channel_count > 0) ? (base_bits / channel_count) : 0;
}

Error CapabilityManager::validate(const DeviceInfo& device, const CaptureConfig& config) {
    // 1. Channel Validation
    if (config.enabled_channels.empty()) {
        return {ErrorCode::UnsupportedChannelCombination, "At least one channel must be enabled", true, "Specify at least one channel in channels list"};
    }
    if (config.enabled_channels.size() > TOTAL_CHANNELS) {
        return {ErrorCode::UnsupportedChannelCombination, "Cannot enable more than 16 channels", true, "Limit enabled channels to at most 16"};
    }

    std::set<uint8_t> unique_channels;
    for (uint8_t ch : config.enabled_channels) {
        if (ch >= TOTAL_CHANNELS) {
            return {ErrorCode::UnsupportedChannelCombination, "Channel ID out of range [0, 15]: " + std::to_string(ch), true, "Ensure all channel IDs are 0 to 15"};
        }
        if (!unique_channels.insert(ch).second) {
            return {ErrorCode::UnsupportedChannelCombination, "Duplicate channel ID in list: " + std::to_string(ch), true, "Remove duplicate channels"};
        }
    }

    const size_t num_ch = config.enabled_channels.size();
    DeviceModel model = (device.device_level == 1) ? DeviceModel::DL16Plus : DeviceModel::DL16;

    // 2. Sample Rate Validation
    if (!is_sample_rate_valid(model, num_ch, config.mode, config.sample_rate)) {
        if (config.mode == CaptureMode::Stream && config.sample_rate * num_ch > MAX_STREAM_BANDWIDTH_BPS) {
            return {ErrorCode::BandwidthExceeded,
                    "Stream bandwidth exceeded (rate * channels = " + std::to_string(config.sample_rate * num_ch / 1'000'000) + " Mbps > 320 Mbps limit)",
                    true, "Reduce sample rate or reduce enabled channels"};
        }
        if (config.sample_rate == 1'000'000'000ULL && num_ch > MAX_PLUS_1GHZ_CHANNELS) {
            return {ErrorCode::UnsupportedChannelCombination,
                    "1 GHz sampling is only supported with 8 or fewer channels",
                    true, "Reduce enabled channels to 8 or fewer to use 1 GHz"};
        }
        if ((config.sample_rate == 500'000'000ULL || config.sample_rate == 1'000'000'000ULL) && model != DeviceModel::DL16Plus) {
            return {ErrorCode::UnsupportedSampleRate,
                    "Rates above 250 MHz are only supported on ATK-DL16 Plus",
                    true, "Select sample rate <= 250 MHz for standard DL16"};
        }
        return {ErrorCode::UnsupportedSampleRate,
                "Unsupported sample rate: " + std::to_string(config.sample_rate) + " Hz",
                true, "Select a standard rate (e.g. 1M, 2M, 5M, 10M, 20M, 25M, 50M, 100M, 200M, 250M)"};
    }

    // 3. Threshold Voltage Validation
    if (config.threshold_voltage < -5.0 || config.threshold_voltage > 5.0) {
        return {ErrorCode::InvalidThreshold,
                "Threshold voltage " + std::to_string(config.threshold_voltage) + "V out of hardware range [-5.0V, +5.0V]",
                true, "Set threshold between -5.0V and +5.0V"};
    }

    // 4. Memory Capacity Validation
    uint64_t total_samples = config.total_samples();
    if (total_samples == 0) {
        return {ErrorCode::InvalidCaptureConfig, "Total sample count is zero", true, "Increase duration_ms or sample_rate"};
    }
    uint64_t max_depth = get_max_depth_samples(model, num_ch, config.mode, config.enable_rle);
    if (config.mode == CaptureMode::Buffer && total_samples > max_depth) {
        return {ErrorCode::CapacityExceeded,
                "Sampling depth " + std::to_string(total_samples) + " samples exceeds buffer RAM capacity (" + std::to_string(max_depth) + " samples)",
                true, "Reduce duration_ms or enable RLE compression"};
    }

    // 5. Trigger Validation
    if (config.trigger.position_percent < 0.0 || config.trigger.position_percent > 100.0) {
        return {ErrorCode::UnsupportedTrigger,
                "Trigger position percent " + std::to_string(config.trigger.position_percent) + "% must be between 0% and 100%",
                true, "Set trigger position between 0 and 100"};
    }
    for (const auto& trig : config.trigger.triggers) {
        if (unique_channels.count(trig.channel) == 0) {
            return {ErrorCode::UnsupportedTrigger,
                    "Trigger channel " + std::to_string(trig.channel) + " is not in enabled channels list",
                    true, "Enable channel " + std::to_string(trig.channel) + " in capture config"};
        }
    }

    return Error::ok();
}

} // namespace atkdl16
