#include "mdns_advertiser.h"

#include <avahi-client/client.h>
#include <avahi-client/publish.h>
#include <avahi-common/alternative.h>
#include <avahi-common/error.h>
#include <avahi-common/malloc.h>
#include <avahi-common/strlst.h>

#include <cstdio>

namespace {

constexpr char kSendspinServiceType[] = "_sendspin._tcp";
constexpr unsigned kMaximumNameCollisions = 10;

}  // namespace

MdnsAdvertiser::~MdnsAdvertiser() {
    stop();
}

bool MdnsAdvertiser::start(const std::string& name, uint16_t port, const std::string& path) {
    if (poll_ != nullptr || name.empty() || path.empty() || port == 0) {
        return false;
    }

    name_ = name;
    path_ = path;
    port_ = port;
    poll_ = avahi_threaded_poll_new();
    if (poll_ == nullptr) {
        std::fprintf(stderr, "mDNS: could not create Avahi poll thread\n");
        return false;
    }

    int error = 0;
    client_ = avahi_client_new(avahi_threaded_poll_get(poll_), AVAHI_CLIENT_NO_FAIL, on_client_state, this, &error);
    if (client_ == nullptr) {
        std::fprintf(stderr, "mDNS: could not create Avahi client: %s\n", avahi_strerror(error));
        stop();
        return false;
    }

    if (avahi_threaded_poll_start(poll_) < 0) {
        std::fprintf(stderr, "mDNS: could not start Avahi poll thread\n");
        stop();
        return false;
    }
    poll_started_ = true;
    return true;
}

void MdnsAdvertiser::stop() {
    if (poll_ != nullptr && poll_started_) {
        avahi_threaded_poll_stop(poll_);
        poll_started_ = false;
    }
    if (group_ != nullptr) {
        avahi_entry_group_free(group_);
        group_ = nullptr;
    }
    if (client_ != nullptr) {
        avahi_client_free(client_);
        client_ = nullptr;
    }
    if (poll_ != nullptr) {
        avahi_threaded_poll_free(poll_);
        poll_ = nullptr;
    }
}

void MdnsAdvertiser::on_client_state(AvahiClient* client, AvahiClientState state, void* context) {
    auto* advertiser = static_cast<MdnsAdvertiser*>(context);
    advertiser->client_ = client;

    if (state == AVAHI_CLIENT_S_RUNNING) {
        if (advertiser->group_ == nullptr || avahi_entry_group_is_empty(advertiser->group_)) {
            advertiser->publish_service(client);
        }
    } else if (state == AVAHI_CLIENT_S_COLLISION && advertiser->group_ != nullptr) {
        avahi_entry_group_reset(advertiser->group_);
    } else if (state == AVAHI_CLIENT_FAILURE) {
        std::fprintf(stderr, "mDNS: Avahi client failed: %s\n", avahi_strerror(avahi_client_errno(client)));
    }
}

void MdnsAdvertiser::on_entry_group_state(AvahiEntryGroup*, AvahiEntryGroupState state, void* context) {
    auto* advertiser = static_cast<MdnsAdvertiser*>(context);

    if (state == AVAHI_ENTRY_GROUP_ESTABLISHED) {
        std::fprintf(stderr, "mDNS: Advertising %s on port %u (name: %s, path: %s)\n", kSendspinServiceType,
                     advertiser->port_, advertiser->name_.c_str(), advertiser->path_.c_str());
    } else if (state == AVAHI_ENTRY_GROUP_COLLISION) {
        advertiser->retry_with_alternative_name(advertiser->client_);
    } else if (state == AVAHI_ENTRY_GROUP_FAILURE && advertiser->client_ != nullptr) {
        std::fprintf(stderr, "mDNS: service registration failed: %s\n",
                     avahi_strerror(avahi_client_errno(advertiser->client_)));
    }
}

bool MdnsAdvertiser::publish_service(AvahiClient* client) {
    if (group_ == nullptr) {
        group_ = avahi_entry_group_new(client, on_entry_group_state, this);
        if (group_ == nullptr) {
            std::fprintf(stderr, "mDNS: could not create service group: %s\n",
                         avahi_strerror(avahi_client_errno(client)));
            return false;
        }
    }

    AvahiStringList* txt = avahi_string_list_add_pair(nullptr, "path", path_.c_str());
    if (txt == nullptr) {
        std::fprintf(stderr, "mDNS: could not create Sendspin TXT records\n");
        return false;
    }
    txt = avahi_string_list_add_pair(txt, "name", name_.c_str());
    if (txt == nullptr) {
        std::fprintf(stderr, "mDNS: could not add the player name TXT record\n");
        return false;
    }

    int result = avahi_entry_group_add_service_strlst(group_, AVAHI_IF_UNSPEC, AVAHI_PROTO_UNSPEC,
                                                       static_cast<AvahiPublishFlags>(0), name_.c_str(),
                                                       kSendspinServiceType, nullptr, nullptr, port_, txt);
    avahi_string_list_free(txt);
    if (result == AVAHI_ERR_COLLISION) {
        return retry_with_alternative_name(client);
    }
    if (result < 0) {
        std::fprintf(stderr, "mDNS: could not add Sendspin service: %s\n", avahi_strerror(result));
        return false;
    }

    result = avahi_entry_group_commit(group_);
    if (result < 0) {
        std::fprintf(stderr, "mDNS: could not commit Sendspin service: %s\n", avahi_strerror(result));
        return false;
    }
    return true;
}

bool MdnsAdvertiser::retry_with_alternative_name(AvahiClient* client) {
    if (name_collision_count_ >= kMaximumNameCollisions) {
        std::fprintf(stderr, "mDNS: too many service-name collisions; advertisement stopped\n");
        return false;
    }

    char* alternative_name = avahi_alternative_service_name(name_.c_str());
    if (alternative_name == nullptr) {
        std::fprintf(stderr, "mDNS: could not generate an alternate service name\n");
        return false;
    }
    name_ = alternative_name;
    avahi_free(alternative_name);
    ++name_collision_count_;

    if (group_ != nullptr && avahi_entry_group_reset(group_) < 0) {
        std::fprintf(stderr, "mDNS: could not reset the colliding service group\n");
        return false;
    }
    return publish_service(client);
}
