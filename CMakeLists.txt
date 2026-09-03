cmake_minimum_required(VERSION 3.20)
project(atk_dl16 VERSION 1.0.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(MSVC)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()

# Include directories
set(ATKDL16_INCLUDE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/core/include")

# Locate libusb
if(WIN32)
    set(LIBUSB_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/third_party/libusb")
    set(LIBUSB_INCLUDE_DIR "${LIBUSB_ROOT}/include")

    if(MSVC)
        # Use prebuilt static library for self-contained binaries
        set(LIBUSB_LIBRARIES "${LIBUSB_ROOT}/VS2022/MS64/static/libusb-1.0.lib" Advapi32)
    else()
        # MinGW
        set(LIBUSB_LIBRARIES "${LIBUSB_ROOT}/MinGW64/static/libusb-1.0.a")
    endif()
else()
    find_package(PkgConfig REQUIRED)
    pkg_check_modules(LIBUSB REQUIRED libusb-1.0)
    set(LIBUSB_INCLUDE_DIR ${LIBUSB_INCLUDE_DIRS})
    set(LIBUSB_LIBRARIES ${LIBUSB_LIBRARIES})
endif()

# Core Library
add_library(atkdl16_core STATIC
    core/src/protocol.cpp
    core/src/rx_parser.cpp
    core/src/rle.cpp
    core/src/sample_store.cpp
    core/src/capability.cpp
    core/src/usb_transport.cpp
    core/src/device.cpp
    core/src/capture.cpp
)

target_include_directories(atkdl16_core PUBLIC
    ${ATKDL16_INCLUDE_DIR}
    ${LIBUSB_INCLUDE_DIR}
)

target_link_libraries(atkdl16_core PUBLIC
    ${LIBUSB_LIBRARIES}
)

# Test Runner Executable
add_executable(atkdl16_tests
    tests/unit/test_main.cpp
)

target_include_directories(atkdl16_tests PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/tests/unit
)

target_link_libraries(atkdl16_tests PRIVATE
    atkdl16_core
)

# CLI Tool Executable
add_executable(atk-dl16
    cli/main.cpp
)

target_link_libraries(atk-dl16 PRIVATE
    atkdl16_core
)

# Enable CTest
enable_testing()
add_test(NAME UnitAndSyntheticTests COMMAND atkdl16_tests)
