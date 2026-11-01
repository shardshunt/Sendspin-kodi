import asyncio
import logging
import os
import platform
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

import xbmcaddon
import xbmcgui
import xbmcvfs
from control import SendspinControlClient
from kodi import KodiManager


class SendspinServiceController:
    def __init__(self) -> None:
        self.logger = logging.getLogger("sendspin")
        addon = xbmcaddon.Addon()
        control_url = addon.getSetting("control_url") or self._control_url_from_port(addon)
        addon_path = addon.getAddonInfo("path")
        configured_backend_path = (addon.getSetting("backend_path") or "").strip()
        self.backend_path = configured_backend_path or self._default_backend_path(addon_path)
        profile_path = addon.getAddonInfo("profile")
        if profile_path:
            try:
                profile_path = xbmcvfs.translatePath(profile_path)
            except Exception:
                pass
        self.profile_path = profile_path or os.path.join(
            os.path.expanduser("~"), ".kodi", "userdata", "addon_data", "service.sendspin"
        )
        self.state_file = os.path.join(self.profile_path, "sendspin", "state.json")
        self.control_socket_path = os.path.join(self.profile_path, "sendspin", "control.sock")
        self.server_url = addon.getSetting("server_url").strip()
        self.player_name = addon.getSetting("player_name") or "Kodi"
        self.native_backend_available = os.path.isfile(self.backend_path) and os.access(self.backend_path, os.X_OK)
        self.backend_process = None
        self._backend_log_thread = None
        self._native_backend_retry_at = 0.0
        self.volume_scale = self._get_volume_scale(addon)
        self.audio_device_id = "0"
        self.audio_device_override = False
        self.control = SendspinControlClient(
            control_url,
            socket_path=self.control_socket_path if self.native_backend_available else None,
        )
        self.kodi = KodiManager()
        self.original_kodi_device = None
        self.original_streamsilence = None
        self.original_default_sink = None
        self.original_card_profiles = {}
        self._suppress_kodi_player_events_until = 0.0
        self._kodi_pause_pending_until = 0.0
        self._last_applied_delay_ms = None
        self._profile_settings_missing_logged = False
        self._audio_claimed = False
        self._last_kodi_audio_device = None

    @staticmethod
    def _architecture_name(machine=None):
        machine = (machine or platform.machine()).lower()
        architecture_aliases = {
            "amd64": "x86_64",
            "x86-64": "x86_64",
            "x86_64": "x86_64",
            "aarch64": "aarch64",
            "arm64": "aarch64",
            "armv7": "armv7",
            "armv7l": "armv7",
            "armhf": "armv7",
        }
        return architecture_aliases.get(machine, machine)

    @classmethod
    def _default_backend_path(cls, addon_path):
        backend_root = os.path.join(addon_path, "resources", "bin")
        architecture_path = os.path.join(backend_root, cls._architecture_name(), "sendspin-kodi-backend")
        legacy_path = os.path.join(backend_root, "sendspin-kodi-backend")
        if os.path.isfile(architecture_path):
            return architecture_path
        return legacy_path

    def _control_url_from_port(self, addon) -> str:
        port = addon.getSetting("proxy_port") or "59999"
        return f"http://127.0.0.1:{port}"

    def _forward_backend_logs(self) -> None:
        process = self.backend_process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            self.logger.info("Native backend: %s", line.rstrip())

    def _start_native_backend(self) -> bool:
        if not self.native_backend_available:
            self.logger.error("Native backend executable is missing or not executable: %s", self.backend_path)
            return False

        command = [
            self.backend_path,
            "--name",
            self.player_name,
            "--state-file",
            self.state_file,
            "--control-socket",
            self.control_socket_path,
        ]
        volume_state = self.kodi.get_volume_state()
        if not isinstance(volume_state, dict):
            volume_state = {"volume": 100, "muted": False}
        addon = xbmcaddon.Addon()
        volume = max(0, min(100, round(int(volume_state.get("volume", 100)) * self.volume_scale)))
        delay_ms = round(self._get_delay_ms(addon))
        command.extend(
            [
                "--volume",
                str(volume),
                "--muted",
                "true" if volume_state.get("muted", False) else "false",
                "--delay-ms",
                str(delay_ms),
            ]
        )
        if self.server_url:
            command.extend(["--server", self.server_url])

        backend_environment = os.environ.copy()
        backend_environment["SENDSPIN_AUDIO_DEVICE"] = self.audio_device_id

        try:
            self.backend_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=backend_environment,
            )
        except OSError as error:
            self.logger.error("Could not launch native backend %s: %s", self.backend_path, error)
            self.backend_process = None
            self._native_backend_retry_at = time.monotonic() + 5.0
            return False

        self._backend_log_thread = threading.Thread(target=self._forward_backend_logs, daemon=True)
        self._backend_log_thread.start()
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if self.backend_process.poll() is not None:
                self.logger.error(
                    "Native backend exited during startup with status %s", self.backend_process.returncode
                )
                self._native_backend_retry_at = time.monotonic() + 5.0
                return False
            if self.control.get_state() is not None:
                volume_state = self.kodi.get_volume_state()
                if not isinstance(volume_state, dict):
                    volume_state = {"volume": 100, "muted": False}
                self.control.set_volume(
                    max(0, min(100, round(int(volume_state.get("volume", 100)) * self.volume_scale))),
                    bool(volume_state.get("muted", False)),
                )
                delay_ms = self._get_delay_ms(xbmcaddon.Addon())
                if self.control.set_delay(delay_ms):
                    self._last_applied_delay_ms = delay_ms
                self._native_backend_retry_at = 0.0
                self.logger.info("Native backend control socket is ready.")
                return True
            time.sleep(0.1)

        self.logger.error("Native backend did not become ready within 15 seconds.")
        self._stop_native_backend()
        self._native_backend_retry_at = time.monotonic() + 5.0
        return False

    def _stop_native_backend(self) -> None:
        process = self.backend_process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        self.backend_process = None

    def restart_native_backend(self) -> bool:
        if not self.native_backend_available:
            return False
        if time.monotonic() < self._native_backend_retry_at:
            return False
        self._stop_native_backend()
        return self._start_native_backend()

    def backend_needs_recovery(self) -> bool:
        if not self.native_backend_available or time.monotonic() < self._native_backend_retry_at:
            return False
        if self.backend_process is None or self.backend_process.poll() is not None:
            return True
        return self.control.get_state() is None

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

    def get_user_audio_device_change(self) -> str | None:
        """Return a user-selected Kodi audio device change, if any."""
        current_device = self.kodi.get_audio_output_device()
        if current_device == self._last_kodi_audio_device:
            return None
        self._last_kodi_audio_device = current_device
        return current_device

    def _set_kodi_audio_device(self, device_name: str) -> bool:
        success = self.kodi.set_audio_output_device(device_name)
        if success:
            self._last_kodi_audio_device = device_name
        return success

    def update_sendspin_audio_device(self, device_string: str | None) -> str:
        """Update the backend target after Kodi's audio output setting changes."""
        self.original_kodi_device = device_string
        addon = xbmcaddon.Addon()
        override = addon.getSetting("audio_device_override")
        self.audio_device_override = bool(override)
        if override:
            audio_device_id = override
        elif device_string:
            audio_device_id = self._get_audio_device_id(device_string)
        else:
            audio_device_id = addon.getSetting("fallback_audio_device") or "0"

        self.audio_device_id = audio_device_id
        self._last_kodi_audio_device = device_string
        self.logger.info("Updated Sendspin audio device: %s", audio_device_id)
        return audio_device_id

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

        if not device_string:
            return fallback

        # Special fallback case for general default/sysdefault/pipewire/pulse devices
        clean_dev = device_string.strip().lower()
        is_default = any(x in clean_dev for x in ["default", "sysdefault", "pipewire", "pulse"]) or (not clean_dev)

        if not is_default and "alsa:" not in clean_dev:
            return fallback

        # 1. Parse Kodi string (e.g., CARD=HDMI,DEV=4)
        card_match = re.search(r"CARD=([^,|]+)", device_string)
        dev_match = re.search(r"DEV=(\d+)", device_string)

        if not is_default and (not card_match or not dev_match):
            return fallback

        target_card_name = card_match.group(1).lower() if card_match else None
        target_dev_num = int(dev_match.group(1)) if dev_match else None

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

            # If Kodi is using a generic/software device, use the configured
            # fallback. Numeric fallbacks retain their legacy aplay ordering
            # but are converted to a stable raw ALSA identity immediately.
            if is_default:
                try:
                    fallback_index = int(fallback)
                except (TypeError, ValueError):
                    fallback_index = -1

                if 0 <= fallback_index < len(global_device_list):
                    fallback_device = global_device_list[fallback_index]
                    self.logger.info(
                        "Kodi configured with default/sysdefault audio. Mapping fallback %s to stable hw:%s,%s",
                        fallback,
                        fallback_device["card"],
                        fallback_device["device"],
                    )
                    return f"hw:{fallback_device['card']},{fallback_device['device']}"
                return fallback

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

            # Kodi's HDMI DEV number is often a port number, not ALSA's PCM
            # index. Prefer its HDMI label before accepting a numeric match.
            if "hdmi" in clean_dev:
                for d in card_devices:
                    if re.search(rf"\bHDMI\s+{target_dev_num}\b", d["label"], re.IGNORECASE):
                        self.logger.info(
                            f"Matched Kodi DEV={target_dev_num} to ALSA Device {d['device']} via label: {d['label']}"
                        )
                        final_device_idx = d["device"]
                        break

            if final_device_idx is None:
                for d in card_devices:
                    if d["device"] == target_dev_num:
                        final_device_idx = d["device"]
                        break

            if final_device_idx is None:
                device_number_pattern = rf"(?<!\d){target_dev_num}(?!\d)"
                for d in card_devices:
                    if re.search(device_number_pattern, d["label"]):
                        self.logger.info(
                            f"Matched Kodi DEV={target_dev_num} to ALSA Device {d['device']} via label: {d['label']}"
                        )
                        final_device_idx = d["device"]
                        break

            if final_device_idx is None:
                self.logger.error(f"Device {target_dev_num} not found by index or label on Card {matched_card_idx}.")
                return fallback

            # 5. Pass the hardware identity to Sendspin instead of a global
            # ordinal. The set/order of PortAudio devices can change when an
            # HDMI endpoint is re-enumerated (especially after a backend
            # restart), while hw:<card>,<device> remains tied to the target
            # ALSA PCM.
            return f"hw:{matched_card_idx},{final_device_idx}"

        except Exception as e:
            self.logger.error(f"Robust mapping failed: {e}")
            return fallback

    def is_kodi_holding_pcm(self) -> bool:
        """Checks if kodi or kodi.bin process is currently holding any /dev/snd/pcm* device open."""
        import glob
        import os

        pcm_devices = glob.glob("/dev/snd/pcmC*D*p")
        if not pcm_devices:
            return False

        try:
            for pid_dir in os.listdir("/proc"):
                if not pid_dir.isdigit():
                    continue
                try:
                    comm_path = os.path.join("/proc", pid_dir, "comm")
                    if not os.path.exists(comm_path):
                        continue
                    with open(comm_path) as f:
                        comm = f.read().strip().lower()

                    if "kodi" not in comm:
                        continue

                    fd_dir = os.path.join("/proc", pid_dir, "fd")
                    if not os.path.exists(fd_dir):
                        continue
                    for fd_name in os.listdir(fd_dir):
                        try:
                            link = os.readlink(os.path.join(fd_dir, fd_name))
                            if any(dev in link for dev in pcm_devices):
                                return True
                        except (OSError, ValueError):
                            continue
                except (OSError, PermissionError):
                    continue
        except Exception as e:
            self.logger.warning(f"Error checking if Kodi is holding PCM device: {e}")
        return False

    async def setup(self) -> None:
        self.original_kodi_device = self.kodi.get_audio_output_device()
        self._last_kodi_audio_device = self.original_kodi_device
        self.logger.info("Captured original Kodi audio device: %s", self.original_kodi_device)
        if self.original_kodi_device and any(
            name in self.original_kodi_device.lower() for name in ("pipewire", "pulse")
        ):
            self.logger.warning("Captured Kodi audio device is a software fallback: %s", self.original_kodi_device)
        self.update_sendspin_audio_device(self.original_kodi_device)

        if self.native_backend_available:
            self.logger.info("Leaving Kodi audio routing unchanged until Sendspin playback starts.")
            ready = await asyncio.get_running_loop().run_in_executor(None, self._start_native_backend)
            if not ready:
                self.logger.error("Native backend failed to start; Kodi audio routing remains unchanged.")
        else:
            self.logger.error(
                "Native backend executable is not installed at %s; playback is unavailable.", self.backend_path
            )

    def _switch_to_alternate(self):
        if not self.original_kodi_device:
            return False

        options = self.kodi.get_audio_output_device_options()
        options = [
            option
            for option in options
            if isinstance(option, dict)
            and isinstance(option.get("value"), str)
            and option["value"].strip()
            and option["value"].strip().lower() not in {"error", "none"}
        ]
        if not options:
            if self.original_kodi_device.strip().lower() in {"default", "sysdefault"}:
                self.logger.warning(
                    "Kodi did not expose a valid alternate audio device for generic %s; leaving the setting unchanged.",
                    self.original_kodi_device,
                )
                return False
            self.logger.warning("Could not retrieve audio output device options from Kodi; using static fallbacks.")
            # Fall back to static candidates as a safety measure
            candidates = ["ALSA:pipewire", "PULSE:default", "ALSA:sysdefault"]
        else:
            # Filter out the original device
            available_alts = [
                opt for opt in options if opt.get("value", "").lower() != self.original_kodi_device.lower()
            ]

            # Categorize candidates by priority
            hdmi_candidates = []
            software_candidates = []
            other_candidates = []

            for opt in available_alts:
                val = opt.get("value", "")
                lbl = opt.get("label", "").lower()
                val_lower = val.lower()

                if "hdmi" in val_lower or "hdmi" in lbl:
                    hdmi_candidates.append(val)
                elif any(x in val_lower or x in lbl for x in ["pipewire", "pulse"]):
                    software_candidates.append(val)
                elif not any(x in val_lower or x in lbl for x in ["analog", "default"]):
                    other_candidates.append(val)
                else:
                    # Lowest priority: fallback ALSA devices like ALSA:default or Analog
                    other_candidates.append(val)

            candidates = software_candidates + hdmi_candidates + other_candidates

        for candidate in candidates:
            if self._set_kodi_audio_device(candidate):
                self.logger.info(
                    f"Switched Kodi audio from {self.original_kodi_device} to {candidate} to free hardware."
                )
                return True

        self.logger.error("Failed to switch Kodi to any alternate audio device.")
        return False

    def setup_pipewire_null_sink(self) -> None:
        """Sets up a virtual null-sink in PipeWire/PulseAudio if pactl is available."""
        import shutil

        if not shutil.which("pactl"):
            return

        try:
            res = subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True, text=True, timeout=5)
            if "dummy_sink" not in res.stdout:
                self.logger.info("Loading virtual null-sink in PipeWire/PulseAudio...")
                subprocess.run(["pactl", "load-module", "module-null-sink", "sink_name=dummy_sink"], timeout=5)
        except Exception as e:
            self.logger.warning(f"Failed to setup PipeWire null-sink: {e}")

    def set_default_sink_to_null(self) -> None:
        """Sets the default PipeWire/PulseAudio sink to the dummy virtual null-sink if pactl is available."""
        import shutil

        if not shutil.which("pactl"):
            return

        try:
            sinks = subprocess.run(
                ["pactl", "list", "short", "sinks"], capture_output=True, text=True, timeout=5
            ).stdout
            if not any(line.split("\t")[1:2] == ["dummy_sink"] for line in sinks.splitlines()):
                self.logger.warning("PipeWire dummy sink is unavailable; leaving the default sink unchanged.")
                return
            res = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True, timeout=5)
            curr_sink = res.stdout.strip()
            if curr_sink and curr_sink != "dummy_sink":
                self.original_default_sink = curr_sink
                self.logger.info(f"Captured original default sink: {self.original_default_sink}")

            self.logger.info("Setting PipeWire/PulseAudio default sink to 'dummy_sink'...")
            subprocess.run(["pactl", "set-default-sink", "dummy_sink"], timeout=5)
        except Exception as e:
            self.logger.warning(f"Failed to set default sink to null: {e}")

    def restore_default_sink(self) -> None:
        """Restores the default PipeWire/PulseAudio sink back to the original default sink if pactl is available."""
        import shutil

        if not shutil.which("pactl"):
            return

        target_sink = self.original_default_sink
        if not target_sink:
            try:
                res = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True, timeout=5)
                if res.stdout.strip() == "dummy_sink":
                    sinks_res = subprocess.run(
                        ["pactl", "list", "short", "sinks"], capture_output=True, text=True, timeout=5
                    )
                    for line in sinks_res.stdout.splitlines():
                        parts = line.split()
                        if len(parts) >= 2:
                            sink_name = parts[1]
                            if sink_name != "dummy_sink":
                                target_sink = sink_name
                                self.logger.info(
                                    f"Self-healing: Found fallback physical sink to restore: {target_sink}"
                                )
                                break
            except Exception as e:
                self.logger.warning(f"Self-healing default sink detection failed: {e}")

        if not target_sink:
            return

        try:
            self.logger.info(f"Restoring default sink to: {target_sink}")
            subprocess.run(["pactl", "set-default-sink", target_sink], timeout=5)
            self.original_default_sink = None
        except Exception as e:
            self.logger.warning(f"Failed to restore default sink: {e}")

    def route_kodi_to_null_sink(self) -> None:
        """Routes Kodi's current audio stream input to the dummy null-sink if pactl is available."""
        import shutil

        if not shutil.which("pactl"):
            return

        try:
            res = subprocess.run(["pactl", "list", "sink-inputs"], capture_output=True, text=True, timeout=5)
            inputs = res.stdout.split("\n\n")
            kodi_input_id = None
            for inp in inputs:
                if "kodi" in inp.lower():
                    match = re.search(r"Sink Input #(\d+)", inp)
                    if match:
                        kodi_input_id = match.group(1)
                        break

            if kodi_input_id:
                self.logger.info(f"Routing Kodi audio stream (ID {kodi_input_id}) to virtual null-sink...")
                subprocess.run(["pactl", "move-sink-input", kodi_input_id, "dummy_sink"], timeout=5)
        except Exception as e:
            self.logger.warning(f"Failed to route Kodi stream to null sink: {e}")

    def restore_kodi_stream_routing(self) -> None:
        """Routes Kodi's current audio stream input back to the default sink if pactl is available."""
        import shutil

        if not shutil.which("pactl"):
            return

        try:
            res = subprocess.run(["pactl", "list", "sink-inputs"], capture_output=True, text=True, timeout=5)
            inputs = res.stdout.split("\n\n")
            kodi_input_id = None
            for inp in inputs:
                if "kodi" in inp.lower():
                    match = re.search(r"Sink Input #(\d+)", inp)
                    if match:
                        kodi_input_id = match.group(1)
                        break

            if kodi_input_id:
                self.logger.info(f"Restoring Kodi audio stream (ID {kodi_input_id}) back to default sink...")
                subprocess.run(["pactl", "move-sink-input", kodi_input_id, "@DEFAULT_SINK@"], timeout=5)
        except Exception as e:
            self.logger.warning(f"Failed to restore Kodi stream routing: {e}")

    def suspend_physical_sinks(self, suspend: bool = True) -> None:
        """Suspends or resumes all physical audio sinks on the host to immediately release/restore hardware access."""
        import shutil

        if not shutil.which("pactl"):
            return

        state_val = "1" if suspend else "0"
        try:
            res = subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True, text=True, timeout=5)
            lines = res.stdout.strip().split("\n")
            for line in lines:
                parts = line.split("\t")
                if len(parts) >= 2:
                    sink_name = parts[1]
                    if sink_name != "dummy_sink":
                        self.logger.info(f"Setting suspend state of sink '{sink_name}' to {state_val}...")
                        subprocess.run(["pactl", "suspend-sink", sink_name, state_val], timeout=5)
        except Exception as e:
            self.logger.warning(f"Failed to manage physical sink suspend state: {e}")

    def _physical_card_profiles(self) -> dict[str, str]:
        """Return active profiles for cards that currently expose physical sinks."""
        import shutil

        if not shutil.which("pactl"):
            return {}

        try:
            sinks = subprocess.run(
                ["pactl", "list", "short", "sinks"], capture_output=True, text=True, timeout=5
            ).stdout.splitlines()
            cards = subprocess.run(
                ["pactl", "list", "short", "cards"], capture_output=True, text=True, timeout=5
            ).stdout.splitlines()
            card_details = subprocess.run(["pactl", "list", "cards"], capture_output=True, text=True, timeout=5).stdout
            card_names = [line.split("\t")[1] for line in cards if len(line.split("\t")) >= 2]
            profiles = {}
            for line in sinks:
                parts = line.split("\t")
                if len(parts) < 2 or parts[1] == "dummy_sink":
                    continue
                sink_name = parts[1]
                sink_prefix = sink_name.removeprefix("alsa_output.")
                for card_name in card_names:
                    card_prefix = card_name.removeprefix("alsa_card.")
                    if sink_prefix.startswith(card_prefix):
                        profile = ""
                        for card_block in re.split(r"(?m)^Card #", card_details):
                            if f"Name: {card_name}" not in card_block:
                                continue
                            profile_match = re.search(r"(?m)^\s*Active Profile:\s*(\S+)", card_block)
                            if profile_match:
                                profile = profile_match.group(1)
                            break
                        if profile:
                            profiles[card_name] = profile
                        break
            return profiles
        except Exception as error:
            self.logger.warning(f"Could not capture PipeWire card profiles: {error}")
            return {}

    def disable_physical_card_profiles(self) -> None:
        """Disable PipeWire card profiles so native ALSA can claim the hardware."""
        import shutil

        if not shutil.which("pactl") or self.original_card_profiles:
            return

        self.original_card_profiles = self._physical_card_profiles()
        for card_name in self.original_card_profiles:
            try:
                self.logger.info(f"Disabling PipeWire card profile '{card_name}' for native playback...")
                subprocess.run(["pactl", "set-card-profile", card_name, "off"], timeout=5, check=False)
            except Exception as error:
                self.logger.warning(f"Could not disable PipeWire card profile {card_name}: {error}")

    def restore_physical_card_profiles(self) -> None:
        """Restore PipeWire card profiles captured before native playback."""
        import shutil

        if not shutil.which("pactl"):
            return

        profiles = self.original_card_profiles
        self.original_card_profiles = {}
        for card_name, profile in profiles.items():
            try:
                self.logger.info(f"Restoring PipeWire card profile '{card_name}' to '{profile}'...")
                subprocess.run(["pactl", "set-card-profile", card_name, profile], timeout=5, check=False)
            except Exception as error:
                self.logger.warning(f"Could not restore PipeWire card profile {card_name}: {error}")

    def restore_kodi_audio_device(self) -> bool:
        self.restore_physical_card_profiles()
        self.restore_default_sink()
        self.suspend_physical_sinks(False)
        if self.original_streamsilence is not None:
            self.logger.info(f"Restoring keep-alive streamsilence setting to: {self.original_streamsilence}")
            self.kodi.set_setting_value("audiooutput.streamsilence", self.original_streamsilence)
            self.original_streamsilence = None

        if not self.original_kodi_device:
            return False
        restore_device = self.original_kodi_device
        if self.audio_device_override or any(name in restore_device.lower() for name in ("pulse", "pipewire")):
            target_device = self._kodi_device_for_alsa_target(self.audio_device_id)
            if target_device:
                restore_device = target_device

        current_device = self.kodi.get_audio_output_device()
        if current_device == restore_device:
            return True
        self.logger.info("Restoring Kodi audio device to: %s", restore_device)
        return bool(self._set_kodi_audio_device(restore_device))

    def _kodi_device_for_alsa_target(self, target_device: str) -> str | None:
        if not target_device.startswith("hw:"):
            return None

        for option in self.kodi.get_audio_output_device_options():
            value = option.get("value") if isinstance(option, dict) else None
            if not isinstance(value, str) or "alsa:" not in value.lower():
                continue
            if not re.search(r"CARD=[^,|]+,DEV=\d+", value, re.IGNORECASE):
                continue
            if self._get_audio_device_id(value) == target_device:
                return value
        return None

    def prepare_kodi_audio_for_sendspin(self) -> None:
        device_text = (self.original_kodi_device or "").lower()
        hardware_audio = (
            "alsa" in device_text
            or "default" in device_text
            or "sysdefault" in device_text
            or self.audio_device_id.startswith("hw:")
        )
        if hardware_audio:
            if self.original_streamsilence is None:
                self.original_streamsilence = self.kodi.get_setting_value("audiooutput.streamsilence")
                self.kodi.set_setting_value("audiooutput.streamsilence", 0)
            self._switch_to_alternate()
            self.setup_pipewire_null_sink()
            self.set_default_sink_to_null()
            self.suspend_physical_sinks(True)
            self.disable_physical_card_profiles()

    def acquire_sendspin_audio(self) -> bool:
        if self._audio_claimed and not self.is_sendspin_audio_released():
            return True

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
        self.restore_kodi_stream_routing()
        success = self.release_sendspin_audio()
        self.restore_kodi_audio_device()
        return success

    def get_kodi_volume_state(self):
        return self.kodi.get_volume_state()

    def apply_sendspin_volume_to_kodi(self, volume, muted=False):
        kodi_volume = max(0, min(100, round(int(volume) / self.volume_scale)))
        self.kodi.set_muted(bool(muted))
        self.kodi.set_volume(kodi_volume)
        return kodi_volume

    def apply_kodi_volume_to_sendspin(self, volume_state):
        sendspin_volume = max(0, min(100, round(int(volume_state["volume"]) * self.volume_scale)))
        self.control.set_volume(sendspin_volume, volume_state["muted"])
        return sendspin_volume

    def suppress_kodi_player_events(self, seconds: float = 1.5) -> None:
        self._suppress_kodi_player_events_until = time.monotonic() + seconds

    def _should_forward_kodi_player_event(self) -> bool:
        return time.monotonic() >= self._suppress_kodi_player_events_until

    def handle_kodi_pause(self) -> bool:
        if not self._should_forward_kodi_player_event():
            return False
        if self.kodi_pause_is_pending():
            return True

        self._kodi_pause_pending_until = time.monotonic() + 5.0
        if self.control.pause():
            self.logger.info("Forwarded Kodi pause to Sendspin.")
            return True

        self._kodi_pause_pending_until = 0.0
        self.logger.warning("Sendspin did not accept the Kodi pause command.")
        return False

    def kodi_pause_is_pending(self) -> bool:
        return time.monotonic() < self._kodi_pause_pending_until

    def handle_kodi_resume(self) -> bool:
        self._kodi_pause_pending_until = 0.0
        if self._should_forward_kodi_player_event():
            self.logger.info("Forwarding Kodi resume to Sendspin.")
            return self.control.play()
        return False

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

    def show_pairing_token(self) -> bool:
        token = self.control.get_pairing_token()
        if not token:
            xbmcgui.Dialog().ok("Sendspin", "The native backend pairing token is not available.")
            return False
        xbmcgui.Dialog().textviewer("Sendspin pairing token", token)
        return True

    async def cleanup(self) -> None:
        if self._audio_claimed:
            self.release_sendspin_audio_to_kodi()
        else:
            self.restore_kodi_audio_device()
        await asyncio.get_running_loop().run_in_executor(None, self._stop_native_backend)
        await self.kodi.cleanup()
