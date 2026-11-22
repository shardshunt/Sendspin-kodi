#pragma once

#include <functional>
#include <string>

class UnixControlSocket {
public:
    using RequestHandler = std::function<std::string(const std::string&)>;

    UnixControlSocket() = default;
    ~UnixControlSocket();

    UnixControlSocket(const UnixControlSocket&) = delete;
    UnixControlSocket& operator=(const UnixControlSocket&) = delete;

    bool open(const std::string& path, std::string& error);
    void poll_once(const RequestHandler& handler);
    void close();

private:
    int server_socket_ = -1;
    std::string path_;
};
