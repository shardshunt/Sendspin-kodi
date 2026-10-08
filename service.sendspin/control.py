import json
import logging
import socket
import urllib.error
import urllib.request


class SendspinControlClient:
    """HTTP client for the Sendspin daemon control API."""

    def __init__(self, base_url: str, timeout: float = 0.75, socket_path: str | None = None) -> None:
        self.logger = logging.getLogger("sendspin")
        self.base_url = (base_url or "http://127.0.0.1:59999").rstrip("/")
        self.timeout = timeout
        self.socket_path = socket_path
        self._logged_unavailable = False

    def command(self, name: str, **params) -> bool:
        payload = {"command": name}
        payload.update(params)
        return self._post("/control", payload)

    def command_response(self, name: str, **params) -> dict | None:
        payload = {"command": name}
        payload.update(params)
        return self._post_json("/control", payload)

    def play(self) -> bool:
        return self.command("play")

    def pause(self) -> bool:
        return self.command("pause")

    def toggle_play_pause(self) -> bool:
        return self.command("toggle_play_pause")

    def next_track(self) -> bool:
        return self.command("next")

    def previous_track(self) -> bool:
        return self.command("previous")

    def release_audio(self) -> bool:
        return self.command("release_audio")

    def acquire_audio(self) -> bool:
        return self.command("acquire_audio")

    def audio_status(self) -> dict | None:
        response = self.command_response("audio_status")
        if not isinstance(response, dict):
            return None
        audio = response.get("audio")
        return audio if isinstance(audio, dict) else None

    def set_volume(self, volume: int, muted: bool = False) -> bool:
        return self.command("set_volume", volume=max(0, min(100, int(volume))), muted=bool(muted))

    def set_delay(self, delay_ms: float) -> bool:
        try:
            value = float(delay_ms)
        except (TypeError, ValueError):
            self.logger.warning("Invalid delay_ms value: %s", delay_ms)
            return False

        if value < 0.0 or value > 5000.0:
            self.logger.warning("delay_ms out of range: %s", value)
            return False

        return self.command("set_delay", delay_ms=value)

    def seek(self, position: float) -> bool:
        return self.command("seek", position=max(0.0, float(position)))

    def get_state(self) -> dict | None:
        return self._get("/state")

    def get_pairing_token(self) -> str | None:
        response = self.command_response("pair_token")
        token = response.get("token") if isinstance(response, dict) else None
        return token if isinstance(token, str) and token else None

    def _get(self, path: str) -> dict | None:
        if self.socket_path:
            return self._request_unix_json({"command": "get_state"})

        request = urllib.request.Request(f"{self.base_url}{path}", method="GET")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if not 200 <= response.status < 300:
                    self.logger.warning("Sendspin state request failed: status=%s", response.status)
                    return None

                self._logged_unavailable = False
                payload = response.read().decode("utf-8")
                return json.loads(payload)
        except urllib.error.HTTPError as e:
            self.logger.warning("Sendspin state request failed: status=%s", e.code)
            return None
        except (json.JSONDecodeError, urllib.error.URLError, TimeoutError, OSError) as e:
            if not self._logged_unavailable:
                self._logged_unavailable = True
                self.logger.warning("Sendspin control API unavailable at %s: %s", self.base_url, e)
            return None

    def _post(self, path: str, payload: dict) -> bool:
        response = self._post_json(path, payload)
        if self.socket_path and isinstance(response, dict) and response.get("ok") is False:
            return False
        return response is not None

    def _post_json(self, path: str, payload: dict) -> dict | None:
        if self.socket_path:
            return self._request_unix_json(payload)

        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if 200 <= response.status < 300:
                    self._logged_unavailable = False
                    body = response.read().decode("utf-8")
                    if not body:
                        return {}
                    return json.loads(body)
                self.logger.warning("Sendspin control command failed: status=%s payload=%s", response.status, payload)
                return None
        except urllib.error.HTTPError as e:
            if e.code == 400:
                self.logger.warning(
                    "Sendspin control API returned 400 Bad Request at %s%s: command might be unsupported. "
                    "Please check that the Sendspin backend supports this command.",
                    self.base_url,
                    path,
                )
            else:
                self.logger.warning("Sendspin control command failed: status=%s payload=%s", e.code, payload)
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if not self._logged_unavailable:
                self._logged_unavailable = True
                self.logger.warning("Sendspin control API unavailable at %s: %s", self.base_url, e)
            return None
        except json.JSONDecodeError as e:
            self.logger.warning("Sendspin control response was not valid JSON: %s", e)
            return None

    def _request_unix_json(self, payload: dict) -> dict | None:
        request = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout)
                connection.connect(self.socket_path)
                connection.sendall(request)
                response = bytearray()
                while len(response) <= 65536:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    response.extend(chunk)
                    if b"\n" in chunk:
                        break
                else:
                    self.logger.warning("Native backend response exceeded 65536 bytes")
                    return None

            payload = json.loads(bytes(response).split(b"\n", 1)[0].decode("utf-8"))
            if not isinstance(payload, dict):
                self.logger.warning("Native backend returned a non-object JSON response")
                return None
            self._logged_unavailable = False
            return payload
        except (json.JSONDecodeError, UnicodeDecodeError, TimeoutError, OSError) as error:
            if not self._logged_unavailable:
                self._logged_unavailable = True
                self.logger.warning("Native backend socket unavailable at %s: %s", self.socket_path, error)
            return None
