import logging
import re
import subprocess

import xbmcaddon
from audio import DockerPlaybackEngine
from kodi import KodiManager


class SendspinServiceController:
    def __init__(self) -> None:
        self.logger = logging.getLogger("sendspin")
        self.playback_engine = DockerPlaybackEngine(
            image_name=xbmcaddon.Addon().getSetting("docker_image_name") or "sendspin-local",
            container_name=xbmcaddon.Addon().getSetting("docker_container_name") or "sendspin-player",
            config_dir=xbmcaddon.Addon().getSetting("docker_config_dir") or "/storage/.config/sendspin",
        )
        self.kodi = KodiManager()
        self.original_kodi_device = None

    def _get_audio_device_id(self, device_string: str) -> str:
        """Extract the global ALSA device index from Kodi's audio device string."""
        # Retrieve the fallback value from settings, defaulting to "0" if the field is empty
        fallback = xbmcaddon.Addon().getSetting("fallback_audio_device") or "0"

        # Check if the device is ALSA; PulseAudio or Bluetooth will trigger the fallback
        if not device_string or "ALSA:" not in device_string:
            self.logger.warning(f"Non-ALSA device detected ({device_string}). Using fallback: {fallback}")
            return fallback

        # Parse card name and device number from the Kodi connection string[cite: 6]
        card_match = re.search(r"CARD=([^,|]+)", device_string)
        dev_match = re.search(r"DEV=(\d+)", device_string)

        if not card_match or not dev_match:
            self.logger.warning(f"Could not parse ALSA string. Using fallback: {fallback}")
            return fallback

        card_name = card_match.group(1)
        dev_num = int(dev_match.group(1))

        # Run aplay -l to map the named card and device to a numerical index[cite: 6]
        try:
            result = subprocess.run(["aplay", "-l"], capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                self.logger.error(f"Failed to run aplay -l: {result.stderr}")
                return fallback

            lines = result.stdout.split("\n")
            devices = []
            current_card = None

            for line in lines:
                if line.startswith("card "):
                    # Extract the card number from lines like 'card 0: PCH [HDA Intel PCH]'[cite: 6]
                    parts = line.split(":", 1)
                    if len(parts) > 0:
                        card_part = parts[0].strip()
                        card_num = int(card_part.split()[1])
                        current_card = card_num
                elif line.strip().startswith("device ") and current_card is not None:
                    # Extract the device number from lines like '  device 3: HDMI 0 [HDMI 0]'[cite: 6]
                    dev_part = line.strip().split(":", 1)[0]
                    dev_num_line = int(dev_part.split()[1])
                    devices.append((current_card, dev_num_line))

            # Match the card name back to its numerical card number[cite: 6]
            card_num = None
            for line in lines:
                if "card " in line and f"[{card_name}]" in line:
                    card_part = line.split(":", 1)[0]
                    card_num = int(card_part.split()[1])
                    break

            if card_num is None:
                self.logger.error(f"Could not find card number for {card_name}. Using fallback.")
                return fallback

            # Find the index of the specific (card, device) tuple in the full list[cite: 6]
            try:
                idx = devices.index((card_num, dev_num))
                return str(idx)
            except ValueError:
                self.logger.error(f"Device {card_num},{dev_num} not found in list. Using fallback.")
                return fallback

        except Exception as e:
            self.logger.error(f"Error during audio device detection: {e}")
            return fallback
        """Extract the global ALSA device index from Kodi's audio device string."""
        if not device_string or "ALSA:" not in device_string:
            return "0"

        # Parse card name and device number
        card_match = re.search(r"CARD=([^,|]+)", device_string)
        dev_match = re.search(r"DEV=(\d+)", device_string)
        if not card_match or not dev_match:
            return "0"

        card_name = card_match.group(1)
        dev_num = int(dev_match.group(1))

        # Run aplay -l to get device list
        try:
            result = subprocess.run(["aplay", "-l"], capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                self.logger.error(f"Failed to run aplay -l: {result.stderr}")
                return "0"

            lines = result.stdout.split("\n")
            devices = []
            current_card = None
            for line in lines:
                if line.startswith("card "):
                    # card 0: PCH [HDA Intel PCH]
                    parts = line.split(":", 1)
                    if len(parts) > 0:
                        card_part = parts[0].strip()
                        card_num = int(card_part.split()[1])
                        current_card = card_num
                elif line.strip().startswith("device ") and current_card is not None:
                    #   device 3: HDMI 0 [HDMI 0]
                    dev_part = line.strip().split(":", 1)[0]
                    dev_num_line = int(dev_part.split()[1])
                    devices.append((current_card, dev_num_line))

            # Find the card number for the card name
            card_num = None
            for line in lines:
                if "card " in line and f"[{card_name}]" in line:
                    card_part = line.split(":", 1)[0]
                    card_num = int(card_part.split()[1])
                    break

            if card_num is None:
                self.logger.error(f"Could not find card number for {card_name}")
                return "0"

            # Find the index of (card_num, dev_num)
            try:
                idx = devices.index((card_num, dev_num))
                return str(idx)
            except ValueError:
                self.logger.error(f"Device {card_num},{dev_num} not found in list")
                return "0"
        except Exception as e:
            self.logger.error(f"Error getting audio device index: {e}")
            return "0"

    async def setup(self) -> None:
        # Capture the current device string[cite: 3, 5]
        self.original_kodi_device = self.kodi.get_audio_output_device()
        self.logger.info(f"Captured original audio device: {self.original_kodi_device}")

        # Extract audio device ID for Docker
        override = xbmcaddon.Addon().getSetting("audio_device_override")
        if override:
            audio_device_id = override
            self.logger.info(f"Using audio device override: {audio_device_id}")
        else:
            audio_device_id = self._get_audio_device_id(self.original_kodi_device)
            self.logger.info(f"Extracted audio device ID: {audio_device_id}")
        self.playback_engine.audio_device = audio_device_id

        # If ALSA is active, move Kodi to an alternate to avoid hardware locking[cite: 1, 5]
        if self.original_kodi_device and "alsa" in self.original_kodi_device.lower():
            self._switch_to_alternate()

        self.playback_engine.start()  #

    def _switch_to_alternate(self):
        # Try common safe fallbacks[cite: 5]
        candidates = ["ALSA:default", "ALSA:sysdefault", "PULSE:default"]
        for candidate in candidates:
            if candidate.lower() != self.original_kodi_device.lower():
                if self.kodi.set_audio_output_device(candidate):
                    self.logger.info(f"Switched Kodi audio to {candidate} to free hardware.")
                    break

    async def cleanup(self) -> None:
        # Stop container and restore audio device[cite: 1, 3, 5]
        self.playback_engine.stop()
        if self.original_kodi_device:
            self.logger.info(f"Restoring audio device to: {self.original_kodi_device}")
            self.kodi.set_audio_output_device(self.original_kodi_device)
        await self.kodi.cleanup()
