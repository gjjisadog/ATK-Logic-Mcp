#pragma once

#include "types.h"
#include <string>

namespace atkdl16 {

class CapabilityManager {
public:
    // Validate whether a capture configuration is supported by the specific hardware
    static Error validate(const DeviceInfo& device, const CaptureConfig& config);

    // Get max allowed sample rate for a given channel count and mode
    static uint64_t get_max_sample_rate(DeviceModel model, size_t channel_count, CaptureMode mode);

    // Get maximum buffer depth in samples for a given mode and RLE state
    static uint64_t get_max_depth_samples(DeviceModel model, size_t channel_count, CaptureMode mode, bool rle);

    // Check if a specific sample rate is supported
    static bool is_sample_rate_valid(DeviceModel model, size_t channel_count, CaptureMode mode, uint64_t rate_hz);
};

} // namespace atkdl16
