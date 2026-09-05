#include "atkdl16/json_util.h"
#include <sstream>
#include <iomanip>
#include <cstdio>

namespace atkdl16 {

std::string json_escape(const std::string& input) {
    std::string out;
    out.reserve(input.size() + 16);
    for (size_t i = 0; i < input.size(); ++i) {
        unsigned char c = static_cast<unsigned char>(input[i]);
        switch (c) {
            case '\"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    char hex_buf[7];
                    std::snprintf(hex_buf, sizeof(hex_buf), "\\u%04x", static_cast<unsigned int>(c));
                    out += hex_buf;
                } else {
                    out.push_back(static_cast<char>(c));
                }
                break;
        }
    }
    return out;
}

std::string serialize_capture_result_json(const CaptureResult& result, const std::string& out_dir) {
    std::ostringstream ss;
    std::string artifact_path = out_dir.empty() ? result.capture_id : (result.capture_id.empty() ? out_dir : (out_dir + "/" + result.capture_id));
    if (!result.success) {
        ss << "{\n"
           << "  \"success\": false,\n"
           << "  \"error_code\": \"" << json_escape(to_string(result.error_code)) << "\",\n"
           << "  \"message\": \"" << json_escape(result.error_message) << "\",\n"
           << "  \"evidence_source\": \"REAL_HARDWARE\",\n"
           << "  \"data_integrity\": \"" << json_escape(to_string(result.data_integrity)) << "\",\n"
           << "  \"capture_complete_received\": " << (result.capture_complete_received ? "true" : "false") << ",\n"
           << "  \"artifact_dir\": \"" << json_escape(artifact_path) << "\",\n"
           << "  \"warnings\": [";
        for (size_t i = 0; i < result.warnings.size(); ++i) {
            ss << "\"" << json_escape(result.warnings[i]) << "\"";
            if (i + 1 < result.warnings.size()) ss << ", ";
        }
        ss << "]\n}\n";
        return ss.str();
    }

    if (!result.capture_id.empty() && !out_dir.empty()) {
        artifact_path = out_dir + "/" + result.capture_id;
    }
    ss << "{\n"
       << "  \"success\": true,\n"
       << "  \"capture_id\": \"" << json_escape(result.capture_id) << "\",\n"
       << "  \"evidence_source\": \"REAL_HARDWARE\",\n"
       << "  \"data_integrity\": \"" << json_escape(to_string(result.data_integrity)) << "\",\n"
       << "  \"requested_samples\": " << result.requested_samples << ",\n"
       << "  \"minimum_actual_samples\": " << result.minimum_actual_samples << ",\n"
       << "  \"actual_samples_per_channel\": {\n";
    bool first_ch = true;
    for (const auto& [ch, cnt] : result.actual_samples_per_channel) {
        if (!first_ch) ss << ",\n";
        first_ch = false;
        ss << "    \"" << static_cast<int>(ch) << "\": " << cnt;
    }
    ss << "\n  },\n"
       << "  \"trigger_ack_received\": " << (result.trigger_ack_received ? "true" : "false") << ",\n"
       << "  \"trigger_offset_received\": " << (result.trigger_offset_received ? "true" : "false") << ",\n"
       << "  \"capture_complete_received\": " << (result.capture_complete_received ? "true" : "false") << ",\n"
       << "  \"capacity_exceeded\": " << (result.capacity_exceeded ? "true" : "false") << ",\n"
       << "  \"bandwidth_exceeded\": " << (result.bandwidth_exceeded ? "true" : "false") << ",\n"
       << "  \"artifact_dir\": \"" << json_escape(artifact_path) << "\",\n"
       << "  \"warnings\": [";
    for (size_t i = 0; i < result.warnings.size(); ++i) {
        ss << "\"" << json_escape(result.warnings[i]) << "\"";
        if (i + 1 < result.warnings.size()) ss << ", ";
    }
    ss << "]\n}\n";
    return ss.str();
}

} // namespace atkdl16
