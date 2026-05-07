import ast
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import threading


class DockerPlaybackEngine:
    def __init__(
        self,
        image_name="sendspin-local",
        container_name="sendspin-player",
        config_dir="/storage/.config/sendspin",
        audio_device="0",
        volume_scale=10 / 30,
    ):
        self.logger = logging.getLogger("sendspin")
        self.image_name = image_name
        self.container_name = container_name
        self.config_dir = config_dir
        self.audio_device = audio_device
        self.log_process = None
        self.log_thread = None
        self.volume_state_path = os.path.join(self.config_dir, "kodi-volume.json")
        self.volume_scale = volume_scale
        self.logged_runtime_volume_limit = False
        # Path to the directory containing the Dockerfile
        self.addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.current_metadata = {}
        self.metadata_updated = False

    def kodi_to_sendspin_volume(self, volume):
        return max(0, min(100, round(int(volume) * self.volume_scale)))

    def sendspin_to_kodi_volume(self, volume):
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

    def configure_volume_sync(self, volume, muted):
        """Seed Sendspin daemon settings from Kodi before the container starts."""
        os.makedirs(self.config_dir, exist_ok=True)
        sendspin_volume = self.kodi_to_sendspin_volume(volume)

        settings_path = os.path.join(self.config_dir, "settings-daemon.json")
        try:
            with open(settings_path, encoding="utf-8") as file:
                settings = json.load(file)
        except (FileNotFoundError, ValueError):
            settings = {}

        settings["player_volume"] = sendspin_volume
        settings["player_muted"] = bool(muted)
        settings["use_hardware_volume"] = False
        settings.pop("hook_set_volume", None)
        self._write_json_file(settings_path, settings)

        effective_volume = 0 if settings["player_muted"] else settings["player_volume"]
        self._write_json_file(self.volume_state_path, {"volume": effective_volume})

        self.logger.info(
            "Configured Sendspin volume sync: kodi_volume=%s sendspin_volume=%s muted=%s",
            volume,
            settings["player_volume"],
            settings["player_muted"],
        )

    def write_kodi_volume_to_settings(self, volume, muted):
        """Persist Kodi volume for Sendspin's next config read.

        Sendspin daemon does not currently reload this file while running.
        """
        sendspin_volume = self.kodi_to_sendspin_volume(volume)
        settings_path = os.path.join(self.config_dir, "settings-daemon.json")
        try:
            with open(settings_path, encoding="utf-8") as file:
                settings = json.load(file)
        except (FileNotFoundError, ValueError):
            settings = {}

        settings["player_volume"] = sendspin_volume
        settings["player_muted"] = bool(muted)
        settings["use_hardware_volume"] = False
        settings.pop("hook_set_volume", None)
        self._write_json_file(settings_path, settings)
        if not self.logged_runtime_volume_limit:
            self.logged_runtime_volume_limit = True
            self.logger.warning(
                "Kodi volume was persisted for Sendspin's next start, but the running "
                "sendspin daemon does not reload settings-daemon.json at runtime."
            )
        return sendspin_volume

    def read_volume_state(self):
        try:
            with open(self.volume_state_path, encoding="utf-8") as file:
                data = json.load(file)
        except (FileNotFoundError, ValueError):
            return None

        try:
            return max(0, min(100, int(data["volume"])))
        except (KeyError, TypeError, ValueError):
            return None

    def _ensure_image_exists(self):
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
        """Background worker to capture docker logs and send them to Kodi."""
        # Using -f (follow) to keep the stream open
        cmd = ["docker", "logs", "-f", "--tail", "0", self.container_name]
        self.log_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

        if self.log_process.stdout is not None:
            for line in self.log_process.stdout:
                if line:
                    self._capture_volume_from_log(line)
                    self._parse_metadata(line)
                    self.logger.info(f"DOCKER: {line.strip()}")

        if self.log_process.stdout:
            self.log_process.stdout.close()

    def _capture_volume_from_log(self, line):
        match = re.search(r"Server set player volume: (\d+)%", line)
        if not match:
            return

        volume = max(0, min(100, int(match.group(1))))
        self._write_json_file(self.volume_state_path, {"volume": volume})

    def _parse_metadata(self, line):
        """Extracts and cleans metadata from Sendspin log payloads."""
        if "ServerStatePayload:" in line:
            try:
                payload_str = line.split("ServerStatePayload:", 1)[1]

                # Regex to convert <Enum.VALUE: 'data'> into 'data'
                # and <Enum.VALUE: False> into False
                cleaned_str = re.sub(r"<\w+\.[^:]+:\s+([^>]+)>", r"\1", payload_str)

                payload = ast.literal_eval(cleaned_str)
                metadata = payload.get("metadata", {})

                # Check if metadata contains actual content beyond just a timestamp
                if "title" in metadata and metadata != self.current_metadata:
                    self.current_metadata = metadata
                    self.metadata_updated = True
                    self.logger.info(f"Parsed metadata for: {metadata.get('title')}")
            except Exception as e:
                self.logger.error(f"Failed to parse metadata line: {e}")

    def start(self):
        if not shutil.which("docker"):
            self.logger.error("Docker not found in PATH.")
            return

        # Ensure image is ready before proceeding
        if not self._ensure_image_exists():
            return

        self.stop()
        self.logger.info(f"Starting Docker container: {self.container_name}")

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
            "--metadata",
            "true",
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
