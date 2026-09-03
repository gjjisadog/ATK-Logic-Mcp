#pragma once

#include "types.h"
#include "device.h"
#include "rx_parser.h"
#include "sample_store.h"
#include <string>
#include <functional>
#include <atomic>
#include <memory>

namespace atkdl16 {

using ProgressCallback = std::function<void(uint64_t current_samples, uint64_t total_samples, uint8_t percent)>;

class CaptureEngine {
public:
    explicit CaptureEngine(Device& device);
    ~CaptureEngine();

    // Execute capture according to config
    // Adheres strictly to the state machine sequence:
    // FLUSH_RX -> CONFIGURE (0x11) -> SETTLE (30ms) -> ARM_TRIGGER (0x12) -> CAPTURING -> DRAINING -> COMPLETE
    CaptureResult execute_capture(const CaptureConfig& config,
                                  const std::string& capture_dir = "captures",
                                  ProgressCallback on_progress = nullptr,
                                  int timeout_sec = 30);

    // Cancel active capture
    void stop();

    // Current state
    CaptureState state() const noexcept { return m_state; }

private:
    Device& m_device;
    std::atomic<CaptureState> m_state{CaptureState::Disconnected};
    std::atomic<bool> m_stop_requested{false};
};

} // namespace atkdl16
