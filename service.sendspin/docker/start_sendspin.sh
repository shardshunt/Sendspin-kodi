#!/bin/bash

# Ensure the config directory exists for persistence
mkdir -p /storage/.config/sendspin

# Stop and remove any existing container with the same name
docker stop sendspin-player 2>/dev/null
docker rm sendspin-player 2>/dev/null

# Execute the container
docker run -d \
  --name sendspin-player \
  --restart unless-stopped \
  --net host \
  --privileged \
  --device /dev/snd:/dev/snd \
  -v /storage/.config/sendspin:/root/.config/sendspin \
  -v /run/pulse:/run/pulse \
  -e PULSE_SERVER=unix:/run/pulse/native \
  sendspin-local \
  daemon
