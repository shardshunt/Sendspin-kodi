# Kodi Container Smoke Test

This harness runs a headless Kodi Omega container with the local add-on bind-mounted into Kodi's add-ons directory. It also starts a tiny mock Sendspin control API so the add-on can exercise its Kodi lifecycle without starting the real Sendspin Docker backend.

It is intended for quick bug reproduction and log collection. It does not replace testing on the target LibreELEC/CoreELEC box, because audio device locking, ALSA enumeration, and Docker socket behavior are host-specific.

## Run

On Bazzite DX, the default path is Podman-native and does not require Docker or Compose:

```bash
tests/kodi/smoke.sh
```

That creates a Podman pod named `sendspin-kodi-test`, starts the mock API and Kodi in that pod, opens `plugin://plugin.audio.sendspin/`, then prints the last Kodi log lines.

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
- Kodi can enable and resolve `plugin.audio.sendspin`.
- The add-on can call `/state` and `/control` on the configured control API.
- Kodi logs contain the add-on startup and cleanup messages.
- `api_scenarios.sh` checks all documented control commands: `play`, `pause`, `toggle_play_pause`, `next`, `previous`, `set_volume`, and `seek`.

## What It Skips

The test settings set `docker_start_enabled=false`, so the add-on does not start the real Sendspin playback container. Use this mode for plugin and Kodi integration debugging. For full host integration, test on the target machine with Docker and `/dev/snd` available.
