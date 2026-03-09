#include "utils.h"
#include <cstdio>

namespace app {

void Utils::initialize() {
    if (!initialized_) {
        setup();
        initialized_ = true;
    }
}

void Utils::log(LogLevel level, const char* message) {
    const char* prefix = "INFO";
    switch (level) {
        case LogLevel::Debug: prefix = "DEBUG"; break;
        case LogLevel::Info: prefix = "INFO"; break;
        case LogLevel::Warning: prefix = "WARN"; break;
        case LogLevel::Error: prefix = "ERROR"; break;
    }
    printf("[%s] %s\n", prefix, message);
}

void Utils::setup() {
    log(LogLevel::Info, "Utils initialized");
}

} // namespace app
