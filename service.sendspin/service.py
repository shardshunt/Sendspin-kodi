import asyncio
import logging
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET

import xbmcaddon
import xbmcvfs
from audio import DockerPlaybackEngine
from control import SendspinControlClient
from kodi import KodiManager


class SendspinServiceController:
    def __init__(self) -> None:
        self.logger = logging.getLogger("sendspin")
        addon = xbmcaddon.Addon()
        control_url = addon.getSetting("control_url") or self._control_url_from_port(addon)
        config_dir = self._get_writable_config_dir(addon)
        self.playback_engine = DockerPlaybackEngine(
            image_name=addon.getSetting("docker_image_name") or "ghcr.io/shardshunt/sendspin-cli-for-sendspin-kodi",
            container_name=addon.getSetting("docker_container_name") or "sendspin-player",
            config_dir=config_dir,
            image_version=self._get_image_version(addon),
            volume_scale=self._get_volume_scale(addon),
            control_url=control_url,
        )
        self.docker_start_enabled = addon.getSetting("docker_start_enabled") != "false"
        self.control = SendspinControlClient(control_url)
        self.kodi = KodiManager()
        self.original_kodi_device = None
        self._suppress_kodi_player_events_until = 0.0
        self._last_applied_delay_ms = None
        self._profile_settings_missing_logged = False
        self._audio_claimed = False

        # Check for version mismatch due to stale persisted settings
        addon_version = addon.getAddonInfo("version")
        image_version = addon.getSetting("docker_image_version")
        if image_version and image_version != addon_version:
            self.logger.warning(
                "Version mismatch detected! Add-on version is '%s' but configured Docker image version is '%s'. "
                "This is likely due to stale persisted settings. Please go to Add-on settings and choose 'Reset to defaults', "
                "or manually update the Docker image version to '%s' to prevent command errors.",
                addon_version,
                image_version,
                addon_version,
            )

    def _control_url_from_port(self, addon) -> str:
        port = addon.getSetting("proxy_port") or "59999"
        return f"http://127.0.0.1:{port}"

    def _get_writable_config_dir(self, addon) -> str:
        config_dir = addon.getSetting("docker_config_dir") or "/storage/.config/sendspin"

        # Check if the directory (or its closest existing parent) is writable
        is_writable = False
        try:
            test_dir = os.path.abspath(config_dir)
            parent = test_dir
            # Traverse up to find the first existing directory
            while parent and parent != os.path.dirname(parent) and not os.path.exists(parent):
                parent = os.path.dirname(parent)
            if parent and os.path.exists(parent) and os.access(parent, os.W_OK):
                is_writable = True
        except Exception:
            is_writable = False

        if is_writable:
            return config_dir

        # Fallback to addon profile directory
        profile_path = addon.getAddonInfo("profile")
        if profile_path:
            try:
                real_profile = xbmcvfs.translatePath(profile_path)
                if real_profile:
                    fallback_dir = os.path.join(real_profile, "sendspin")
                    self.logger.warning(
                        "Configured Docker config directory '%s' is not writable or cannot be created. "
                        "Falling back to profile directory: '%s'",
                        config_dir,
                        fallback_dir,
                    )
                    return fallback_dir
            except Exception as e:
                self.logger.warning("Failed to translate profile path '%s': %s", profile_path, e)

        # Ultimate fallback to user home directory
        fallback_dir = os.path.expanduser("~/.config/sendspin")
        self.logger.warning(
            "Configured Docker config directory '%s' is not writable. Falling back to user home: '%s'",
            config_dir,
            fallback_dir,
        )
        return fallback_dir

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

    def _parse_delay_value(self, value: str) -> float:
        fallback = 0.0
        try:
            delay = float(value)
        except (TypeError, ValueError):
            self.logger.warning("Invalid delay_ms setting '%s'; using %s", value, fallback)
            return fallback

        if delay < 0.0:
            self.logger.warning("Negative delay_ms setting '%s'; clipping to 0", delay)
            return 0.0
        if delay > 5000.0:
            self.logger.warning("delay_ms setting '%s' above max; clipping to 5000", delay)
            return 5000.0

        return delay

    def _get_image_version(self, addon) -> str:
        version_file = os.path.join(addon.getAddonInfo("path"), "docker_image_version.txt")
        try:
            with open(version_file, encoding="utf-8") as file:
                image_version = file.read().strip()
        except OSError as exc:
            self.logger.warning(
                "Could not read Docker image version file %s: %s; using untagged image",
                version_file,
                exc,
            )
            return ""

        if image_version:
            self.logger.info("Using Docker image version from %s: %s", version_file, image_version)
            return image_version

        self.logger.warning(
            "Docker image version file %s is empty; using untagged image",
            version_file,
        )
        return ""

    def _get_delay_ms(self, addon) -> float:
        fallback = 0.0
        setting = addon.getSetting("delay_ms") or str(fallback)
        return self._parse_delay_value(setting)

    def _get_delay_ms_from_profile(self, path: str) -> float:
        try:
            tree = ET.parse(path)
            root = tree.getroot()
            for setting in root.findall("setting"):
                if setting.get("id") == "delay_ms" and setting.text is not None:
                    return self._parse_delay_value(setting.text.strip())
        except (ET.ParseError, FileNotFoundError, OSError) as e:
            # Avoid log spam: only report the missing profile settings file once per session
            if not getattr(self, "_profile_settings_missing_logged", False):
                # Collect diagnostics to help identify why the file isn't readable
                try:
                    exists = os.path.exists(path)
                except Exception:
                    exists = False

                extra = []
                if not exists and isinstance(path, str) and path.startswith("special://"):
                    translated = None
                    translated_exc = None
                    try:
                        translated = xbmcvfs.translatePath(path)
                    except Exception as te:
                        translated = None
                        try:
                            translated_exc = str(te)
                        except Exception:
                            translated_exc = "<unrepresentable>"
                    if translated:
                        try:
                            translated_exists = os.path.exists(translated)
                        except Exception:
                            translated_exists = False
                        extra.append(f"translated={translated} exists={translated_exists}")
                    if translated_exc:
                        extra.append(f"translate_exc={translated_exc}")

                if exists:
                    try:
                        st = os.stat(path)
                        extra.append(f"mode={oct(st.st_mode)} uid={st.st_uid} gid={st.st_gid}")
                    except Exception:
                        pass

                extra_info = ("; " + ", ".join(extra)) if extra else ""
                self.logger.info("Could not read addon settings file %s: %s%s", path, e, extra_info)
                self._profile_settings_missing_logged = True
        return 0.0

    def get_sendspin_state(self) -> dict | None:
        return self.control.get_state()

    def get_delay_ms_setting(self) -> float:
        addon = xbmcaddon.Addon()
        profile_path = addon.getAddonInfo("profile")
        if profile_path:
            try:
                real_profile = xbmcvfs.translatePath(profile_path)
            except Exception:
                # Log the exception to help diagnose translation failures
                try:
                    self.logger.info("xbmcvfs.translatePath(profile) failed; using raw profile: %s", profile_path)
                except Exception:
                    pass
                real_profile = profile_path

            settings_path = os.path.join(real_profile, "settings.xml")
            delay_ms = self._get_delay_ms_from_profile(settings_path)
            return delay_ms
        return self._get_delay_ms(addon)

    def set_sendspin_delay(self, delay_ms: float) -> bool:
        success = self.control.set_delay(delay_ms)
        if success:
            self._last_applied_delay_ms = delay_ms
        return success

    def get_sendspin_audio_status(self) -> dict | None:
        return self.control.audio_status()

    def is_sendspin_audio_released(self, state: dict | None = None) -> bool:
        if isinstance(state, dict):
            audio = state.get("audio")
            if isinstance(audio, dict):
                return bool(audio.get("released", False))

        status = self.get_sendspin_audio_status()
        if isinstance(status, dict):
            return bool(status.get("released", False))
        return False

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
        self.original_kodi_device = self.kodi.get_audio_output_device()
        self.logger.info(f"Captured original audio device: {self.original_kodi_device}")

        # Temporarily free Kodi's audio device so the Docker container can probe all devices (including busy ones)
        switched_temp = False
        if self.original_kodi_device and "alsa" in self.original_kodi_device.lower():
            self.logger.info("Temporarily freeing Kodi audio device to allow Docker backend device probing...")
            self._switch_to_alternate()
            current_device = self.kodi.get_audio_output_device()
            if current_device != self.original_kodi_device:
                switched_temp = True
                await asyncio.sleep(1.5)  # Wait for Kodi to release the ALSA device

        kodi_volume = self.kodi.get_volume_state()
        addon = xbmcaddon.Addon()
        delay_ms = self._get_delay_ms(addon)
        self.playback_engine.configure_volume_sync(kodi_volume["volume"], kodi_volume["muted"], delay_ms)
        self._last_applied_delay_ms = delay_ms

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

        try:
            if self.docker_start_enabled:
                self.playback_engine.start()
            else:
                self.logger.info("Docker backend startup disabled by addon setting.")
                if self._last_applied_delay_ms is not None:
                    self.control.set_delay(self._last_applied_delay_ms)

            if self.wait_for_control_api():
                self.logger.info("Docker backend started with release-audio-on-start; no manual release required.")
            else:
                self.logger.warning("Sendspin control API did not become available during setup.")
        finally:
            # Restore original device after Docker has started/probed and server had time to connect/handshake
            if switched_temp:
                self.logger.info(
                    "Waiting for server connection and format probing before restoring Kodi audio device..."
                )
                await asyncio.sleep(8.0)  # Wait 8 seconds for server connection and format probing to complete cleanly
                self.logger.info("Restoring original Kodi audio device after Docker startup...")
                self.restore_kodi_audio_device()

    def _switch_to_alternate(self):
        # Try common safe fallbacks
        candidates = ["ALSA:default", "ALSA:sysdefault", "PULSE:default"]
        for candidate in candidates:
            if self.original_kodi_device and candidate.lower() != self.original_kodi_device.lower():
                if self.kodi.set_audio_output_device(candidate):
                    self.logger.info(f"Switched Kodi audio to {candidate} to free hardware.")
                    break

    def wait_for_control_api(self, timeout_seconds: float = 20.0, interval_seconds: float = 0.5) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.control.audio_status() is not None or self.control.get_state() is not None:
                return True
            time.sleep(interval_seconds)
        return False

    def restore_kodi_audio_device(self) -> bool:
        if not self.original_kodi_device:
            return False
        current_device = self.kodi.get_audio_output_device()
        if current_device == self.original_kodi_device:
            return True
        self.logger.info(f"Restoring Kodi audio device to: {self.original_kodi_device}")
        return bool(self.kodi.set_audio_output_device(self.original_kodi_device))

    def acquire_sendspin_audio(self) -> bool:
        if self._audio_claimed and not self.is_sendspin_audio_released():
            return True

        if self.original_kodi_device and "alsa" in self.original_kodi_device.lower():
            self._switch_to_alternate()

        success = self.control.acquire_audio()
        if success:
            self._audio_claimed = True
            self.logger.info("Acquired Sendspin audio output.")
        return success

    def release_sendspin_audio(self) -> bool:
        success = self.control.release_audio()
        if success:
            self._audio_claimed = False
            self.logger.info("Released Sendspin audio output.")
        return success

    def release_sendspin_audio_to_kodi(self) -> bool:
        success = self.release_sendspin_audio()
        self.restore_kodi_audio_device()
        return success

    def get_kodi_volume_state(self):
        return self.kodi.get_volume_state()

    def apply_sendspin_volume_to_kodi(self, volume, muted=False):
        kodi_volume = self.playback_engine.sendspin_to_kodi_volume(volume)
        self.kodi.set_muted(bool(muted))
        self.kodi.set_volume(kodi_volume)
        return kodi_volume

    def apply_kodi_volume_to_sendspin(self, volume_state):
        sendspin_volume = self.playback_engine.kodi_to_sendspin_volume(volume_state["volume"])
        self.control.set_volume(sendspin_volume, volume_state["muted"])
        return sendspin_volume

    def suppress_kodi_player_events(self, seconds: float = 1.5) -> None:
        self._suppress_kodi_player_events_until = time.monotonic() + seconds

    def _should_forward_kodi_player_event(self) -> bool:
        return time.monotonic() >= self._suppress_kodi_player_events_until

    def handle_kodi_pause(self) -> None:
        if self._should_forward_kodi_player_event():
            self.logger.info("Forwarding Kodi pause to Sendspin.")
            self.control.pause()

    def handle_kodi_resume(self) -> None:
        if self._should_forward_kodi_player_event():
            self.logger.info("Forwarding Kodi resume to Sendspin.")
            self.control.play()

    def send_play(self) -> bool:
        return self.control.play()

    def send_pause(self) -> bool:
        return self.control.pause()

    def send_play_pause(self) -> bool:
        return self.control.toggle_play_pause()

    def send_next_track(self) -> bool:
        return self.control.next_track()

    def send_previous_track(self) -> bool:
        return self.control.previous_track()

    async def cleanup(self) -> None:
        self.release_sendspin_audio_to_kodi()
        if self.docker_start_enabled:
            self.playback_engine.stop()
        else:
            self.logger.info("Docker backend cleanup skipped because startup is disabled.")
        await self.kodi.cleanup()
