import json
import logging
import os
import shlex
import shutil
import subprocess
import threading
from urllib.parse import urlparse

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
        image_version="",
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
        self.image_version = image_version
        self.versioned_image_name = self._get_versioned_image_name()

    def _get_versioned_image_name(self) -> str:
        """Build image name using only the configured Docker image version."""
        base_name = self.image_name
        image_version = (self.image_version or "").strip()

        if not image_version:
            return base_name

        slash_index = base_name.rfind("/")
        tag_index = base_name.rfind(":")
        if tag_index > slash_index:
            base_name = base_name[:tag_index]
        return f"{base_name}:{image_version}"

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
        try:
            os.makedirs(self.config_dir, exist_ok=True)
        except OSError as exc:
            self.logger.warning(
                "Failed to create config directory %s: %s; falling back to ~/.config/sendspin",
                self.config_dir,
                exc,
            )
            self.config_dir = os.path.expanduser("~/.config/sendspin")
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
                    dialog.create("Pulling Docker image (this may take a while)", f"{self.versioned_image_name}")
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

    def _container_exists(self) -> bool:
        result = subprocess.run(["docker", "container", "inspect", self.container_name], capture_output=True, text=True)
        return result.returncode == 0

    def _container_is_running(self) -> bool:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", self.container_name],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip().lower() == "true"

    def _container_image(self) -> str | None:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.Config.Image}}", self.container_name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def _start_log_stream(self) -> None:
        if self.log_thread is not None and self.log_thread.is_alive():
            return
        self.log_thread = threading.Thread(target=self._stream_logs, daemon=True)
        self.log_thread.start()

    def start(self):
        if not shutil.which("docker"):
            self.logger.error("Docker not found in PATH.")
            return

        # Ensure image is ready before proceeding
        if not self._ensure_image_exists():
            return

        if self._container_exists():
            existing_image = self._container_image()
            if existing_image == self.versioned_image_name:
                if self._container_is_running():
                    self.logger.info(
                        "Reusing running Docker container %s with image %s",
                        self.container_name,
                        self.versioned_image_name,
                    )
                    self._start_log_stream()
                    return

                self.logger.info("Starting existing Docker container: %s", self.container_name)
                result = subprocess.run(["docker", "start", self.container_name], capture_output=True, text=True)
                if result.returncode == 0:
                    self._start_log_stream()
                else:
                    self.logger.error("Docker failed to start existing container: %s", result.stderr.strip())
                return

            self.logger.info(
                "Recreating Docker container %s because image changed from %s to %s",
                self.container_name,
                existing_image,
                self.versioned_image_name,
            )
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
        ]

        cmd.extend(
            [
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
                "--release-audio-on-start",
            ]
        )

        executable_command = shlex.join(cmd)
        self.logger.info(f"Executing Docker command: {executable_command}")

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            self.logger.info(f"Docker container started. ID: {result.stdout.strip()}")
            self._start_log_stream()
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
