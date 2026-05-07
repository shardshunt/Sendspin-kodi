import logging
import re
import subprocess

import xbmcaddon
from audio import DockerPlaybackEngine
from kodi import KodiManager


class SendspinServiceController:
    def __init__(self) -> None:
        self.logger = logging.getLogger("sendspin")
        addon = xbmcaddon.Addon()
        self.playback_engine = DockerPlaybackEngine(
            image_name=addon.getSetting("docker_image_name") or "sendspin-local",
            container_name=addon.getSetting("docker_container_name") or "sendspin-player",
            config_dir=addon.getSetting("docker_config_dir") or "/storage/.config/sendspin",
            volume_scale=self._get_volume_scale(addon),
        )
        self.kodi = KodiManager()
        self.original_kodi_device = None

    def _get_volume_scale(self, addon) -> float:
        fallback = 0.3
        setting = addon.getSetting("volume_scale") or str(fallback)
        try:
            scale = float(setting)
        except ValueError:
            self.logger.warning("Invalid volume scale setting '%s'; using %s", setting, fallback)
            return fallback

        if scale <= 0:
            self.logger.warning("Non-positive volume scale setting '%s'; using %s", setting, fallback)
            return fallback

        self.logger.info("Using Kodi to Sendspin volume scale: %s", scale)
        return scale

    def get_new_metadata(self):
        """Returns metadata if updated, otherwise None."""
        if self.playback_engine.metadata_updated:
            # Reset the flag so we only trigger once per update
            self.playback_engine.metadata_updated = False
            return self.playback_engine.current_metadata
        return None

    def _get_audio_device_id(self, device_string: str) -> str:
        """Maps Kodi strings to ALSA indices by matching both hardware numbers and port labels."""
        fallback = xbmcaddon.Addon().getSetting("fallback_audio_device") or "0"

        if not device_string or "ALSA:" not in device_string:
            return fallback

        # 1. Parse Kodi string (e.g., CARD=HDMI,DEV=4)
        card_match = re.search(r"CARD=([^,|]+)", device_string)
        dev_match = re.search(r"DEV=(\d+)", device_string)

        if not card_match or not dev_match:
            return fallback

        target_card_name = card_match.group(1).lower()
        target_dev_num = int(dev_match.group(1))

        try:
            # 2. Capture hardware state
            result = subprocess.run(["aplay", "-l"], capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                return fallback

            lines = result.stdout.split("\n")
            hardware_cards = {}  # {card_idx: "Long Name"}
            global_device_list = []  # List of {'card': idx, 'device': idx, 'label': str}

            for line in lines:
                if line.startswith("card "):
                    card_idx = int(line.split(":")[0].split()[1])

                    # Update card names map
                    name_bracket = re.search(r"\[(.*?)\]", line)
                    if name_bracket and card_idx not in hardware_cards:
                        hardware_cards[card_idx] = name_bracket.group(1)

                    # Extract device index and its label (e.g., "HDMI 4 [Panasonic-TV]")
                    dev_info = re.search(r"device (\d+): (.*)", line)
                    if dev_info:
                        global_device_list.append(
                            {"card": card_idx, "device": int(dev_info.group(1)), "label": dev_info.group(2)}
                        )

            # 3. Match the card
            matched_card_idx = None
            for idx, hw_name in hardware_cards.items():
                if target_card_name in hw_name.lower() or hw_name.lower() in target_card_name:
                    matched_card_idx = idx
                    break

            if matched_card_idx is None:
                self.logger.error(f"Could not match card '{target_card_name}'.")
                return fallback

            # 4. Match the device (Direct Index vs Label Search)
            final_device_idx = None

            # Filter global list to only devices on our matched card
            card_devices = [d for d in global_device_list if d["card"] == matched_card_idx]

            # Step A: Look for exact numerical device match
            for d in card_devices:
                if d["device"] == target_dev_num:
                    final_device_idx = d["device"]
                    break

            # Step B: If no exact index, search for the target number in the labels
            if final_device_idx is None:
                target_str = str(target_dev_num)
                for d in card_devices:
                    # Matches if "4" appears in "HDMI 4 [Panasonic-TV]"
                    if target_str in d["label"]:
                        self.logger.info(
                            f"Matched Kodi DEV={target_str} to ALSA Device {d['device']} via label: {d['label']}"
                        )
                        final_device_idx = d["device"]
                        break

            if final_device_idx is None:
                self.logger.error(f"Device {target_dev_num} not found by index or label on Card {matched_card_idx}.")
                return fallback

            # 5. Calculate global sequential index for Docker
            try:
                # We need the index of the tuple in the full global list
                docker_idx = next(
                    i
                    for i, d in enumerate(global_device_list)
                    if d["card"] == matched_card_idx and d["device"] == final_device_idx
                )
                return str(docker_idx)
            except StopIteration:
                return fallback

        except Exception as e:
            self.logger.error(f"Robust mapping failed: {e}")
            return fallback

    async def setup(self) -> None:
        # Capture the current device string[cite: 3, 5]
        self.original_kodi_device = self.kodi.get_audio_output_device()
        self.logger.info(f"Captured original audio device: {self.original_kodi_device}")

        kodi_volume = self.kodi.get_volume_state()
        self.playback_engine.configure_volume_sync(kodi_volume["volume"], kodi_volume["muted"])

        # Extract audio device ID for Docker
        override = xbmcaddon.Addon().getSetting("audio_device_override")
        if override:
            audio_device_id = override
            self.logger.info(f"Using audio device override: {audio_device_id}")
        elif self.original_kodi_device is not None:
            audio_device_id = self._get_audio_device_id(self.original_kodi_device)
            self.logger.info(f"Extracted audio device ID: {audio_device_id}")
        else:
            audio_device_id = xbmcaddon.Addon().getSetting("fallback_audio_device") or "0"
            self.logger.warning(f"No original Kodi device found; using fallback: {audio_device_id}")
        self.playback_engine.audio_device = audio_device_id

        # If ALSA is active, move Kodi to an alternate to avoid hardware locking[cite: 1, 5]
        if self.original_kodi_device and "alsa" in self.original_kodi_device.lower():
            self._switch_to_alternate()

        self.playback_engine.start()

    def _switch_to_alternate(self):
        # Try common safe fallbacks
        candidates = ["ALSA:default", "ALSA:sysdefault", "PULSE:default"]
        for candidate in candidates:
            if candidate.lower() != self.original_kodi_device.lower():
                if self.kodi.set_audio_output_device(candidate):
                    self.logger.info(f"Switched Kodi audio to {candidate} to free hardware.")
                    break

    def get_kodi_volume_state(self):
        return self.kodi.get_volume_state()

    def get_sendspin_volume(self):
        return self.playback_engine.read_volume_state()

    def apply_sendspin_volume_to_kodi(self, volume):
        kodi_volume = self.playback_engine.sendspin_to_kodi_volume(volume)
        self.kodi.set_muted(kodi_volume == 0)
        self.kodi.set_volume(kodi_volume)
        return kodi_volume

    def apply_kodi_volume_to_sendspin(self, volume_state):
        return self.playback_engine.write_kodi_volume_to_settings(
            volume_state["volume"],
            volume_state["muted"],
        )

    async def cleanup(self) -> None:
        # Stop container and restore audio device[cite: 1, 3, 5]
        self.playback_engine.stop()
        if self.original_kodi_device:
            self.logger.info(f"Restoring audio device to: {self.original_kodi_device}")
            self.kodi.set_audio_output_device(self.original_kodi_device)
        await self.kodi.cleanup()
