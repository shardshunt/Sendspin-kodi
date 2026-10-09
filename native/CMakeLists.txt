cmake_minimum_required(VERSION 3.16)
project(sendspin_kodi_backend LANGUAGES C CXX)

include(FetchContent)

set(BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(SENDSPIN_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(SENDSPIN_ENABLE_SOURCE OFF CACHE BOOL "" FORCE)
set(SENDSPIN_ENABLE_OPUS OFF CACHE BOOL "" FORCE)
set(SENDSPIN_ENABLE_COLOR OFF CACHE BOOL "" FORCE)
set(SENDSPIN_ENABLE_VISUALIZER OFF CACHE BOOL "" FORCE)

# LibreELEC provides ALSA but does not ship PortAudio. Build PortAudio into
# the backend as a static ALSA-only library so the add-on does not require
# libportaudio.so.2, JACK, PulseAudio, or PipeWire on the target box.
set(PA_BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(PA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(PA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(PA_USE_ALSA ON CACHE BOOL "" FORCE)
set(PA_USE_JACK OFF CACHE BOOL "" FORCE)
set(PA_USE_PULSEAUDIO OFF CACHE BOOL "" FORCE)
set(PA_USE_SNDIO OFF CACHE BOOL "" FORCE)

FetchContent_Declare(
    portaudio
    GIT_REPOSITORY https://github.com/PortAudio/portaudio.git
    GIT_TAG v19.7.0
    GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(portaudio)

FetchContent_Declare(
    sendspin_cpp
    GIT_REPOSITORY https://github.com/Sendspin/sendspin-cpp.git
    GIT_TAG 91038b72892596b9ec0cccfec1b9e6ebc58ba148
    GIT_SHALLOW FALSE
)
FetchContent_MakeAvailable(sendspin_cpp)

# Music Assistant 2.10.1 / aiosendspin 9.1.1 omits psk_category from the
# Noise handshake message. Apply the compatibility patch to the fetched tree.
function(apply_sendspin_compat_patch patch_file source_file marker)
    if(EXISTS "${patch_file}")
        file(READ "${source_file}" source_text)
        string(FIND "${source_text}" "${marker}" marker_position)
        if(marker_position EQUAL -1)
            execute_process(
                COMMAND git apply "${patch_file}"
                WORKING_DIRECTORY "${sendspin_cpp_SOURCE_DIR}"
                RESULT_VARIABLE patch_result
                OUTPUT_VARIABLE patch_output
                ERROR_VARIABLE patch_error
            )
            if(NOT patch_result EQUAL 0)
                message(FATAL_ERROR
                    "Could not apply sendspin-cpp compatibility patch:\n"
                    "${patch_output}\n${patch_error}")
            endif()
        endif()
    endif()
endfunction()

apply_sendspin_compat_patch(
    "${CMAKE_CURRENT_SOURCE_DIR}/patches/sendspin-cpp-legacy-psk-category.patch"
    "${sendspin_cpp_SOURCE_DIR}/src/noise_handshake.cpp"
    "SENDSPIN_KODI_LEGACY_CATEGORY_COMPAT")

# Music Assistant 2.10.1 / aiosendspin 9.1.1 uses the pre-v0.9 client message
# shapes. Keep these compatibility rewrites local to the fetched dependency.
set(SENDSPIN_PROTOCOL_SOURCE "${sendspin_cpp_SOURCE_DIR}/src/protocol.cpp")
file(READ "${SENDSPIN_PROTOCOL_SOURCE}" SENDSPIN_PROTOCOL_TEXT)
string(FIND "${SENDSPIN_PROTOCOL_TEXT}" "SENDSPIN_KODI_MA2101_CLIENT_HELLO_COMPAT" SENDSPIN_HELLO_MARKER)
if(SENDSPIN_HELLO_MARKER EQUAL -1)
    string(REPLACE
        [=[        JsonObject methods_obj = root["payload"]["supported_pair_methods"].to<JsonObject>();
        for (const auto& desc : msg->supported_pair_methods) {
            JsonObject method_obj = methods_obj[to_cstr(desc.method)].to<JsonObject>();
            const auto& out_channels = desc.out_channels;
            if (out_channels.has_value() && !out_channels->empty()) {
                JsonArray ch_arr = method_obj["out_channels"].to<JsonArray>();
                for (const auto& ch : out_channels.value()) {
                    ch_arr.add(to_cstr(ch));
                }
            }
            const auto& formats = desc.formats;
            if (formats.has_value() && !formats->empty()) {
                JsonArray fmt_arr = method_obj["formats"].to<JsonArray>();
                for (const auto& fmt : formats.value()) {
                    fmt_arr.add(to_cstr(fmt));
                }
            }
            const auto& locations = desc.locations;]=]
        [=[        // SENDSPIN_KODI_MA2101_CLIENT_HELLO_COMPAT: older list representation.
        JsonArray methods_array = root["payload"]["supported_pair_methods"].to<JsonArray>();
        for (const auto& desc : msg->supported_pair_methods) {
            JsonObject method_obj = methods_array.add<JsonObject>();
            method_obj["method"] = to_cstr(desc.method);
            const auto& out_channels = desc.out_channels;
            if (out_channels.has_value() && !out_channels->empty()) {
                JsonArray ch_arr = method_obj["out_channels"].to<JsonArray>();
                for (const auto& ch : out_channels.value()) {
                    ch_arr.add(to_cstr(ch));
                }
            }
            const auto& locations = desc.locations;]=]
        SENDSPIN_PROTOCOL_TEXT "${SENDSPIN_PROTOCOL_TEXT}")
    file(WRITE "${SENDSPIN_PROTOCOL_SOURCE}" "${SENDSPIN_PROTOCOL_TEXT}")
endif()

file(READ "${SENDSPIN_PROTOCOL_SOURCE}" SENDSPIN_PLAYER_TEXT)
string(FIND "${SENDSPIN_PLAYER_TEXT}" "SENDSPIN_KODI_MA2101_PLAYER_COMMANDS_COMPAT" SENDSPIN_COMMAND_MARKER)
if(SENDSPIN_COMMAND_MARKER EQUAL -1)
    string(REPLACE
        [=[        root["payload"]["player@v1_support"]["buffer_capacity"] =
            msg->player_v1_support.value().buffer_capacity;]=]
        [=[        root["payload"]["player@v1_support"]["buffer_capacity"] =
            msg->player_v1_support.value().buffer_capacity;
        // SENDSPIN_KODI_MA2101_PLAYER_COMMANDS_COMPAT
        JsonArray commands_list =
            root["payload"]["player@v1_support"]["supported_commands"].to<JsonArray>();
        commands_list.add("volume");
        commands_list.add("mute");]=]
        SENDSPIN_PLAYER_TEXT "${SENDSPIN_PLAYER_TEXT}")
    file(WRITE "${SENDSPIN_PROTOCOL_SOURCE}" "${SENDSPIN_PLAYER_TEXT}")
endif()

string(REPLACE
    [=[root["payload"]["player"]["output_delay_ms"] = player_state.output_delay_ms;]=]
    [=[root["payload"]["player"]["static_delay_ms"] = player_state.output_delay_ms;]=]
    SENDSPIN_PLAYER_TEXT "${SENDSPIN_PLAYER_TEXT}")
string(REPLACE
    [=[commands_list.add(to_cstr(cmd));]=]
    [=[if (cmd == SendspinPlayerCommand::SET_OUTPUT_DELAY) {
                commands_list.add("set_static_delay");
            }]=]
    SENDSPIN_PLAYER_TEXT "${SENDSPIN_PLAYER_TEXT}")
file(WRITE "${SENDSPIN_PROTOCOL_SOURCE}" "${SENDSPIN_PLAYER_TEXT}")

# The server's older schema calls the adjustable-delay command
# set_static_delay and carries static_delay_ms; v0.9.3 used output_delay names.
set(SENDSPIN_COMMAND_SOURCE "${sendspin_cpp_SOURCE_DIR}/src/protocol_messages.h")
file(READ "${SENDSPIN_COMMAND_SOURCE}" SENDSPIN_COMMAND_TEXT)
string(REPLACE
    [=[    if (str == "set_output_delay") {]=]
    [=[    if (str == "set_output_delay" || str == "set_static_delay") {]=]
    SENDSPIN_COMMAND_TEXT "${SENDSPIN_COMMAND_TEXT}")
file(WRITE "${SENDSPIN_COMMAND_SOURCE}" "${SENDSPIN_COMMAND_TEXT}")

string(REPLACE
    [=[    if (auto v = read_uint_field<uint16_t>(player_object["output_delay_ms"], "output_delay_ms")) {
        player_cmd->output_delay_ms = v;
    }]=]
    [=[    if (auto v = read_uint_field<uint16_t>(player_object["static_delay_ms"], "static_delay_ms")) {
        player_cmd->output_delay_ms = v;
    } else if (auto v = read_uint_field<uint16_t>(player_object["output_delay_ms"], "output_delay_ms")) {
        player_cmd->output_delay_ms = v;
    }]=]
    SENDSPIN_PLAYER_TEXT "${SENDSPIN_PLAYER_TEXT}")
file(WRITE "${SENDSPIN_PROTOCOL_SOURCE}" "${SENDSPIN_PLAYER_TEXT}")

set(SENDSPIN_CPP_EXAMPLES_DIR "${sendspin_cpp_SOURCE_DIR}/examples/common")

add_executable(sendspin-kodi-backend
    backend_main.cpp
    control_socket.cpp
    "${SENDSPIN_CPP_EXAMPLES_DIR}/file_persistence_provider.cpp"
    "${SENDSPIN_CPP_EXAMPLES_DIR}/portaudio_sink.cpp"
)
target_compile_features(sendspin-kodi-backend PRIVATE cxx_std_20)
target_include_directories(sendspin-kodi-backend PRIVATE "${SENDSPIN_CPP_EXAMPLES_DIR}")
target_link_libraries(sendspin-kodi-backend PRIVATE sendspin portaudio_static)
target_compile_definitions(sendspin-kodi-backend PRIVATE SENDSPIN_KODI_VERSION="0.1.0")
