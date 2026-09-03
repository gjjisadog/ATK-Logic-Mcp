#include "atkdl16/device.h"
#include "atkdl16/rx_parser.h"
#include "atkdl16/protocol.h"
#include <iostream>
#include <thread>
#include <chrono>
#include <cstring>

namespace atkdl16 {

Device::Device()
    : m_transport(std::make_unique<UsbTransport>()) {}

Device::~Device() {
    close();
}

std::vector<DeviceInfo> Device::enumerate() {
    UsbTransport transport;
    return transport.enumerate();
}

Error Device::open(int port) {
    if (m_is_open) {
        return Error::ok();
    }

    auto err = m_transport->open(port);
    if (!err) {
        return err;
    }

    m_is_open = true;

    // Query device telemetry and FPGA state
    err = query_info(m_info);
    if (!err) {
        close();
        return err;
    }

    // Wake up FPGA comparator / clock subsystems
    wake_fpga();

    return Error::ok();
}

void Device::close() {
    if (m_is_open) {
        sleep_fpga();
        m_transport->close();
        m_is_open = false;
    }
}

bool Device::is_open() const noexcept {
    return m_is_open && m_transport && m_transport->is_open();
}

Error Device::wake_fpga() {
    if (!is_open()) return {ErrorCode::DeviceDisconnected, "Device not open", false, ""};
    uint8_t buf[512] = {0};
    buf[0] = 0x0A;
    buf[1] = static_cast<uint8_t>(CommandCode::FpgaResetActive); // 0x87
    buf[2] = 0x01; // Active / Awake
    m_transport->write_bulk(EP_BULK_OUT, buf, sizeof(buf), 100);
    flush_buffers();
    return Error::ok();
}

Error Device::sleep_fpga() {
    if (!is_open()) return {ErrorCode::DeviceDisconnected, "Device not open", false, ""};
    uint8_t buf[512] = {0};
    buf[0] = 0x0A;
    buf[1] = static_cast<uint8_t>(CommandCode::FpgaResetActive); // 0x87
    buf[2] = 0x00; // Sleep
    m_transport->write_bulk(EP_BULK_OUT, buf, sizeof(buf), 100);
    return Error::ok();
}

Error Device::flush_buffers() {
    if (!is_open()) return {ErrorCode::DeviceDisconnected, "Device not open", false, ""};
    std::vector<uint8_t> dump(2048);
    size_t actual = 0;
    int tries = 0;
    while (tries++ < 50) {
        m_transport->read_bulk(EP_BULK_IN, dump.data(), dump.size(), actual, 5);
        if (actual == 0) {
            break;
        }
    }
    return Error::ok();
}

Error Device::query_info(DeviceInfo& info) {
    if (!is_open()) {
        return {ErrorCode::DeviceDisconnected, "Device not open", false, "Open device first"};
    }

    // 1. Query MCU Version
    uint8_t mcu_cmd[512] = {0};
    mcu_cmd[0] = 0x0A;
    mcu_cmd[1] = static_cast<uint8_t>(CommandCode::McuGetVersion); // 0x81

    auto err = m_transport->write_bulk(EP_BULK_OUT, mcu_cmd, sizeof(mcu_cmd), 150);
    if (!err) {
        return err;
    }

    uint8_t mcu_resp[512] = {0};
    size_t actual_len = 0;
    err = m_transport->read_bulk(EP_BULK_IN, mcu_resp, sizeof(mcu_resp), actual_len, 200);
    if (!err || actual_len < 9) {
        return {ErrorCode::ProtocolError, "MCU version response timeout or truncated", true, "Retry query"};
    }

    // Check upstream response pattern: [0x0A, 0x81, 0x01, 0x61, ...]
    if (mcu_resp[0] == 0x0A && mcu_resp[1] == 0x81 && mcu_resp[2] == 0x01 && mcu_resp[3] == 0x61) {
        info.device_level = mcu_resp[8]; // 0 = DL16, 1 = DL16 Plus
        info.mcu_firmware_version = static_cast<int>(mcu_resp[4]) * 10 + static_cast<int>(mcu_resp[5]);
        info.hardware_version = static_cast<int>(mcu_resp[6]);
        info.model = (info.device_level == 1) ? DeviceModel::DL16Plus : DeviceModel::DL16;
    } else {
        return {ErrorCode::ProtocolError, "Invalid MCU version header", true, "Check device firmware"};
    }

    // 2. Pulse Reset State on FPGA (Upstream sequence: SetResetState(0) then SetResetState(1))
    uint8_t reset_cmd[512] = {0};
    reset_cmd[0] = 0x0A;
    reset_cmd[1] = static_cast<uint8_t>(CommandCode::FpgaResetActive);
    reset_cmd[2] = 0x00;
    m_transport->write_bulk(EP_BULK_OUT, reset_cmd, sizeof(reset_cmd), 50);

    reset_cmd[2] = 0x01;
    m_transport->write_bulk(EP_BULK_OUT, reset_cmd, sizeof(reset_cmd), 50);

    // Flush leftover sync bytes
    flush_buffers();

    // 3. Query FPGA Device Information (Command 0x10)
    err = m_transport->send_command(CommandCode::GetDeviceData);
    if (!err) {
        return err;
    }

    std::vector<uint8_t> raw_resp(2048, 0);
    std::vector<uint8_t> conv_resp(2048, 0);
    actual_len = 0;
    err = m_transport->read_bulk(EP_BULK_IN, raw_resp.data(), raw_resp.size(), actual_len, 300);
    if (!err || actual_len < 2048) {
        return {ErrorCode::ProtocolError, "FPGA device info response timed out or incomplete", true, "Check USB link"};
    }

    // Deinterleave the 2048-byte block
    convert_to_pc(raw_resp.data(), conv_resp.data(), 2048);

    // Parse with RxParser
    RxParser parser;
    parser.push_bytes(conv_resp.data(), actual_len);

    bool found_info = false;
    while (parser.has_message()) {
        auto msg = parser.pop_message();
        if (msg && msg->type == RxMessageType::DeviceInfo) {
            auto dev_info_opt = RxParser::parse_device_info(*msg);
            if (dev_info_opt && dev_info_opt->fpga_status_ok) {
                info.usb_speed = dev_info_opt->usb_speed;
                info.fpga_firmware_version = dev_info_opt->fpga_version;
                info.model_name = dev_info_opt->model_name;
                if (info.device_level == 1 && info.model_name.find("Plus") == std::string::npos) {
                    info.model_name += " Plus";
                }
                found_info = true;
                break;
            }
        }
    }

    if (!found_info) {
        return {ErrorCode::ProtocolError, "Failed to parse FPGA device info packet", true, "Check FPGA firmware"};
    }

    info.channel_count = 16;
    info.vid = VENDOR_ID;
    info.pid = PRODUCT_ID;
    info.is_busy = false;

    return Error::ok();
}

} // namespace atkdl16
