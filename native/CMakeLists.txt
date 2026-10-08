cmake_minimum_required(VERSION 3.16)
project(sendspin_kodi_backend LANGUAGES C CXX)

include(FetchContent)
include(FindPkgConfig)

set(BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(SENDSPIN_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(SENDSPIN_ENABLE_SOURCE OFF CACHE BOOL "" FORCE)
set(SENDSPIN_ENABLE_OPUS OFF CACHE BOOL "" FORCE)
set(SENDSPIN_ENABLE_COLOR OFF CACHE BOOL "" FORCE)
set(SENDSPIN_ENABLE_VISUALIZER OFF CACHE BOOL "" FORCE)

FetchContent_Declare(
    sendspin_cpp
    GIT_REPOSITORY https://github.com/Sendspin/sendspin-cpp.git
    GIT_TAG 91038b72892596b9ec0cccfec1b9e6ebc58ba148
    GIT_SHALLOW FALSE
)
FetchContent_MakeAvailable(sendspin_cpp)

pkg_check_modules(PORTAUDIO REQUIRED IMPORTED_TARGET portaudio-2.0)

set(SENDSPIN_CPP_EXAMPLES_DIR "${sendspin_cpp_SOURCE_DIR}/examples/common")

add_executable(sendspin-kodi-backend
    backend_main.cpp
    control_socket.cpp
    "${SENDSPIN_CPP_EXAMPLES_DIR}/file_persistence_provider.cpp"
    "${SENDSPIN_CPP_EXAMPLES_DIR}/portaudio_sink.cpp"
)
target_compile_features(sendspin-kodi-backend PRIVATE cxx_std_20)
target_include_directories(sendspin-kodi-backend PRIVATE "${SENDSPIN_CPP_EXAMPLES_DIR}")
target_link_libraries(sendspin-kodi-backend PRIVATE sendspin PkgConfig::PORTAUDIO)
target_compile_definitions(sendspin-kodi-backend PRIVATE SENDSPIN_KODI_VERSION="0.1.0")
