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
