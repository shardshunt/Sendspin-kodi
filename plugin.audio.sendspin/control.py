import json
import logging
import urllib.error
import urllib.request


class SendspinControlClient:
    """HTTP client for the Sendspin daemon control API."""

    def __init__(self, base_url: str, timeout: float = 0.75) -> None:
        self.logger = logging.getLogger("sendspin")
        self.base_url = (base_url or "http://127.0.0.1:59999").rstrip("/")
        self.timeout = timeout
        self._logged_unavailable = False

    def command(self, name: str, **params) -> bool:
        payload = {"command": name}
        payload.update(params)
        return self._post("/control", payload)

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

    def set_volume(self, volume: int, muted: bool = False) -> bool:
        return self.command("set_volume", volume=max(0, min(100, int(volume))), muted=bool(muted))

    def seek(self, position: float) -> bool:
        return self.command("seek", position=max(0.0, float(position)))

    def _post(self, path: str, payload: dict) -> bool:
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
                    return True
                self.logger.warning("Sendspin control command failed: status=%s payload=%s", response.status, payload)
                return False
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if not self._logged_unavailable:
                self._logged_unavailable = True
                self.logger.warning("Sendspin control API unavailable at %s: %s", self.base_url, e)
            return False
