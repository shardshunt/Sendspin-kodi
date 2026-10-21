# Kodi Container Smoke Test

This harness runs a headless Kodi Omega container with the local add-on bind-mounted into Kodi's add-ons directory. It also starts a tiny mock Sendspin control API so the add-on can exercise its Kodi lifecycle without a playback backend.

It is intended for quick bug reproduction and log collection. It does not replace testing on the target LibreELEC/CoreELEC box, because audio-device locking and ALSA enumeration are host-specific.

## Run

On Bazzite DX, the default path is Podman-native and does not require Docker or Compose:

```bash
tests/kodi/smoke.sh
```

That creates a Podman pod named `sendspin-kodi-test`, starts the mock API and Kodi in that pod, opens `plugin://service.sendspin/`, then prints the last Kodi log lines.

For a broader API scenario run:

```bash
tests/kodi/api_scenarios.sh
```

That run exercises the documented plugin routes, direct control commands, empty state payloads, full track/playback/volume payloads, and paused playback.

Useful follow-up commands:

```bash
podman logs -f sendspin-kodi-test-kodi
podman logs -f sendspin-control-mock
podman pod rm -f sendspin-kodi-test
```

Kodi's web interface is exposed on <http://127.0.0.1:18080>. The JSON-RPC endpoint is <http://127.0.0.1:18080/jsonrpc>.

## Runtime Selection

The smoke script defaults to Podman when `podman` is available. You can force a mode:

```bash
SENDSPIN_KODI_RUNTIME=podman tests/kodi/smoke.sh
SENDSPIN_KODI_RUNTIME=compose tests/kodi/smoke.sh
```

The Compose mode is kept for machines with Docker Compose or Podman Compose:

```bash
docker compose -f tests/kodi/docker-compose.yml logs -f kodi
docker compose -f tests/kodi/docker-compose.yml down
```

## What It Covers

- Kodi can boot with this add-on mounted.
- Kodi can enable and resolve `service.sendspin`.
- The add-on can call `/state` and `/control` on the configured control API.
- Kodi logs contain the add-on startup and cleanup messages.
- `api_scenarios.sh` checks all documented control commands: `play`, `pause`, `toggle_play_pause`, `next`, `previous`, `set_volume`, and `seek`.

## What It Skips

The mock API exercises Kodi-side routes and player synchronization only. Use
`native_harness.sh` for the real backend and audio-handoff path.

Build the ABI-matched backend with:

```bash
scripts/build_native_backend_ubuntu_container.sh
```

Then run:

```bash
SENDSPIN_SERVER_URL=ws://192.168.0.161:8927/sendspin tests/kodi/native_harness.sh
```

The default ALSA target is `hw:2,0` (the USB device on the test host). Set
`ALSA_DEVICE=hw:1,3` or another `hw:CARD,DEVICE` value to select a different
output.

If rootless Podman still hides the host ALSA cards, retry the hardware test
with the test container privileged:

```bash
KODI_PRIVILEGED=true \
SENDSPIN_SERVER_URL=ws://192.168.0.161:8927/sendspin \
  tests/kodi/native_harness.sh
```

The native harness uses Podman's host user namespace and retained host groups
so the container process can access `/dev/snd` through the host's device ACLs.
If rootless Podman exposes the device nodes but ALSA still reports `Unknown
PCM`, run the harness rootful with `PODMAN_ROOTFUL=true`.
