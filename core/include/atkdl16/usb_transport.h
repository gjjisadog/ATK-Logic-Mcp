#pragma once

#include "types.h"
#include "constants.h"
#include <vector>
#include <memory>
#include <functional>
#include <mutex>

// Forward declare libusb structs to avoid exposing libusb.h in public header
struct libusb_context;
struct libusb_device;
struct libusb_device_handle;
struct libusb_transfer;

namespace atkdl16 {

class UsbTransport {
public:
    UsbTransport();
    ~UsbTransport();

    // Prevent copying
    UsbTransport(const UsbTransport&) = delete;
    UsbTransport& operator=(const UsbTransport&) = delete;

    // Initialize libusb context
    Error init();

    // Enumerate connected DL16 devices
    std::vector<DeviceInfo> enumerate();

    // Open connection to device at port/index
    Error open(int port = -1);

    // Close active USB handle
    void close();

    // Check connection status
    bool is_open() const noexcept;

    // Synchronous raw transfer methods
    Error write_bulk(uint8_t endpoint, const uint8_t* data, size_t length, int timeout_ms = DEFAULT_USB_TIMEOUT_MS);
    Error read_bulk(uint8_t endpoint, uint8_t* buffer, size_t length, size_t& actual_length, int timeout_ms = DEFAULT_USB_TIMEOUT_MS);

    // Synchronous framed command transmission (handles padding, CRC32, device conversion)
    Error send_command(CommandCode code, const uint8_t* payload = nullptr, size_t payload_len = 0);

    // Asynchronous capture stream management
    using DataCallback = std::function<void(const uint8_t* data, size_t length)>;
    using ErrorCallback = std::function<void(ErrorCode code, const std::string& msg)>;

    Error start_async_read(DataCallback on_data, ErrorCallback on_error);
    void stop_async_read();
    bool is_async_reading() const noexcept;

    // File/mutex lock management for multi-process safety
    static bool try_acquire_device_lock(int port);
    static void release_device_lock(int port);

private:
    libusb_context* m_ctx{nullptr};
    libusb_device_handle* m_handle{nullptr};
    libusb_device* m_dev{nullptr};
    int m_port{-1};
    bool m_is_open{false};
    bool m_async_active{false};
    void* m_lock_handle{nullptr};

    mutable std::recursive_mutex m_mutex;
    struct AsyncContext;
    std::unique_ptr<AsyncContext> m_async_ctx;
};

} // namespace atkdl16
