#include "control_socket.h"
#include "file_persistence_provider.h"
#include "portaudio_sink.h"

#include <ArduinoJson.h>

#include "sendspin/client.h"
#include "sendspin/controller_role.h"
#include "sendspin/metadata_role.h"
#include "sendspin/player_role.h"

#include <atomic>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>

namespace {

volatile std::sig_atomic_t running = 1;

void stop_signal(int) {
    running = 0;
}

struct Options {
    std::string name = "Sendspin Kodi";
    std::string server_url;
    std::string state_file;
    std::string control_socket;
    bool print_pairing_token = false;
    uint8_t initial_volume = 100;
    bool initial_muted = false;
    std::optional<uint16_t> initial_delay_ms;
    bool show_help = false;
    bool show_version = false;
};

void print_usage(const char* program) {
    std::fprintf(stderr,
                 "Usage: %s [--name NAME] [--server WS_URL] [--state-file PATH] "
                 "[--control-socket PATH] [--volume 0-100] [--muted true|false] "
                 "[--delay-ms 0-5000] [--print-pairing-token] [--version]\n",
                 program);
}

bool parse_options(int argc, char** argv, Options& options) {
    for (int index = 1; index < argc; ++index) {
        const std::string argument = argv[index];
        if (argument == "--help" || argument == "-h") {
            options.show_help = true;
        } else if (argument == "--version") {
            options.show_version = true;
        } else if (argument == "--print-pairing-token") {
            options.print_pairing_token = true;
        } else if ((argument == "--name" || argument == "--server" || argument == "--state-file" ||
                argument == "--control-socket" || argument == "--volume" || argument == "--muted" ||
                argument == "--delay-ms") && index + 1 < argc) {
            const std::string value = argv[++index];
            if (argument == "--name") {
                options.name = value;
            } else if (argument == "--server") {
                options.server_url = value;
            } else if (argument == "--state-file") {
                options.state_file = value;
            } else if (argument == "--control-socket") {
                options.control_socket = value;
            } else if (argument == "--muted") {
                if (value != "true" && value != "false") {
                    return false;
                }
                options.initial_muted = value == "true";
            } else {
                int parsed_value = -1;
                try {
                    parsed_value = std::stoi(value);
                } catch (const std::exception&) {
                    return false;
                }
                if (argument == "--volume") {
                    if (parsed_value < 0 || parsed_value > 100) {
                        return false;
                    }
                    options.initial_volume = static_cast<uint8_t>(parsed_value);
                } else {
                    if (parsed_value < 0 || parsed_value > 5000) {
                        return false;
                    }
                    options.initial_delay_ms = static_cast<uint16_t>(parsed_value);
                }
            }
        } else {
            std::fprintf(stderr, "Unknown or incomplete option: %s\n", argument.c_str());
            return false;
        }
    }
    return true;
}

std::string default_state_file() {
    const char* home = std::getenv("HOME");
    std::filesystem::path path = home == nullptr ? "." : home;
    path /= ".local/state/sendspin-kodi/state.json";
    return path.string();
}

bool prepare_state_directory(const std::string& state_file) {
    std::error_code error;
    const auto parent = std::filesystem::path(state_file).parent_path();
    if (parent.empty()) {
        return true;
    }
    std::filesystem::create_directories(parent, error);
    if (error) {
        std::fprintf(stderr, "Cannot create state directory '%s': %s\n", parent.c_str(), error.message().c_str());
        return false;
    }
    std::filesystem::permissions(parent, std::filesystem::perms::owner_all,
                                 std::filesystem::perm_options::replace, error);
    if (error) {
        std::fprintf(stderr, "Cannot secure state directory '%s': %s\n", parent.c_str(), error.message().c_str());
        return false;
    }
    return true;
}

struct HostNetworkProvider final : sendspin::SendspinNetworkProvider {
    bool is_network_ready() override {
        return true;
    }
};

struct KodiPlayerListener final : sendspin::PlayerRoleListener {
    KodiPlayerListener(sendspin::PortAudioSink& audio_sink, sendspin::PlayerRole& player_role)
        : sink(audio_sink), player(player_role) {}

    size_t on_audio_write(uint8_t* data, size_t length, uint32_t timeout_ms) override {
        if (this->released.load(std::memory_order_acquire)) {
            return length;
        }
        return sink.write(data, length, timeout_ms);
    }

    void on_stream_start() override {
        this->stream_active = true;
        const auto& parameters = player.get_current_stream_params();
        if (!parameters.is_complete()) {
            std::fprintf(stderr, "Sendspin stream started without complete audio parameters\n");
            return;
        }
        if (this->released.load(std::memory_order_acquire)) {
            return;
        }
        if (!sink.configure(*parameters.sample_rate, *parameters.channels, *parameters.bit_depth)) {
            std::fprintf(stderr, "Could not configure the host audio output\n");
            return;
        }
        sink.set_volume(player.get_volume());
        sink.set_muted(player.get_muted());
    }

    void on_stream_end() override {
        this->stream_active = false;
        sink.clear();
    }

    void on_volume_changed(uint8_t volume) override {
        sink.set_volume(volume);
    }

    void on_mute_changed(bool muted) override {
        sink.set_muted(muted);
    }

    bool release_audio() {
        this->released.store(true, std::memory_order_release);
        sink.stop();
        return true;
    }

    bool acquire_audio() {
        if (!this->stream_active) {
            this->released.store(false, std::memory_order_release);
            return true;
        }
        const auto& parameters = player.get_current_stream_params();
        if (!parameters.is_complete() ||
            !sink.configure(*parameters.sample_rate, *parameters.channels, *parameters.bit_depth)) {
            return false;
        }
        sink.set_volume(player.get_volume());
        sink.set_muted(player.get_muted());
        this->released.store(false, std::memory_order_release);
        return true;
    }

    sendspin::PortAudioSink& sink;
    sendspin::PlayerRole& player;
    std::atomic<bool> released {false};
    bool stream_active = false;
};

struct TrackState {
    std::string title;
    std::string artist;
    std::string album;
    std::string artwork_url;
    uint32_t playback_speed = 0;
};

struct KodiMetadataListener final : sendspin::MetadataRoleListener {
    explicit KodiMetadataListener(TrackState& state) : state(state) {}

    void on_metadata(const sendspin::ServerMetadataStateObject& metadata) override {
        this->state.title = metadata.title.value_or("");
        this->state.artist = metadata.artist.value_or("");
        this->state.album = metadata.album.value_or("");
        this->state.artwork_url = metadata.artwork_url.value_or("");
        this->state.playback_speed = metadata.progress.has_value() ? metadata.progress->playback_speed : 0;
    }

    void on_metadata_clear() override {
        this->state = {};
    }

    TrackState& state;
};

struct KodiClientListener final : sendspin::SendspinClientListener {
    void on_trust_changed(sendspin::ConnectionTrust trust) override {
        std::fprintf(stderr, "Sendspin server trust: %s\n",
                     trust == sendspin::ConnectionTrust::USER ? "paired" : "unpaired");
    }

    void on_pairing_succeeded(const std::string&) override {
        std::fprintf(stderr, "Sendspin pairing completed\n");
    }
};

std::string serialize_json(JsonDocument& document) {
    std::string response;
    serializeJson(document, response);
    return response;
}

std::string state_response(sendspin::SendspinClient& client, sendspin::PlayerRole& player,
                           sendspin::MetadataRole& metadata, const KodiPlayerListener& player_listener,
                           const TrackState& track_state) {
    JsonDocument state;
    JsonObject track = state["track"].to<JsonObject>();
    track["title"] = track_state.title;
    track["artist"] = track_state.artist;
    track["album"] = track_state.album;
    track["artwork_url"] = track_state.artwork_url;

    JsonObject playback = state["playback"].to<JsonObject>();
    playback["position"] = static_cast<double>(metadata.get_track_progress_ms()) / 1000.0;
    playback["duration"] = static_cast<double>(metadata.get_track_duration_ms()) / 1000.0;
    playback["speed"] = track_state.playback_speed > 0 ? 1.0 : 0.0;

    JsonObject volume = state["volume"].to<JsonObject>();
    volume["volume"] = player.get_volume();
    volume["muted"] = player.get_muted();

    JsonObject connection = state["connection"].to<JsonObject>();
    connection["connected"] = client.is_connected();

    JsonObject audio = state["audio"].to<JsonObject>();
    audio["released"] = player_listener.released.load(std::memory_order_acquire);
    audio["stream_active"] = player_listener.stream_active;
    return serialize_json(state);
}

std::string error_response(const char* message) {
    JsonDocument response;
    response["ok"] = false;
    response["error"] = message;
    return serialize_json(response);
}

std::string handle_control_request(const std::string& request, sendspin::SendspinClient& client,
                                  sendspin::PlayerRole& player, sendspin::ControllerRole& controller,
                                  sendspin::MetadataRole& metadata, KodiPlayerListener& player_listener,
                                  const TrackState& track_state) {
    JsonDocument input;
    if (deserializeJson(input, request)) {
        return error_response("invalid JSON request");
    }
    const char* command_value = input["command"] | "";
    const std::string command(command_value);
    if (command == "get_state") {
        return state_response(client, player, metadata, player_listener, track_state);
    }
    if (command == "pair_token") {
        const auto token = client.pairing_token();
        if (!token.has_value()) {
            return error_response("pairing token is not ready");
        }
        JsonDocument response;
        response["ok"] = true;
        response["token"] = *token;
        return serialize_json(response);
    }
    if (command == "audio_status") {
        JsonDocument response;
        response["ok"] = true;
        response["audio"]["released"] = player_listener.released.load(std::memory_order_acquire);
        response["audio"]["stream_active"] = player_listener.stream_active;
        return serialize_json(response);
    }
    if (command == "release_audio" || command == "acquire_audio") {
        JsonDocument response;
        const bool success = command == "release_audio" ? player_listener.release_audio()
                                                         : player_listener.acquire_audio();
        response["ok"] = success;
        if (!success) {
            response["error"] = "could not configure audio output";
        }
        return serialize_json(response);
    }
    if (command == "set_volume") {
        if (!input["volume"].is<int>() || input["volume"].as<int>() < 0 || input["volume"].as<int>() > 100) {
            return error_response("volume must be an integer from 0 to 100");
        }
        const auto volume = static_cast<uint8_t>(input["volume"].as<int>());
        const bool muted = input["muted"] | false;
        player.update_volume(volume);
        player.update_muted(muted);
        player_listener.on_volume_changed(volume);
        player_listener.on_mute_changed(muted);
        JsonDocument response;
        response["ok"] = true;
        return serialize_json(response);
    }
    if (command == "set_delay") {
        const double delay = input["delay_ms"] | -1.0;
        if (!std::isfinite(delay) || delay < 0.0 || delay > 5000.0) {
            return error_response("delay_ms must be from 0 to 5000");
        }
        player.update_output_delay(static_cast<uint16_t>(std::lround(delay)));
        JsonDocument response;
        response["ok"] = true;
        return serialize_json(response);
    }

    sendspin::ClientCommandControllerObject transport_command {};
    if (command == "play") {
        transport_command.command = sendspin::SendspinControllerCommand::PLAY;
    } else if (command == "pause") {
        transport_command.command = sendspin::SendspinControllerCommand::PAUSE;
    } else if (command == "next") {
        transport_command.command = sendspin::SendspinControllerCommand::NEXT;
    } else if (command == "previous") {
        transport_command.command = sendspin::SendspinControllerCommand::PREVIOUS;
    } else if (command == "toggle_play_pause") {
        const bool playing = client.get_group_state().playback_state.value_or(sendspin::SendspinPlaybackState::STOPPED) ==
                             sendspin::SendspinPlaybackState::PLAYING;
        transport_command.command = playing ? sendspin::SendspinControllerCommand::PAUSE
                                             : sendspin::SendspinControllerCommand::PLAY;
    } else if (command == "seek") {
        const double position = input["position"] | -1.0;
        if (!std::isfinite(position) || position < 0.0 ||
            position > static_cast<double>(std::numeric_limits<uint32_t>::max()) / 1000.0) {
            return error_response("position must be non-negative seconds");
        }
        transport_command.command = sendspin::SendspinControllerCommand::SEEK;
        transport_command.position_ms = static_cast<uint32_t>(position * 1000.0);
    } else {
        return error_response("unknown command");
    }

    JsonDocument response;
    response["ok"] = controller.send_command(transport_command);
    return serialize_json(response);
}

}  // namespace

int main(int argc, char** argv) {
    Options options;
    if (!parse_options(argc, argv, options)) {
        print_usage(argv[0]);
        return 2;
    }
    if (options.show_help) {
        print_usage(argv[0]);
        return 0;
    }
    if (options.show_version) {
        std::printf("sendspin-kodi-backend %s\n", SENDSPIN_KODI_VERSION);
        return 0;
    }
    if (options.state_file.empty()) {
        options.state_file = default_state_file();
    }
    if (options.control_socket.empty()) {
        options.control_socket = (std::filesystem::path(options.state_file).parent_path() / "control.sock").string();
    }
    if (!prepare_state_directory(options.state_file)) {
        return 1;
    }

    sendspin::SendspinClient::set_log_level(sendspin::LogLevel::WARN);
    sendspin::PortAudioSink audio_sink;
    if (sendspin::PortAudioSink::max_output_channels() == 0) {
        std::fprintf(stderr, "No PortAudio output device is available\n");
        return 1;
    }

    sendspin::SendspinClientConfig config;
    config.name = options.name;
    config.product_name = "Sendspin Kodi";
    config.manufacturer = "Sendspin";
    config.software_version = SENDSPIN_KODI_VERSION;

    sendspin::FilePersistenceProvider persistence(options.state_file);
    sendspin::SendspinClient client(std::move(config));
    client.set_persistence_provider(&persistence);
    // Match sendspin-cpp-cli's `-a` diagnostic/player mode. Music Assistant
    // 2.10.1 presents a new Kodi client with the Sentinel PSK before pairing;
    // allowing unpaired access lets the server activate playback immediately.
    client.set_unpaired_access_enabled(true);

    sendspin::PlayerRoleConfig player_config;
    player_config.audio_formats = {
        {sendspin::SendspinCodecFormat::PCM, 2, 48000, 16},
        {sendspin::SendspinCodecFormat::PCM, 2, 44100, 16},
        // Prefer PCM on LibreELEC. The bundled micro-FLAC decoder rejects
        // some Music Assistant FLAC frames, while the PCM path is reliable.
        {sendspin::SendspinCodecFormat::FLAC, 2, 48000, 16},
        {sendspin::SendspinCodecFormat::FLAC, 2, 44100, 16},
    };
    auto& player = client.add_player(std::move(player_config));
    player.set_output_delay_adjustable(true);
    auto& controller = client.add_controller();
    auto& metadata = client.add_metadata();

    KodiPlayerListener player_listener(audio_sink, player);
    TrackState track_state;
    KodiMetadataListener metadata_listener(track_state);
    KodiClientListener client_listener;
    HostNetworkProvider network_provider;
    audio_sink.on_frames_played = [&player](uint32_t frames, int64_t timestamp) {
        player.notify_audio_played(frames, timestamp);
    };
    player.set_listener(&player_listener);
    metadata.set_listener(&metadata_listener);
    client.set_listener(&client_listener);
    client.set_network_provider(&network_provider);

    if (!client.start()) {
        std::fprintf(stderr, "sendspin-cpp failed to start\n");
        return 1;
    }
    player.update_volume(options.initial_volume);
    player.update_muted(options.initial_muted);
    audio_sink.set_volume(options.initial_volume);
    audio_sink.set_muted(options.initial_muted);
    if (options.initial_delay_ms.has_value()) {
        player.update_output_delay(*options.initial_delay_ms);
    }
    if (options.print_pairing_token) {
        const auto token = client.pairing_token();
        if (!token.has_value()) {
            std::fprintf(stderr, "Could not obtain a pairing token\n");
            client.stop();
            return 1;
        }
        std::printf("%s\n", token->c_str());
        client.stop();
        return 0;
    }
    if (!options.server_url.empty()) {
        client.connect_to(options.server_url);
    }

    UnixControlSocket control_socket;
    std::string socket_error;
    if (!control_socket.open(options.control_socket, socket_error)) {
        std::fprintf(stderr, "Could not open backend control socket: %s\n", socket_error.c_str());
        client.stop();
        return 1;
    }

    std::signal(SIGINT, stop_signal);
    std::signal(SIGTERM, stop_signal);
    std::fprintf(stderr, "Sendspin backend ready as '%s'; state file: %s\n", options.name.c_str(),
                 options.state_file.c_str());
    std::fprintf(stderr, "Control socket: %s\n", options.control_socket.c_str());
    if (options.server_url.empty()) {
        std::fprintf(stderr, "No outbound server URL configured; waiting for an inbound server connection\n");
    }

    while (running != 0) {
        client.loop();
        control_socket.poll_once([&](const std::string& request) {
            return handle_control_request(request, client, player, controller, metadata, player_listener, track_state);
        });
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    control_socket.close();
    client.stop();
    audio_sink.stop();
    return 0;
}
