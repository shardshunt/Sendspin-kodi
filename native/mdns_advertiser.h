#pragma once

#include <avahi-client/client.h>
#include <avahi-client/publish.h>
#include <avahi-common/thread-watch.h>

#include <cstdint>
#include <string>

class MdnsAdvertiser final {
public:
    MdnsAdvertiser() = default;
    ~MdnsAdvertiser();

    MdnsAdvertiser(const MdnsAdvertiser&) = delete;
    MdnsAdvertiser& operator=(const MdnsAdvertiser&) = delete;

    bool start(const std::string& name, uint16_t port, const std::string& path);
    void stop();

private:
    static void on_client_state(AvahiClient* client, AvahiClientState state, void* context);
    static void on_entry_group_state(AvahiEntryGroup* group, AvahiEntryGroupState state, void* context);

    bool publish_service(AvahiClient* client);
    bool retry_with_alternative_name(AvahiClient* client);

    AvahiThreadedPoll* poll_{nullptr};
    AvahiClient* client_{nullptr};
    AvahiEntryGroup* group_{nullptr};
    std::string name_;
    std::string path_;
    uint16_t port_{0};
    unsigned name_collision_count_{0};
    bool poll_started_{false};
};
