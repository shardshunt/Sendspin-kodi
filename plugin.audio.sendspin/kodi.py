import json
import logging

import xbmc


class KodiManager:
    def __init__(self):
        self.logger = logging.getLogger("sendspin")

    async def cleanup(self) -> None:
        # No background tasks to clean up in script mode
        pass

    def get_audio_output_device(self) -> str | None:
        # Retrieve the current setting via JSON-RPC[cite: 3]
        query = {
            "jsonrpc": "2.0",
            "method": "Settings.GetSettingValue",
            "params": {"setting": "audiooutput.audiodevice"},
            "id": 1,
        }
        response = json.loads(xbmc.executeJSONRPC(json.dumps(query)))
        return response.get("result", {}).get("value")

    def set_audio_output_device(self, device_name: str) -> bool:
        # Update the audio setting via JSON-RPC[cite: 3]
        query = {
            "jsonrpc": "2.0",
            "method": "Settings.SetSettingValue",
            "params": {"setting": "audiooutput.audiodevice", "value": device_name},
            "id": 1,
        }
        response = json.loads(xbmc.executeJSONRPC(json.dumps(query)))
        return response.get("result")

    def get_volume_state(self) -> dict[str, int | bool]:
        query = {
            "jsonrpc": "2.0",
            "method": "Application.GetProperties",
            "params": {"properties": ["volume", "muted"]},
            "id": 1,
        }
        response = json.loads(xbmc.executeJSONRPC(json.dumps(query)))
        result = response.get("result", {})
        return {"volume": int(result.get("volume", 100)), "muted": bool(result.get("muted", False))}

    def set_volume(self, volume: int) -> bool:
        query = {
            "jsonrpc": "2.0",
            "method": "Application.SetVolume",
            "params": {"volume": max(0, min(100, int(volume)))},
            "id": 1,
        }
        response = json.loads(xbmc.executeJSONRPC(json.dumps(query)))
        return "result" in response

    def set_muted(self, muted: bool) -> bool:
        query = {
            "jsonrpc": "2.0",
            "method": "Application.SetMute",
            "params": {"mute": bool(muted)},
            "id": 1,
        }
        response = json.loads(xbmc.executeJSONRPC(json.dumps(query)))
        return "result" in response
