#include "atkdl16/device.h"
#include "atkdl16/capture.h"
#include "atkdl16/capability.h"
#include "atkdl16/sample_store.h"
#include <iostream>
#include <string>
#include <vector>
#include <sstream>
#include <iomanip>

using namespace atkdl16;

static uint64_t parse_rate_string(const std::string& str) {
    if (str.empty()) return 20'000'000ULL;
    double val = std::stod(str);
    char last = str.back();
    if (last == 'k' || last == 'K') return static_cast<uint64_t>(val * 1e3);
    if (last == 'm' || last == 'M') return static_cast<uint64_t>(val * 1e6);
    if (last == 'g' || last == 'G') return static_cast<uint64_t>(val * 1e9);
    return static_cast<uint64_t>(val);
}

static uint64_t parse_duration_string(const std::string& str) {
    if (str.empty()) return 20;
    double val = std::stod(str);
    if (str.find("ms") != std::string::npos) return static_cast<uint64_t>(val);
    if (str.find("us") != std::string::npos) return static_cast<uint64_t>(std::max(1.0, val / 1000.0));
    if (str.find('s') != std::string::npos) return static_cast<uint64_t>(val * 1000.0);
    return static_cast<uint64_t>(val);
}

static std::vector<uint8_t> parse_channels(const std::string& str) {
    std::vector<uint8_t> result;
    std::stringstream ss(str);
    std::string item;
    while (std::getline(ss, item, ',')) {
        if (!item.empty()) {
            result.push_back(static_cast<uint8_t>(std::stoi(item)));
        }
    }
    if (result.empty()) {
        result = {0, 1};
    }
    return result;
}

static TriggerConfig parse_trigger(const std::string& str) {
    TriggerConfig cfg;
    if (str.empty() || str == "immediate" || str == "none") {
        cfg.is_instantly = true;
        return cfg;
    }

    // Format: ch0:rising or 0:rising or ch1:falling
    std::string s = str;
    if (s.rfind("ch", 0) == 0) s = s.substr(2);
    auto colon = s.find(':');
    if (colon != std::string::npos) {
        uint8_t ch = static_cast<uint8_t>(std::stoi(s.substr(0, colon)));
        std::string edge = s.substr(colon + 1);
        TriggerType type = TriggerType::RisingEdge;
        if (edge == "falling") type = TriggerType::FallingEdge;
        else if (edge == "high") type = TriggerType::HighLevel;
        else if (edge == "low") type = TriggerType::LowLevel;
        else if (edge == "both" || edge == "double") type = TriggerType::DoubleEdge;
        else if (edge == "rising") type = TriggerType::RisingEdge;

        cfg.triggers.push_back({ch, type});
        cfg.is_instantly = false;
    } else {
        cfg.is_instantly = true;
    }
    return cfg;
}

static void print_usage() {
    std::cout << "ATK-DL16 Headless Logic Analyzer CLI\n\n"
              << "Usage: atk-dl16 <command> [options]\n\n"
              << "Commands:\n"
              << "  list                          List connected DL16 devices\n"
              << "  info                          Display detailed hardware information\n"
              << "  capture [options]             Perform capture and store raw samples\n"
              << "  inspect --id <capture_id>     Inspect edge list of a previous capture\n\n"
              << "Capture Options:\n"
              << "  --channels <0,1,...>          Enabled channels (default: 0,1)\n"
              << "  --sample-rate <rate>          Sample rate (e.g. 20M, 100M, 1G, default: 20M)\n"
              << "  --duration <ms>               Capture duration (e.g. 20ms, 100ms, default: 20ms)\n"
              << "  --threshold <voltage>         Comparator threshold voltage (e.g. 1.6, default: 1.6)\n"
              << "  --mode <buffer|stream>        Capture mode (buffer = standard, stream = experimental, default: buffer)\n"
              << "  --trigger <ch:edge>           Trigger spec (e.g. ch0:rising, immediate, default: immediate)\n"
              << "  --out <dir>                   Output directory for captures (default: captures)\n";
}

int main(int argc, char* argv[]) {
    if (argc < 2) {
        print_usage();
        return 0;
    }

    std::string cmd = argv[1];

    if (cmd == "list") {
        auto devices = Device::enumerate();
        std::cout << "Connected ATK-DL16 Devices (" << devices.size() << " found):\n";
        for (size_t i = 0; i < devices.size(); ++i) {
            std::cout << "  [" << i << "] Port: " << devices[i].port_number
                      << ", VID: 0x" << std::hex << devices[i].vid
                      << ", PID: 0x" << devices[i].pid << std::dec << "\n";
        }
        return 0;
    }

    if (cmd == "info") {
        Device dev;
        auto err = dev.open();
        if (!err) {
            std::cerr << "Error opening device: " << err.message << " (" << err.suggested_action << ")\n";
            return 1;
        }
        const auto& info = dev.info();
        std::cout << "Device Information:\n"
                  << "  Model:               " << info.model_name << "\n"
                  << "  Hardware Level:      " << (info.device_level == 1 ? "DL16 Plus" : "DL16 Standard") << "\n"
                  << "  USB Link Speed:      " << (info.usb_speed == UsbSpeed::Usb30 ? "USB 3.0" : "USB 2.0") << "\n"
                  << "  MCU Firmware:        v" << (info.mcu_firmware_version / 10.0) << "\n"
                  << "  FPGA Firmware:       v" << (info.fpga_firmware_version / 100.0) << "\n"
                  << "  Channels:            " << info.channel_count << "\n";
        dev.close();
        return 0;
    }

    if (cmd == "capture") {
        CaptureConfig config;
        std::string out_dir = "captures";

        for (int i = 2; i < argc; ++i) {
            std::string arg = argv[i];
            if (arg == "--channels" && i + 1 < argc) {
                config.enabled_channels = parse_channels(argv[++i]);
            } else if (arg == "--sample-rate" && i + 1 < argc) {
                config.sample_rate = parse_rate_string(argv[++i]);
            } else if (arg == "--duration" && i + 1 < argc) {
                config.duration_ms = parse_duration_string(argv[++i]);
            } else if (arg == "--threshold" && i + 1 < argc) {
                config.threshold_voltage = std::stod(argv[++i]);
            } else if (arg == "--mode" && i + 1 < argc) {
                std::string m = argv[++i];
                config.mode = (m == "stream") ? CaptureMode::Stream : CaptureMode::Buffer;
            } else if (arg == "--trigger" && i + 1 < argc) {
                config.trigger = parse_trigger(argv[++i]);
            } else if (arg == "--out" && i + 1 < argc) {
                out_dir = argv[++i];
            }
        }

        Device dev;
        auto err = dev.open();
        if (!err) {
            std::cerr << "Failed to open device: " << err.message << "\n";
            return 1;
        }

        CaptureEngine engine(dev);
        std::cout << "Starting capture: " << (config.sample_rate / 1'000'000.0) << " MHz, "
                  << config.duration_ms << " ms, " << config.enabled_channels.size() << " channels...\n";

        auto result = engine.execute_capture(
            config, out_dir,
            [](uint64_t cur, uint64_t total, uint8_t pct) {
                std::cout << "\rProgress: " << static_cast<int>(pct) << "% (" << cur << "/" << total << " samples)" << std::flush;
            }
        );
        std::cout << "\n";

        if (!result.success) {
            std::cerr << "Capture failed: " << result.error_message << "\n";
            return 1;
        }

        std::cout << "Capture successful!\n"
                  << "  capture_id:     " << result.capture_id << "\n"
                  << "  integrity:      " << to_string(result.data_integrity) << "\n"
                  << "  requested:      " << result.requested_samples << "\n"
                  << "  actual_samples: " << result.actual_samples << "\n"
                  << "  output_dir:     " << out_dir << "/" << result.capture_id << "\n";
        return 0;
    }

    if (cmd == "inspect") {
        std::string capture_id;
        std::string dir = "captures";
        for (int i = 2; i < argc; ++i) {
            std::string arg = argv[i];
            if ((arg == "--id" || arg == "--capture") && i + 1 < argc) {
                capture_id = argv[++i];
            } else if (arg == "--dir" && i + 1 < argc) {
                dir = argv[++i];
            }
        }
        if (capture_id.empty()) {
            std::cerr << "Usage: atk-dl16 inspect --id <capture_id>\n";
            return 1;
        }

        std::string target = dir + "/" + capture_id;
        auto store = SampleStore::load_from_directory(target);
        if (!store) {
            std::cerr << "Failed to load capture from " << target << "\n";
            return 1;
        }

        std::cout << "Capture Artifact: " << capture_id << "\n";
        auto all_edges = store->extract_all_edges();
        for (const auto& [ch, ce] : all_edges) {
            std::cout << "  Channel " << static_cast<int>(ch) << ": "
                      << ce.edges.size() << " transitions (initial level: " << static_cast<int>(ce.initial_level) << ")\n";
            size_t show_count = std::min(ce.edges.size(), size_t{10});
            for (size_t k = 0; k < show_count; ++k) {
                std::cout << "    [" << ce.edges[k].sample_index << " -> " << static_cast<int>(ce.edges[k].level) << "]\n";
            }
            if (ce.edges.size() > show_count) {
                std::cout << "    ... (" << (ce.edges.size() - show_count) << " more edges)\n";
            }
        }
        return 0;
    }

    print_usage();
    return 0;
}
