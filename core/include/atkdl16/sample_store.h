#pragma once

#include "types.h"
#include <vector>
#include <map>
#include <string>
#include <cstdint>
#include <memory>

namespace atkdl16 {

class SampleStore {
public:
    explicit SampleStore(const CaptureConfig& config);

    // Append newly arrived raw sample bytes for a specific channel
    void append_channel_data(uint8_t channel, const uint8_t* data, size_t length);

    // Apply hardware trigger offset and pre-trigger cropping
    // start_offsets: map from channel ID to sample offset count
    void apply_start_offsets(const std::map<uint8_t, uint64_t>& start_offsets);

    // Crop or clamp all channel sample buffers to target_samples
    void finalize_samples(uint64_t target_samples);

    // Query sample logic level (0 or 1) at specific sample index
    uint8_t get_sample(uint8_t channel, uint64_t sample_index) const;

    // Get total valid sample count for a channel
    uint64_t sample_count(uint8_t channel) const;

    // Retrieve raw bit-packed buffer for a channel
    const std::vector<uint8_t>& get_channel_bytes(uint8_t channel) const;

    // Deterministic edge extraction (transitions from 0->1 or 1->0)
    ChannelEdges extract_edges(uint8_t channel, uint64_t start_index = 0, uint64_t end_index = 0) const;

    // Extract edges for all enabled channels
    std::map<uint8_t, ChannelEdges> extract_all_edges() const;

    // Validate data completeness across all enabled channels
    DataIntegrity validate_integrity(uint64_t requested_samples, std::vector<std::string>& warnings) const;

    // Save capture artifacts to target directory:
    // captures/<capture-id>/
    //   meta.json
    //   ch00.bits, ch01.bits...
    //   edges.json
    bool save_to_directory(const std::string& directory_path, const std::string& capture_id, const CaptureResult* result = nullptr) const;

    // Load capture from directory for replay/inspection
    static std::unique_ptr<SampleStore> load_from_directory(const std::string& directory_path);

    // Load capture from an official .atkdl archive file
    static std::unique_ptr<SampleStore> load_from_atkdl(const std::string& atkdl_path);

    const CaptureConfig& config() const noexcept { return m_config; }

private:
    CaptureConfig m_config;
    std::map<uint8_t, std::vector<uint8_t>> m_channel_data;
    std::map<uint8_t, uint64_t> m_channel_sample_counts;
    std::map<uint8_t, uint64_t> m_start_offsets;
    bool m_is_finalized{false};
};

} // namespace atkdl16
