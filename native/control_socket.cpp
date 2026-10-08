#include "control_socket.h"

#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <filesystem>
#include <string>
#include <sys/types.h>
#include <sys/time.h>

namespace {

constexpr size_t max_request_bytes = 65536;

std::string read_request(int client_socket) {
    std::string request;
    char buffer[1024];
    while (request.size() < max_request_bytes) {
        const ssize_t received = ::recv(client_socket, buffer, sizeof(buffer), 0);
        if (received < 0) {
            if (errno == EINTR) {
                continue;
            }
            return {};
        }
        if (received == 0) {
            return request;
        }
        request.append(buffer, static_cast<size_t>(received));
        const size_t newline = request.find('\n');
        if (newline != std::string::npos) {
            request.resize(newline);
            return request;
        }
    }
    return {};
}

bool write_response(int client_socket, std::string response) {
    response.push_back('\n');
    size_t sent = 0;
    while (sent < response.size()) {
        const ssize_t written = ::send(client_socket, response.data() + sent, response.size() - sent, MSG_NOSIGNAL);
        if (written < 0) {
            if (errno == EINTR) {
                continue;
            }
            return false;
        }
        sent += static_cast<size_t>(written);
    }
    return true;
}

}  // namespace

UnixControlSocket::~UnixControlSocket() {
    this->close();
}

bool UnixControlSocket::open(const std::string& path, std::string& error) {
    if (path.size() >= sizeof(sockaddr_un::sun_path)) {
        error = "control socket path is too long";
        return false;
    }

    const std::filesystem::path socket_path(path);
    const auto parent = socket_path.parent_path();
    std::error_code filesystem_error;
    if (!parent.empty()) {
        std::filesystem::create_directories(parent, filesystem_error);
        if (filesystem_error) {
            error = "cannot create control socket directory: " + filesystem_error.message();
            return false;
        }
    }

    struct stat existing_status {};
    if (::lstat(path.c_str(), &existing_status) == 0) {
        if (!S_ISSOCK(existing_status.st_mode) || existing_status.st_uid != ::getuid()) {
            error = "control socket path exists and is not an owned socket";
            return false;
        }
        const int probe_socket = ::socket(AF_UNIX, SOCK_STREAM, 0);
        if (probe_socket < 0) {
            error = std::string("cannot create control socket probe: ") + std::strerror(errno);
            return false;
        }
        sockaddr_un probe_address {};
        probe_address.sun_family = AF_UNIX;
        std::memcpy(probe_address.sun_path, path.c_str(), path.size() + 1);
        const int probe_result = ::connect(probe_socket, reinterpret_cast<sockaddr*>(&probe_address),
                                           sizeof(probe_address));
        const int probe_error = errno;
        ::close(probe_socket);
        if (probe_result == 0 || (probe_error != ECONNREFUSED && probe_error != ENOENT)) {
            error = "control socket is already active or cannot be safely replaced";
            return false;
        }
        if (::unlink(path.c_str()) != 0) {
            error = std::string("cannot remove stale control socket: ") + std::strerror(errno);
            return false;
        }
    } else if (errno != ENOENT) {
        error = std::string("cannot inspect control socket path: ") + std::strerror(errno);
        return false;
    }

    const int server_socket = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (server_socket < 0) {
        error = std::string("cannot create control socket: ") + std::strerror(errno);
        return false;
    }

    sockaddr_un address {};
    address.sun_family = AF_UNIX;
    std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
    const mode_t old_mask = ::umask(0077);
    const int bind_result = ::bind(server_socket, reinterpret_cast<sockaddr*>(&address), sizeof(address));
    const int bind_error = errno;
    ::umask(old_mask);
    if (bind_result != 0) {
        error = std::string("cannot bind control socket: ") + std::strerror(bind_error);
        ::close(server_socket);
        return false;
    }
    if (::listen(server_socket, 8) != 0 || ::fcntl(server_socket, F_SETFL, O_NONBLOCK) != 0) {
        error = std::string("cannot listen on control socket: ") + std::strerror(errno);
        ::close(server_socket);
        ::unlink(path.c_str());
        return false;
    }

    this->server_socket_ = server_socket;
    this->path_ = path;
    return true;
}

void UnixControlSocket::poll_once(const RequestHandler& handler) {
    if (this->server_socket_ < 0) {
        return;
    }
    pollfd listener {this->server_socket_, POLLIN, 0};
    if (::poll(&listener, 1, 0) <= 0 || (listener.revents & POLLIN) == 0) {
        return;
    }

    const int client_socket = ::accept(this->server_socket_, nullptr, nullptr);
    if (client_socket < 0) {
        return;
    }
    timeval timeout {0, 250000};
    ::setsockopt(client_socket, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    ::setsockopt(client_socket, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    const std::string request = read_request(client_socket);
    if (!request.empty()) {
        try {
            write_response(client_socket, handler(request));
        } catch (...) {
            write_response(client_socket, R"({"ok":false,"error":"control handler failed"})");
        }
    }
    ::close(client_socket);
}

void UnixControlSocket::close() {
    if (this->server_socket_ >= 0) {
        ::close(this->server_socket_);
        this->server_socket_ = -1;
    }
    if (!this->path_.empty()) {
        ::unlink(this->path_.c_str());
        this->path_.clear();
    }
}
