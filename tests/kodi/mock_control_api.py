import json
import signal
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {
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
}
STARTED_AT = time.monotonic()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/state":
            self.send_error(404)
            return

        STATE["playback"]["position"] = int(time.monotonic() - STARTED_AT)
        self._write_json(STATE)

    def do_POST(self):
        if self.path != "/control":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        command = payload.get("command")

        if command == "pause":
            STATE["playback"]["speed"] = 0
        elif command in {"play", "toggle_play_pause"}:
            STATE["playback"]["speed"] = 1
        elif command == "set_volume":
            STATE["volume"]["volume"] = max(0, min(100, int(payload.get("volume", 30))))
            STATE["volume"]["muted"] = bool(payload.get("muted", False))
        elif command == "seek":
            STATE["playback"]["position"] = max(0, int(float(payload.get("position", 0))))

        self._write_json({"ok": True, "command": command})

    def log_message(self, format, *args):
        print(f"{self.address_string()} - {format % args}", flush=True)

    def _write_json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
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
