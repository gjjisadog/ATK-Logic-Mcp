#pragma once

#include <iostream>
#include <string>
#include <vector>
#include <functional>
#include <cstdlib>

namespace atkdl16::test {

struct TestCase {
    std::string name;
    std::function<void()> func;
};

inline std::vector<TestCase>& get_registry() {
    static std::vector<TestCase> reg;
    return reg;
}

inline int register_test(const std::string& name, std::function<void()> func) {
    get_registry().push_back({name, std::move(func)});
    return 0;
}

#define TEST_CASE(name) \
    static void name(); \
    static int _reg_##name = ::atkdl16::test::register_test(#name, name); \
    static void name()

#define ASSERT_TRUE(cond) \
    do { \
        if (!(cond)) { \
            std::cerr << "Assertion failed: " #cond << " at " << __FILE__ << ":" << __LINE__ << std::endl; \
            std::exit(1); \
        } \
    } while (0)

#define ASSERT_FALSE(cond) ASSERT_TRUE(!(cond))

#define ASSERT_EQ(a, b) \
    do { \
        if ((a) != (b)) { \
            std::cerr << "Assertion failed: " #a " == " #b " (" << (a) << " != " << (b) << ") at " \
                      << __FILE__ << ":" << __LINE__ << std::endl; \
            std::exit(1); \
        } \
    } while (0)

} // namespace atkdl16::test
