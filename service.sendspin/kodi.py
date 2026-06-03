import json
import logging

import xbmc


class KodiManager:
    def __init__(self):
        self.logger = logging.getLogger("sendspin")

    async def cleanup(self) -> None:
        # No background tasks to clean up in script mode
        pass

    def get_setting_value(self, setting_name: str):
        query = {
            "jsonrpc": "2.0",
            "method": "Settings.GetSettingValue",
            "params": {"setting": setting_name},
            "id": 1,
        }
        try:
            response = json.loads(xbmc.executeJSONRPC(json.dumps(query)))
            return response.get("result", {}).get("value")
        except Exception as e:
            self.logger.error(f"Failed to get setting {setting_name}: {e}")
            return None

    def set_setting_value(self, setting_name: str, value) -> bool:
        query = {
            "jsonrpc": "2.0",
            "method": "Settings.SetSettingValue",
            "params": {"setting": setting_name, "value": value},
            "id": 1,
        }
        try:
            response = json.loads(xbmc.executeJSONRPC(json.dumps(query)))
            return response.get("result") == "OK" or response.get("result") is True
        except Exception as e:
            self.logger.error(f"Failed to set setting {setting_name} to {value}: {e}")
            return False

    def get_audio_output_device(self) -> str | None:
        return self.get_setting_value("audiooutput.audiodevice")

    def get_audio_output_device_options(self) -> list[dict]:
        query = {
            "jsonrpc": "2.0",
            "method": "Settings.GetSettings",
            "params": {"level": "expert"},
            "id": 1,
        }
        try:
            response = json.loads(xbmc.executeJSONRPC(json.dumps(query)))
            settings = response.get("result", {}).get("settings", [])
            for setting in settings:
                if setting.get("id") == "audiooutput.audiodevice":
                    return setting.get("options", [])
        except Exception as e:
            self.logger.error(f"Failed to get audio output device options: {e}")
        return []

    def set_audio_output_device(self, device_name: str) -> bool:
        return self.set_setting_value("audiooutput.audiodevice", device_name)

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
