# Sendspin Control API

This document defines the local HTTP API expected by the Kodi Sendspin add-on.

The API is served by the `sendspin daemon` process running inside the Docker container. Kodi talks to it over the host network.

## Defaults

- Base URL: `http://127.0.0.1:59999`
- Bind host: `127.0.0.1`
- Port: `59999`
- Transport: HTTP
- Encoding: JSON

The Kodi add-on passes the control endpoint into the container using:

```text
--control-api true
--control-host 127.0.0.1
--control-port 59999
```

## Error Format

Error responses should use JSON where possible:

```json
{
  "error": "Unknown command"
}
```

Recommended status codes:

- `200` or `204`: command accepted and applied
- `400`: invalid JSON, invalid payload, or unknown command
- `404`: unknown endpoint
- `500`: valid command failed inside the daemon

## `POST /control`

Send a playback or volume command to the running daemon.

Request headers:

```http
Content-Type: application/json
```

### Play

```json
{
  "command": "play"
}
```

### Pause

```json
{
  "command": "pause"
}
```

### Toggle Play/Pause

```json
{
  "command": "toggle_play_pause"
}
```

### Next Track

```json
{
  "command": "next"
}
```

### Previous Track

```json
{
  "command": "previous"
}
```

### Set Volume

```json
{
  "command": "set_volume",
  "volume": 42,
  "muted": false
}
```

Fields:

- `volume`: integer from `0` to `100`, using Sendspin's own volume scale.
- `muted`: optional boolean, defaults to `false`.

The daemon should apply the volume immediately and reflect the new value in `GET /state`.

### Seek

```json
{
  "command": "seek",
  "position": 120.5
}
```

Fields:

- `position`: target playback position in seconds, minimum `0`.

## `GET /state`

Return the daemon's current playback state.

Example response:

```json
{
  "track": {
    "title": "Song title",
    "artist": "Artist",
    "album": "Album",
    "artwork_url": "https://example.invalid/art.jpg"
  },
  "playback": {
    "position": 52.3,
    "duration": 210.0,
    "speed": 1
  },
  "volume": {
    "volume": 42,
    "muted": false
  }
}
```

Fields:

- `track.title`: current track title.
- `track.artist`: current artist.
- `track.album`: current album.
- `track.artwork_url`: optional artwork URL.
- `playback.position`: current playback position in seconds.
- `playback.duration`: current track duration in seconds.
- `playback.speed`: `0` for paused, positive value for playing.
- `volume.volume`: current Sendspin volume, integer from `0` to `100`.
- `volume.muted`: current mute state.

During startup or idle states, `track`, `playback`, and `volume` may be empty objects, but the response must remain valid JSON:

```json
{
  "track": {},
  "playback": {},
  "volume": {}
}
```

## Curl Examples

```bash
curl http://127.0.0.1:59999/state
```

```bash
curl -X POST http://127.0.0.1:59999/control \
  -H 'Content-Type: application/json' \
  -d '{"command":"pause"}'
```

```bash
curl -X POST http://127.0.0.1:59999/control \
  -H 'Content-Type: application/json' \
  -d '{"command":"set_volume","volume":42,"muted":false}'
```

## Kodi Plugin Routes

Kodi can forward explicit add-on actions to this API:

```text
plugin://plugin.audio.sendspin?action=play
plugin://plugin.audio.sendspin?action=pause
plugin://plugin.audio.sendspin?action=playpause
plugin://plugin.audio.sendspin?action=next
plugin://plugin.audio.sendspin?action=previous
```

These routes call `POST /control` internally.
