#ifndef UTILS_H
#define UTILS_H

namespace app {

enum class LogLevel {
    Debug,
    Info,
    Warning,
    Error
};

class Utils {
public:
    void initialize();
    void log(LogLevel level, const char* message);

protected:
    void setup();

private:
    bool initialized_ = false;
};

using LogCallback = void(*)(LogLevel, const char*);

} // namespace app

#endif // UTILS_H
