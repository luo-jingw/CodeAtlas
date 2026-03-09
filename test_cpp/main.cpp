#include "utils.h"
#include <iostream>

namespace app {

class Application {
public:
    Application() = default;

    void run();
    int getStatus() const { return status_; }

private:
    int status_ = 0;
    Utils utils_;
};

void Application::run() {
    utils_.initialize();
    std::cout << "Running..." << std::endl;
    status_ = 1;
}

} // namespace app

int main() {
    app::Application app;
    app.run();
    return app.getStatus();
}
