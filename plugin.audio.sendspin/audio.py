import json
import logging
import os
import shlex
import shutil
import subprocess
import threading
from urllib.parse import urlparse


class DockerPlaybackEngine:
    def __init__(
        self,
        image_name="sendspin-local",
        container_name="sendspin-player",
        config_dir="/storage/.config/sendspin",
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
        # Path to the directory containing the Dockerfile
        self.addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
        """Checks if the docker image exists; if not, attempts to build it."""
        check_cmd = ["docker", "images", "-q", self.image_name]
        result = subprocess.run(check_cmd, capture_output=True, text=True)

        if not result.stdout.strip():
            self.logger.info(f"Image {self.image_name} not found. Attempting to build...")
            dockerfile_path = os.path.join(self.addon_dir, "plugin.audio.sendspin", "Dockerfile")

            if not os.path.exists(dockerfile_path):
                self.logger.error(f"Cannot build image: Dockerfile not found at {dockerfile_path}")
                return False

            build_cmd = [
                "docker",
                "build",
                "--no-cache",
                "--pull",
                "-t",
                self.image_name,
                os.path.join(self.addon_dir, "plugin.audio.sendspin"),
            ]
            build_result = subprocess.run(build_cmd, capture_output=True, text=True)

            if build_result.returncode == 0:
                self.logger.info(f"Successfully built {self.image_name}")
                return True
            else:
                self.logger.error(f"Build failed: {build_result.stderr}")
                return False
        return True

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
            self.image_name,
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
