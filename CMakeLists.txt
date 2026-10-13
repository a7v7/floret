cmake_minimum_required(VERSION 3.20)

project(floret VERSION 0.1.0 LANGUAGES C)

# The kernel. The application (or its platform) supplies the five port
# functions in floret/floret_port.h.
add_library(floret STATIC
    src/floret.c
    src/floret_queue.c
)
target_include_directories(floret PUBLIC ${CMAKE_CURRENT_LIST_DIR}/include)
target_compile_features(floret PUBLIC c_std_11)

# Unit tests on the build machine: cmake -DFLORET_BUILD_TESTS=ON
option(FLORET_BUILD_TESTS "Build the host unit tests" OFF)
if(FLORET_BUILD_TESTS)
    enable_testing()
    set(FLORET_WARNINGS -Wall -Wextra -Wpedantic -Werror)
    target_compile_options(floret PRIVATE ${FLORET_WARNINGS})

    add_executable(floret_tests tests/test_floret.c tests/port_host.c)
    target_link_libraries(floret_tests PRIVATE floret)
    target_compile_options(floret_tests PRIVATE ${FLORET_WARNINGS})
    add_test(NAME floret_tests COMMAND floret_tests)
endif()
