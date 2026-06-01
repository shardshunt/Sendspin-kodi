import json
import signal
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def initial_state():
    return {
        "track": {
            "title": "Container Smoke Test",
            "artist": "Sendspin",
            "album": "Kodi Harness",
            "artwork_url": "",
        },
        "playback": {
            "position": 0,
            "duration": 3600,
            "speed": 1,
        },
        "volume": {
            "volume": 30,
            "muted": False,
        },
        "audio": {
            "released": True,
            "stream_active": False,
        },
    }


STATE = initial_state()
EVENTS = []
STARTED_AT = time.monotonic()


def read_json(request):
    length = int(request.headers.get("Content-Length", "0"))
    if length == 0:
        return {}
    return json.loads(request.rfile.read(length) or b"{}")


def record_event(event_type, payload):
    EVENTS.append({"type": event_type, "payload": payload, "time": round(time.monotonic() - STARTED_AT, 3)})


def state_section(name):
    section = STATE.get(name)
    if not isinstance(section, dict):
        section = {}
        STATE[name] = section
    return section


def apply_command(command, payload):
    if command == "pause":
        state_section("playback")["speed"] = 0
    elif command in {"play", "toggle_play_pause"}:
        state_section("playback")["speed"] = 1
    elif command == "next":
        STATE["track"] = {
            "title": "Next Track",
            "artist": "Sendspin",
            "album": "Kodi Harness",
            "artwork_url": "",
        }
    elif command == "previous":
        STATE["track"] = {
            "title": "Previous Track",
            "artist": "Sendspin",
            "album": "Kodi Harness",
            "artwork_url": "",
        }
    elif command == "set_volume":
        volume = state_section("volume")
        volume["volume"] = max(0, min(100, int(payload.get("volume", 30))))
        volume["muted"] = bool(payload.get("muted", False))
    elif command == "seek":
        state_section("playback")["position"] = max(0, int(float(payload.get("position", 0))))
    elif command == "release_audio":
        audio = state_section("audio")
        audio["released"] = True
        audio["stream_active"] = False
    elif command == "acquire_audio":
        audio = state_section("audio")
        audio["released"] = False
        audio["stream_active"] = True
    elif command == "audio_status":
        pass


def replace_state(payload):
    STATE.clear()
    STATE.update(payload)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path == "/state":
                playback = STATE.get("playback")
                if isinstance(playback, dict) and playback.get("speed", 0) > 0:
                    playback["position"] = int(time.monotonic() - STARTED_AT)
                record_event("state", {})
                self._write_json(STATE)
                return

            if self.path == "/test/events":
                self._write_json({"events": EVENTS})
                return

            self.send_error(404)
        except Exception as e:
            self._write_json({"error": str(e)}, status=500)

    def do_POST(self):
        try:
            if self.path == "/control":
                payload = read_json(self)
                command = payload.get("command")
                record_event("control", payload)
                apply_command(command, payload)
                response = {"ok": True, "command": command}
                if command == "audio_status":
                    response["audio"] = state_section("audio")
                self._write_json(response)
                return

            if self.path == "/test/reset":
                replace_state(initial_state())
                EVENTS.clear()
                self._write_json({"ok": True})
                return

            if self.path == "/test/state":
                replace_state(read_json(self))
                self._write_json({"ok": True, "state": STATE})
                return

            self.send_error(404)
        except Exception as e:
            self._write_json({"error": str(e)}, status=500)

    def log_message(self, format, *args):
        print(f"{self.address_string()} - {format % args}", flush=True)

    def _write_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 59999), Handler)

    def stop_server(signum, frame):
        server.shutdown()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    server.serve_forever()
