import json
import logging
import os
import shlex
import shutil
import subprocess
import threading
from urllib.parse import urlparse

import xbmcaddon

try:
    import xbmcgui
except Exception:
    xbmcgui = None


class DockerPlaybackEngine:
    def __init__(
        self,
        image_name="ghcr.io/shardshunt/sendspin-cli-for-sendspin-kodi",
        container_name="sendspin-player",
        config_dir="/storage/.config/sendspin",
        version_control_enabled=True,
        image_tag_override="",
        audio_device="0",
        volume_scale=10 / 30,
        control_url="http://127.0.0.1:59999",
    ):
        self.logger = logging.getLogger("sendspin")
        self.image_name = image_name
        self.container_name = container_name
        self.config_dir = config_dir
        self.audio_device = audio_device
        self.control_url = control_url
        self.log_process = None
        self.log_thread = None
        self.volume_scale = volume_scale
        self.version_control_enabled = version_control_enabled
        self.image_tag_override = image_tag_override
        self.versioned_image_name = self._get_versioned_image_name()

    def _get_versioned_image_name(self) -> str:
        """Build image name with tag for version control or custom override."""
        base_name = self.image_name

        # If a custom tag is provided, use it
        if self.image_tag_override:
            if ":" in base_name:
                # Remove existing tag if present
                base_name = base_name.split(":")[0]
            return f"{base_name}:{self.image_tag_override}"

        # If version control is enabled, use addon version as tag
        if self.version_control_enabled:
            try:
                addon = xbmcaddon.Addon()
                addon_version = addon.getAddonInfo("version")
                if ":" not in base_name:
                    return f"{base_name}:{addon_version}"
                return base_name
            except Exception as e:
                self.logger.warning("Could not read addon version; using base image name: %s", e)
                return base_name

        return base_name

    def kodi_to_sendspin_volume(self, volume) -> int:
        return max(0, min(100, round(int(volume) * self.volume_scale)))

    def sendspin_to_kodi_volume(self, volume) -> int:
        if self.volume_scale <= 0:
            self.logger.warning("Volume scale is non-positive; using fallback scale 0.3")
            self.volume_scale = 0.3
        return max(0, min(100, round(int(volume) / self.volume_scale)))

    def _write_json_file(self, path, data):
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as file:
            json.dump(data, file)
            file.write("\n")
        os.replace(tmp_path, path)

    def configure_volume_sync(self, volume, muted, delay_ms: float = 0.0):
        """Seed Sendspin daemon settings from Kodi before the container starts."""
        os.makedirs(self.config_dir, exist_ok=True)
        sendspin_volume = self.kodi_to_sendspin_volume(volume)

        try:
            delay = float(delay_ms)
        except (TypeError, ValueError):
            delay = 0.0

        delay = max(0.0, min(5000.0, delay))

        settings_path = os.path.join(self.config_dir, "settings-daemon.json")
        try:
            with open(settings_path, encoding="utf-8") as file:
                settings = json.load(file)
        except (FileNotFoundError, ValueError):
            settings = {}

        settings["player_volume"] = sendspin_volume
        settings["player_muted"] = bool(muted)
        settings["use_hardware_volume"] = False
        settings["delay_ms"] = delay
        settings.pop("hook_set_volume", None)
        self._write_json_file(settings_path, settings)

        self.logger.info(
            "Configured Sendspin daemon settings: kodi_volume=%s sendspin_volume=%s muted=%s delay_ms=%s",
            volume,
            settings["player_volume"],
            settings["player_muted"],
            settings["delay_ms"],
        )

    def _ensure_image_exists(self) -> bool:
        """Checks if the docker image exists; if not, pulls it from registry."""
        check_cmd = ["docker", "images", "-q", self.versioned_image_name]
        result = subprocess.run(check_cmd, capture_output=True, text=True)

        if result.returncode != 0:
            self.logger.error("Failed to query Docker images: %s", result.stderr.strip())
            return False

        if result.stdout.strip():
            return True

        self.logger.info("Image %s not found locally. Pulling from registry...", self.versioned_image_name)

        # Stream pull output so users can see progress in Kodi and logs
        try:
            proc = subprocess.Popen(
                [
                    "docker",
                    "pull",
                    self.versioned_image_name,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            dialog = None
            if xbmcgui is not None:
                try:
                    dialog = xbmcgui.DialogProgress()
                    dialog.create("Pulling Docker image", f"{self.versioned_image_name}")
                except Exception:
                    dialog = None

            percent = 0
            if proc.stdout is not None:
                for line in proc.stdout:
                    if line:
                        self.logger.info("DOCKER-PULL: %s", line.strip())
                        # Update a simple progress indicator in the dialog if present
                        if dialog is not None:
                            try:
                                percent = min(100, percent + 1)
                                dialog.update(percent, line.strip())
                            except Exception:
                                pass

            ret = proc.wait()

            if dialog is not None:
                try:
                    dialog.close()
                except Exception:
                    pass

            if ret == 0:
                self.logger.info("Successfully pulled %s", self.versioned_image_name)
                # Tag as the base name for container use
                subprocess.run(
                    ["docker", "tag", self.versioned_image_name, self.image_name],
                    capture_output=True,
                )
                return True
            else:
                self.logger.error("Failed to pull image %s (exit %s)", self.versioned_image_name, ret)
                return False
        except FileNotFoundError:
            self.logger.error("Docker binary not found when attempting to pull %s", self.versioned_image_name)
            return False
        except Exception as e:
            self.logger.error("Unexpected error pulling image %s: %s", self.versioned_image_name, e)
            return False

    def _stream_logs(self):
        """Background worker to forward Docker logs to Kodi for diagnostics."""
        # Using -f (follow) to keep the stream open
        cmd = ["docker", "logs", "-f", "--tail", "0", self.container_name]
        self.log_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

        if self.log_process.stdout is not None:
            for line in self.log_process.stdout:
                if line:
                    self.logger.info(f"DOCKER: {line.strip()}")

        if self.log_process.stdout:
            self.log_process.stdout.close()

    def _control_host_port(self) -> tuple[str, str]:
        parsed = urlparse(self.control_url)
        return parsed.hostname or "127.0.0.1", str(parsed.port or 59999)

    def start(self):
        if not shutil.which("docker"):
            self.logger.error("Docker not found in PATH.")
            return

        # Ensure image is ready before proceeding
        if not self._ensure_image_exists():
            return

        self.stop()
        self.logger.info(f"Starting Docker container: {self.container_name}")
        control_host, control_port = self._control_host_port()

        cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            self.container_name,
            "--network",
            "host",
            "--privileged",
            "--device",
            "/dev/snd:/dev/snd",
            "-v",
            f"{self.config_dir}:/root/.config/sendspin",
            self.versioned_image_name,
            "daemon",
            "--audio-device",
            self.audio_device,
            "--hardware-volume",
            "false",
            "--control-api",
            "true",
            "--control-host",
            control_host,
            "--control-port",
            control_port,
        ]

        executable_command = shlex.join(cmd)
        self.logger.info(f"Executing Docker command: {executable_command}")

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            self.logger.info(f"Docker container started. ID: {result.stdout.strip()}")
            self.log_thread = threading.Thread(target=self._stream_logs, daemon=True)
            self.log_thread.start()
        else:
            self.logger.error(f"Docker failed to start! Error: {result.stderr.strip()}")

    def stop(self):
        if self.log_process:
            try:
                self.log_process.terminate()
            except OSError:
                pass

        subprocess.run(["docker", "stop", self.container_name], capture_output=True)
        subprocess.run(["docker", "rm", self.container_name], capture_output=True)
        self.logger.info("Docker container stopped.")
