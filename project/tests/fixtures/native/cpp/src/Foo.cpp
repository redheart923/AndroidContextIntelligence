#include "demo/Foo.h"

namespace android::demo {
int Foo::run(int value) {
    return value + 1;
}

int parse(int value) {
    return value;
}

int parse(const char* value) {
    return value == nullptr ? 0 : 1;
}
}  // namespace android::demo
