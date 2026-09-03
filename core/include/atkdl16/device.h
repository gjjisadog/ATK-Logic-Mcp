#pragma once

#include "types.h"
#include "usb_transport.h"
#include <memory>
#include <vector>

namespace atkdl16 {

class Device {
public:
    Device();
    ~Device();

    // Enumerate connected DL16 devices on host
    static std::vector<DeviceInfo> enumerate();

    // Open connection to device
    Error open(int port = -1);

    // Close connection
    void close();

    // Query comprehensive device information (MCU, FPGA, Level, Speed, Model)
    Error query_info(DeviceInfo& info);

    // Reset capture / wake FPGA
    Error wake_fpga();
    Error sleep_fpga();

    // Flush any pending data in USB FIFOs
    Error flush_buffers();

    // Accessors
    bool is_open() const noexcept;
    const DeviceInfo& info() const noexcept { return m_info; }
    UsbTransport& transport() noexcept { return *m_transport; }

private:
    std::unique_ptr<UsbTransport> m_transport;
    DeviceInfo m_info;
    bool m_is_open{false};
};

} // namespace atkdl16
