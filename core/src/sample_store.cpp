#include "atkdl16/sample_store.h"
#include <fstream>
#include <sstream>
#include <iomanip>
#include <chrono>
#include <filesystem>
#include <cstring>
#include <algorithm>

namespace fs = std::filesystem;

namespace atkdl16 {

SampleStore::SampleStore(const CaptureConfig& config)
    : m_config(config) {
    for (uint8_t ch : config.enabled_channels) {
        m_channel_data[ch] = std::vector<uint8_t>();
        m_channel_sample_counts[ch] = 0;
        m_start_offsets[ch] = 0;
    }
}

void SampleStore::append_channel_data(uint8_t channel, const uint8_t* data, size_t length) {
    if (!data || length == 0) {
        return;
    }
    auto& buf = m_channel_data[channel];
    buf.insert(buf.end(), data, data + length);
    m_channel_sample_counts[channel] = buf.size() * 8ULL;
}

void SampleStore::apply_start_offsets(const std::map<uint8_t, uint64_t>& start_offsets) {
    for (const auto& [ch, offset_bits] : start_offsets) {
        m_start_offsets[ch] = offset_bits;
        auto it = m_channel_data.find(ch);
        if (it == m_channel_data.end() || it->second.empty()) {
            m_channel_sample_counts[ch] = 0;
            continue;
        }

        auto& buf = it->second;
        uint64_t orig_samples = m_channel_sample_counts[ch];
        if (offset_bits >= orig_samples) {
            buf.clear();
            m_channel_sample_counts[ch] = 0;
            continue;
        }

        size_t remove_bytes = static_cast<size_t>(offset_bits / 8ULL);
        size_t bit_shift = static_cast<size_t>(offset_bits % 8ULL);

        if (remove_bytes >= buf.size()) {
            buf.clear();
            m_channel_sample_counts[ch] = 0;
            continue;
        }

        if (remove_bytes > 0) {
            buf.erase(buf.begin(), buf.begin() + remove_bytes);
        }

        // Sub-byte bit alignment (bit 0 is early, bit 7 is late)
        if (bit_shift > 0 && !buf.empty()) {
            size_t n = buf.size();
            for (size_t i = 0; i < n - 1; ++i) {
                buf[i] = static_cast<uint8_t>((buf[i] >> bit_shift) | (buf[i + 1] << (8 - bit_shift)));
            }
            buf[n - 1] = static_cast<uint8_t>(buf[n - 1] >> bit_shift);
        }

        // Remaining valid samples count is EXACTLY orig_samples - offset_bits
        uint64_t remaining_samples = orig_samples - offset_bits;
        m_channel_sample_counts[ch] = remaining_samples;

        // Truncate trailing unused storage bytes
        size_t needed_bytes = static_cast<size_t>((remaining_samples + 7) / 8);
        if (buf.size() > needed_bytes) {
            buf.resize(needed_bytes);
        }
        // Zero out unused trailing padding bits in the last byte
        size_t rem_bits = remaining_samples % 8;
        if (rem_bits > 0 && !buf.empty()) {
            uint8_t mask = static_cast<uint8_t>((1 << rem_bits) - 1);
            buf.back() &= mask;
        }
    }
}

void SampleStore::finalize_samples(uint64_t target_samples) {
    for (auto& [ch, buf] : m_channel_data) {
        uint64_t valid_s = m_channel_sample_counts[ch];
        if (valid_s > target_samples) {
            valid_s = target_samples;
            m_channel_sample_counts[ch] = target_samples;
        }
        size_t target_bytes = static_cast<size_t>((valid_s + 7) / 8);
        if (buf.size() > target_bytes) {
            buf.resize(target_bytes);
        }
        // Zero out unused padding bits in the last byte
        size_t remaining_bits = valid_s % 8;
        if (remaining_bits > 0 && !buf.empty()) {
            uint8_t mask = static_cast<uint8_t>((1 << remaining_bits) - 1);
            buf.back() &= mask;
        }
    }
    m_is_finalized = true;
}

DataIntegrity SampleStore::validate_integrity(uint64_t requested_samples, std::vector<std::string>& warnings) const {
    bool complete = true;
    for (uint8_t ch : m_config.enabled_channels) {
        auto it = m_channel_sample_counts.find(ch);
        uint64_t count = (it != m_channel_sample_counts.end()) ? it->second : 0;
        if (count < requested_samples) {
            complete = false;
            warnings.push_back("Channel " + std::to_string(ch) + " has " +
                               std::to_string(count) + " valid samples (requested " +
                               std::to_string(requested_samples) + ")");
        }
    }
    return complete ? DataIntegrity::Complete : DataIntegrity::Incomplete;
}

uint8_t SampleStore::get_sample(uint8_t channel, uint64_t sample_index) const {
    auto it = m_channel_data.find(channel);
    if (it == m_channel_data.end()) {
        return 0;
    }
    const auto& buf = it->second;
    size_t byte_idx = static_cast<size_t>(sample_index / 8);
    if (byte_idx >= buf.size()) {
        return 0;
    }
    uint8_t bit_idx = static_cast<uint8_t>(sample_index % 8);
    return (buf[byte_idx] >> bit_idx) & 0x01;
}

uint64_t SampleStore::sample_count(uint8_t channel) const {
    auto it = m_channel_sample_counts.find(channel);
    return (it != m_channel_sample_counts.end()) ? it->second : 0;
}

const std::vector<uint8_t>& SampleStore::get_channel_bytes(uint8_t channel) const {
    static const std::vector<uint8_t> empty;
    auto it = m_channel_data.find(channel);
    return (it != m_channel_data.end()) ? it->second : empty;
}

ChannelEdges SampleStore::extract_edges(uint8_t channel, uint64_t start_index, uint64_t end_index) const {
    ChannelEdges result;
    result.channel = channel;

    auto it = m_channel_data.find(channel);
    if (it == m_channel_data.end() || it->second.empty()) {
        return result;
    }

    const auto& buf = it->second;
    uint64_t max_samples = sample_count(channel);
    if (start_index >= max_samples) {
        return result;
    }
    if (end_index == 0 || end_index > max_samples) {
        end_index = max_samples;
    }

    result.initial_level = get_sample(channel, start_index);
    uint8_t current_level = result.initial_level;

    size_t start_byte = static_cast<size_t>(start_index / 8);
    size_t end_byte = static_cast<size_t>((end_index + 7) / 8);
    end_byte = std::min(end_byte, buf.size());

    // Word-accelerated edge scan
    size_t byte_idx = start_byte;
    uint64_t current_sample = start_index;

    // Process leading unaligned bits
    size_t lead_bit = static_cast<size_t>(start_index % 8);
    if (lead_bit > 0 && byte_idx < end_byte) {
        uint8_t b = buf[byte_idx];
        for (size_t bit = lead_bit; bit < 8 && current_sample < end_index; ++bit, ++current_sample) {
            uint8_t lvl = (b >> bit) & 1;
            if (lvl != current_level) {
                result.edges.push_back({current_sample, lvl});
                current_level = lvl;
            }
        }
        ++byte_idx;
    }

    // Process aligned 64-bit blocks
    while (byte_idx + 8 <= end_byte && current_sample + 64 <= end_index) {
        uint64_t word;
        std::memcpy(&word, buf.data() + byte_idx, sizeof(uint64_t));

        if (word == 0 && current_level == 0) {
            byte_idx += 8;
            current_sample += 64;
            continue;
        }
        if (word == ~0ULL && current_level == 1) {
            byte_idx += 8;
            current_sample += 64;
            continue;
        }

        // Bit-by-bit inside word
        for (int i = 0; i < 64; ++i, ++current_sample) {
            uint8_t lvl = static_cast<uint8_t>((word >> i) & 1);
            if (lvl != current_level) {
                result.edges.push_back({current_sample, lvl});
                current_level = lvl;
            }
        }
        byte_idx += 8;
    }

    // Process trailing bytes
    while (byte_idx < end_byte && current_sample < end_index) {
        uint8_t b = buf[byte_idx];
        if (b == 0 && current_level == 0 && current_sample + 8 <= end_index) {
            ++byte_idx;
            current_sample += 8;
            continue;
        }
        if (b == 0xFF && current_level == 1 && current_sample + 8 <= end_index) {
            ++byte_idx;
            current_sample += 8;
            continue;
        }

        for (size_t bit = 0; bit < 8 && current_sample < end_index; ++bit, ++current_sample) {
            uint8_t lvl = (b >> bit) & 1;
            if (lvl != current_level) {
                result.edges.push_back({current_sample, lvl});
                current_level = lvl;
            }
        }
        ++byte_idx;
    }

    return result;
}

std::map<uint8_t, ChannelEdges> SampleStore::extract_all_edges() const {
    std::map<uint8_t, ChannelEdges> all_edges;
    for (uint8_t ch : m_config.enabled_channels) {
        all_edges[ch] = extract_edges(ch);
    }
    return all_edges;
}

bool SampleStore::save_to_directory(const std::string& directory_path, const std::string& capture_id, const CaptureResult* result) const {
    try {
        fs::path target_dir = fs::path(directory_path) / capture_id;
        fs::create_directories(target_dir);

        // 1. Write binary bit files per channel
        for (const auto& [ch, buf] : m_channel_data) {
            std::ostringstream filename;
            filename << "ch" << std::setw(2) << std::setfill('0') << static_cast<int>(ch) << ".bits";
            fs::path bit_path = target_dir / filename.str();

            std::ofstream out(bit_path, std::ios::binary);
            if (!out.is_open()) {
                return false;
            }
            out.write(reinterpret_cast<const char*>(buf.data()), buf.size());
        }

        // 2. Write edges.json
        auto all_edges = extract_all_edges();
        fs::path edges_path = target_dir / "edges.json";
        std::ofstream edges_out(edges_path);
        edges_out << "{\n";
        bool first_ch = true;
        for (const auto& [ch, ce] : all_edges) {
            if (!first_ch) edges_out << ",\n";
            first_ch = false;
            edges_out << "  \"" << static_cast<int>(ch) << "\": {\n";
            edges_out << "    \"initial_level\": " << static_cast<int>(ce.initial_level) << ",\n";
            edges_out << "    \"edge_count\": " << ce.edges.size() << ",\n";
            edges_out << "    \"edges\": [\n";
            for (size_t i = 0; i < ce.edges.size(); ++i) {
                edges_out << "      [" << ce.edges[i].sample_index << ", " << static_cast<int>(ce.edges[i].level) << "]";
                if (i + 1 < ce.edges.size()) edges_out << ",";
                edges_out << "\n";
            }
            edges_out << "    ]\n";
            edges_out << "  }";
        }
        edges_out << "\n}\n";

        // 3. Write meta.json
        fs::path meta_path = target_dir / "meta.json";
        std::ofstream meta_out(meta_path);
        auto now = std::chrono::system_clock::now();
        std::time_t now_time = std::chrono::system_clock::to_time_t(now);

        meta_out << "{\n";
        meta_out << "  \"capture_id\": \"" << capture_id << "\",\n";
        meta_out << "  \"timestamp\": " << now_time << ",\n";
        meta_out << "  \"mode\": \"" << (m_config.mode == CaptureMode::Buffer ? "buffer" : "stream") << "\",\n";
        meta_out << "  \"sample_rate\": " << m_config.sample_rate << ",\n";
        meta_out << "  \"duration_ms\": " << m_config.duration_ms << ",\n";
        meta_out << "  \"threshold_voltage\": " << m_config.threshold_voltage << ",\n";
        meta_out << "  \"rle_enabled\": " << (m_config.enable_rle ? "true" : "false") << ",\n";
        meta_out << "  \"trigger_position_percent\": " << m_config.trigger.position_percent << ",\n";
        if (result) {
            meta_out << "  \"evidence_source\": \"REAL_HARDWARE\",\n";
            meta_out << "  \"data_integrity\": \"" << to_string(result->data_integrity) << "\",\n";
            meta_out << "  \"requested_samples\": " << result->requested_samples << ",\n";
            meta_out << "  \"actual_samples\": " << result->actual_samples << ",\n";
            meta_out << "  \"trigger_offset\": " << result->trigger_offset << ",\n";
            meta_out << "  \"trigger_ack_received\": " << (result->trigger_ack_received ? "true" : "false") << ",\n";
            meta_out << "  \"capture_complete_received\": " << (result->capture_complete_received ? "true" : "false") << ",\n";
            meta_out << "  \"capacity_exceeded\": " << (result->capacity_exceeded ? "true" : "false") << ",\n";
            meta_out << "  \"bandwidth_exceeded\": " << (result->bandwidth_exceeded ? "true" : "false") << ",\n";
        }
        meta_out << "  \"channels\": [";
        for (size_t i = 0; i < m_config.enabled_channels.size(); ++i) {
            meta_out << static_cast<int>(m_config.enabled_channels[i]);
            if (i + 1 < m_config.enabled_channels.size()) meta_out << ", ";
        }
        meta_out << "],\n";
        meta_out << "  \"channel_names\": {\n";
        bool first_name = true;
        for (const auto& [ch, name] : m_config.channel_names) {
            if (!first_name) meta_out << ",\n";
            first_name = false;
            meta_out << "    \"" << static_cast<int>(ch) << "\": \"" << name << "\"";
        }
        meta_out << "\n  },\n";
        meta_out << "  \"sample_counts\": {\n";
        bool first_sc = true;
        for (const auto& [ch, cnt] : m_channel_sample_counts) {
            if (!first_sc) meta_out << ",\n";
            first_sc = false;
            meta_out << "    \"" << static_cast<int>(ch) << "\": " << cnt;
        }
        meta_out << "\n  }\n";
        meta_out << "}\n";

        return true;
    } catch (...) {
        return false;
    }
}

std::unique_ptr<SampleStore> SampleStore::load_from_directory(const std::string& directory_path) {
    fs::path dir(directory_path);
    fs::path meta_path = dir / "meta.json";
    if (!fs::exists(meta_path)) {
        return nullptr;
    }

    // Basic config parser
    CaptureConfig config;
    std::ifstream meta_in(meta_path);
    std::string line;
    while (std::getline(meta_in, line)) {
        if (line.find("\"sample_rate\":") != std::string::npos) {
            auto pos = line.find(':');
            config.sample_rate = std::stoull(line.substr(pos + 1));
        } else if (line.find("\"duration_ms\":") != std::string::npos) {
            auto pos = line.find(':');
            config.duration_ms = std::stoull(line.substr(pos + 1));
        } else if (line.find("\"threshold_voltage\":") != std::string::npos) {
            auto pos = line.find(':');
            config.threshold_voltage = std::stod(line.substr(pos + 1));
        }
    }

    // Discover channel bit files
    std::vector<uint8_t> channels;
    for (int ch = 0; ch < 16; ++ch) {
        std::ostringstream fn;
        fn << "ch" << std::setw(2) << std::setfill('0') << ch << ".bits";
        if (fs::exists(dir / fn.str())) {
            channels.push_back(static_cast<uint8_t>(ch));
        }
    }
    config.enabled_channels = channels;

    auto store = std::make_unique<SampleStore>(config);
    for (uint8_t ch : channels) {
        std::ostringstream fn;
        fn << "ch" << std::setw(2) << std::setfill('0') << static_cast<int>(ch) << ".bits";
        fs::path bit_path = dir / fn.str();
        std::ifstream bit_in(bit_path, std::ios::binary);
        if (bit_in.is_open()) {
            std::vector<uint8_t> buf(
                (std::istreambuf_iterator<char>(bit_in)),
                std::istreambuf_iterator<char>()
            );
            store->append_channel_data(ch, buf.data(), buf.size());
        }
    }
    store->finalize_samples(config.total_samples());
    return store;
}

} // namespace atkdl16
