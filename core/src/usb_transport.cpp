#include "atkdl16/usb_transport.h"
#include "atkdl16/protocol.h"
#include "libusb.h"
#include <thread>
#include <atomic>
#include <iostream>
#include <fstream>
#include <filesystem>

#if defined(_WIN32)
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

namespace fs = std::filesystem;

namespace atkdl16 {

struct UsbTransport::AsyncContext {
    std::thread event_thread;
    std::thread read_worker;
    std::atomic<bool> running{false};
    DataCallback on_data;
    ErrorCallback on_error;
    std::vector<libusb_transfer*> transfers;
    std::vector<std::vector<uint8_t>> raw_buffers;
    std::vector<std::vector<uint8_t>> converted_buffers;
};

UsbTransport::UsbTransport() = default;

UsbTransport::~UsbTransport() {
    close();
    if (m_ctx) {
        libusb_exit(m_ctx);
        m_ctx = nullptr;
    }
}

Error UsbTransport::init() {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    if (m_ctx) {
        return Error::ok();
    }
    int res = libusb_init(&m_ctx);
    if (res != LIBUSB_SUCCESS) {
        return {ErrorCode::UsbOpenFailed,
                "Failed to initialize libusb: " + std::string(libusb_error_name(res)),
                false, "Verify libusb driver installation"};
    }
    return Error::ok();
}

std::vector<DeviceInfo> UsbTransport::enumerate() {
    std::vector<DeviceInfo> devices;
    if (!m_ctx) {
        auto err = init();
        if (!err) return devices;
    }

    libusb_device** list = nullptr;
    ssize_t count = libusb_get_device_list(m_ctx, &list);
    if (count < 0) {
        return devices;
    }

    for (ssize_t i = 0; i < count; ++i) {
        libusb_device* dev = list[i];
        libusb_device_descriptor desc;
        if (libusb_get_device_descriptor(dev, &desc) == LIBUSB_SUCCESS) {
            if (desc.idVendor == VENDOR_ID && desc.idProduct == PRODUCT_ID) {
                DeviceInfo info;
                info.vid = desc.idVendor;
                info.pid = desc.idProduct;
                info.port_number = libusb_get_port_number(dev);
                info.model = DeviceModel::DL16; // Preliminary, detailed in query_info
                devices.push_back(info);
            }
        }
    }

    libusb_free_device_list(list, 1);
    return devices;
}

bool UsbTransport::try_acquire_device_lock(int port) {
#if defined(_WIN32)
    std::string mutex_name = "Local\\ATK_DL16_PORT_" + std::to_string(port);
    HANDLE hMutex = CreateMutexA(NULL, TRUE, mutex_name.c_str());
    if (hMutex == NULL || GetLastError() == ERROR_ALREADY_EXISTS) {
        if (hMutex) CloseHandle(hMutex);
        return false;
    }
    CloseHandle(hMutex);
    return true;
#else
    std::string lock_path = "/tmp/atk_dl16_port_" + std::to_string(port) + ".lock";
    int fd = ::open(lock_path.c_str(), O_CREAT | O_RDWR, 0666);
    if (fd < 0) return false;
    return (flock(fd, LOCK_EX | LOCK_NB) == 0);
#endif
}

void UsbTransport::release_device_lock(int port) {
    // Managed via RAII m_lock_handle in close()
}

Error UsbTransport::open(int port) {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    if (m_is_open) {
        return Error::ok();
    }
    if (!m_ctx) {
        auto err = init();
        if (!err) {
            return err;
        }
    }

    libusb_device** list = nullptr;
    ssize_t count = libusb_get_device_list(m_ctx, &list);
    if (count < 0) {
        return {ErrorCode::DeviceNotFound, "No USB devices found", false, "Connect DL16 device"};
    }

    libusb_device* target_dev = nullptr;
    for (ssize_t i = 0; i < count; ++i) {
        libusb_device_descriptor desc;
        if (libusb_get_device_descriptor(list[i], &desc) == LIBUSB_SUCCESS) {
            if (desc.idVendor == VENDOR_ID && desc.idProduct == PRODUCT_ID) {
                int dev_port = libusb_get_port_number(list[i]);
                if (port == -1 || port == dev_port) {
                    target_dev = list[i];
                    libusb_ref_device(target_dev);
                    m_port = dev_port;
                    break;
                }
            }
        }
    }

    libusb_free_device_list(list, 1);

    if (!target_dev) {
        return {ErrorCode::DeviceNotFound, "ATK-DL16 device not found on USB bus", true, "Check USB connection and power"};
    }

#if defined(_WIN32)
    std::string mutex_name = "Local\\ATK_DL16_PORT_" + std::to_string(m_port);
    HANDLE hMutex = CreateMutexA(NULL, TRUE, mutex_name.c_str());
    if (hMutex == NULL || GetLastError() == ERROR_ALREADY_EXISTS) {
        if (hMutex) CloseHandle(hMutex);
        libusb_unref_device(target_dev);
        return {ErrorCode::DeviceBusy,
                "Device on port " + std::to_string(m_port) + " is claimed by another process (e.g. ATK-Logic GUI)",
                true, "Close ATK-Logic GUI or another daemon instance"};
    }
    m_lock_handle = hMutex;
#endif

    int res = libusb_open(target_dev, &m_handle);
    libusb_unref_device(target_dev);

    if (res != LIBUSB_SUCCESS || !m_handle) {
#if defined(_WIN32)
        if (m_lock_handle) {
            CloseHandle(static_cast<HANDLE>(m_lock_handle));
            m_lock_handle = nullptr;
        }
#endif
        if (res == LIBUSB_ERROR_ACCESS) {
            return {ErrorCode::DeviceBusy,
                    "Device on port " + std::to_string(m_port) + " is in use by another application (e.g. ATK-Logic GUI)",
                    true, "Close ATK-Logic GUI to release the USB interface"};
        }
        return {ErrorCode::UsbOpenFailed,
                "Failed to open USB device: " + std::string(libusb_error_name(res)),
                true, "Check permissions or reconnect device"};
    }

    res = libusb_claim_interface(m_handle, 0);
    if (res != LIBUSB_SUCCESS) {
        libusb_close(m_handle);
        m_handle = nullptr;
#if defined(_WIN32)
        if (m_lock_handle) {
            CloseHandle(static_cast<HANDLE>(m_lock_handle));
            m_lock_handle = nullptr;
        }
#endif
        if (res == LIBUSB_ERROR_BUSY || res == LIBUSB_ERROR_ACCESS) {
            return {ErrorCode::DeviceBusy,
                    "Interface 0 is currently claimed by another process (e.g. ATK-Logic GUI)",
                    true, "Close ATK-Logic GUI to release interface"};
        }
        return {ErrorCode::UsbClaimFailed,
                "Failed to claim interface 0: " + std::string(libusb_error_name(res)),
                true, "Verify WinUSB driver configuration"};
    }

    m_is_open = true;
    return Error::ok();
}

void UsbTransport::close() {
    std::lock_guard<std::recursive_mutex> lock(m_mutex);
    if (m_async_active) {
        stop_async_read();
    }
    if (m_handle) {
        libusb_release_interface(m_handle, 0);
        libusb_close(m_handle);
        m_handle = nullptr;
    }
#if defined(_WIN32)
    if (m_lock_handle) {
        ReleaseMutex(static_cast<HANDLE>(m_lock_handle));
        CloseHandle(static_cast<HANDLE>(m_lock_handle));
        m_lock_handle = nullptr;
    }
#endif
    m_is_open = false;
    m_port = -1;
}

bool UsbTransport::is_open() const noexcept {
    return m_is_open && m_handle != nullptr;
}

Error UsbTransport::write_bulk(uint8_t endpoint, const uint8_t* data, size_t length, int timeout_ms) {
    if (!is_open()) {
        return {ErrorCode::DeviceDisconnected, "Device not open", false, "Open device first"};
    }
    int transferred = 0;
    int res = libusb_bulk_transfer(m_handle, endpoint, const_cast<uint8_t*>(data), static_cast<int>(length), &transferred, timeout_ms);
    if (res != LIBUSB_SUCCESS) {
        return {ErrorCode::UsbTransferError,
                "Bulk write failed: " + std::string(libusb_error_name(res)),
                true, "Retry command or check USB link"};
    }
    return Error::ok();
}

Error UsbTransport::read_bulk(uint8_t endpoint, uint8_t* buffer, size_t length, size_t& actual_length, int timeout_ms) {
    actual_length = 0;
    if (!is_open()) {
        return {ErrorCode::DeviceDisconnected, "Device not open", false, "Open device first"};
    }
    int transferred = 0;
    int res = libusb_bulk_transfer(m_handle, endpoint, buffer, static_cast<int>(length), &transferred, timeout_ms);
    actual_length = (transferred > 0) ? static_cast<size_t>(transferred) : 0;

    if (res == LIBUSB_SUCCESS) {
        return Error::ok();
    }
    if (res == LIBUSB_ERROR_TIMEOUT) {
        return Error::ok(); // Non-fatal timeout, actual_length recorded
    }
    if (res == LIBUSB_ERROR_NO_DEVICE) {
        close();
        return {ErrorCode::DeviceDisconnected, "Device disconnected during read", false, "Reconnect device"};
    }
    return {ErrorCode::UsbTransferError, "Bulk read failed: " + std::string(libusb_error_name(res)), true, "Retry read"};
}

Error UsbTransport::send_command(CommandCode code, const uint8_t* payload, size_t payload_len) {
    auto frame = build_device_frame(code, payload, payload_len);
    return write_bulk(EP_BULK_OUT, frame.data(), frame.size(), 200);
}

Error UsbTransport::start_async_read(DataCallback on_data, ErrorCallback on_error) {
    if (!is_open()) {
        return {ErrorCode::DeviceDisconnected, "Device not open", false, "Open device first"};
    }
    if (m_async_active) {
        return Error::ok();
    }

    m_async_ctx = std::make_unique<AsyncContext>();
    m_async_ctx->on_data = std::move(on_data);
    m_async_ctx->on_error = std::move(on_error);
    m_async_ctx->running = true;

    m_async_active = true;

    // Launch worker thread using synchronous read_bulk in loop with deinterleaving
    m_async_ctx->read_worker = std::thread([this]() {
        std::vector<uint8_t> raw_buf(READ_TRANSFER_BUFFER_SIZE);
        std::vector<uint8_t> conv_buf(READ_TRANSFER_BUFFER_SIZE);

        while (m_async_ctx && m_async_ctx->running) {
            size_t actual = 0;
            auto err = read_bulk(EP_BULK_IN, raw_buf.data(), raw_buf.size(), actual, CAPTURE_READ_TIMEOUT_MS);
            if (!err) {
                if (err.code == ErrorCode::DeviceDisconnected) {
                    if (m_async_ctx->on_error) {
                        m_async_ctx->on_error(ErrorCode::DeviceDisconnected, "Device was disconnected");
                    }
                    break;
                }
            }

            if (actual >= USB_BLOCK_SIZE) {
                size_t aligned_len = (actual / USB_BLOCK_SIZE) * USB_BLOCK_SIZE;
                if (conv_buf.size() < aligned_len) {
                    conv_buf.resize(aligned_len);
                }
                convert_to_pc(raw_buf.data(), conv_buf.data(), aligned_len);

                if (m_async_ctx->on_data) {
                    m_async_ctx->on_data(conv_buf.data(), aligned_len);
                }
            }
        }
    });

    return Error::ok();
}

void UsbTransport::stop_async_read() {
    if (!m_async_active || !m_async_ctx) {
        return;
    }
    m_async_ctx->running = false;
    if (m_async_ctx->read_worker.joinable()) {
        m_async_ctx->read_worker.join();
    }
    m_async_ctx.reset();
    m_async_active = false;
}

bool UsbTransport::is_async_reading() const noexcept {
    return m_async_active;
}

} // namespace atkdl16
