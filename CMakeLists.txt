cmake_minimum_required(VERSION 3.20)

project(floret VERSION 0.1.0 LANGUAGES C)

# Headers only for now; the kernel sources are added as they are written.
add_library(floret INTERFACE)
target_include_directories(floret INTERFACE ${CMAKE_CURRENT_LIST_DIR}/include)
target_compile_features(floret INTERFACE c_std_11)
