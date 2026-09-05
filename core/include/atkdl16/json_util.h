#pragma once

#include "atkdl16/types.h"
#include <string>

namespace atkdl16 {

/**
 * Escapes special characters in input string according to RFC 8259 JSON specification:
 * - Quotation mark (") -> \"
 * - Reverse solidus (\) -> \\
 * - Control characters:
 *   - Backspace (\b) -> \b
 *   - Form feed (\f) -> \f
 *   - Line feed (\n) -> \n
 *   - Carriage return (\r) -> \r
 *   - Tab (\t) -> \t
 *   - 0x00..0x1F -> \u00xx
 */
std::string json_escape(const std::string& input);

/**
 * Serializes a CaptureResult object into a valid, standards-compliant JSON string.
 * All string fields (capture_id, message, error_code, warnings, artifact_dir)
 * are escaped using json_escape() so Windows backslashes and special characters
 * are guaranteed valid when parsed by json.loads().
 */
std::string serialize_capture_result_json(const CaptureResult& result, const std::string& out_dir);

} // namespace atkdl16
