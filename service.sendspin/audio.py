import logging
import re
import shutil
import subprocess


class DockerPlaybackEngine:
    """
    Playback manager for a Docker-hosted Sendspin CLI player.
    """

    def __init__(
        self,
        image_name: str = "sendspin-local",
        container_name: str = "sendspin-player",
        audio_device: str | None = None,
        config_dir: str = "/storage/.config/sendspin",
    ) -> None:
        self.logger = logging.getLogger("sendspin")
        self.image_name = image_name
        self.container_name = container_name
        self.audio_device = audio_device
        self.config_dir = config_dir

    def _docker_available(self) -> bool:
        return shutil.which("docker") is not None

    def _run_docker(self, args: list[str]) -> subprocess.CompletedProcess | None:
        if not self._docker_available():
            self.logger.error("Docker CLI not found in PATH.")
            return None
        try:
            return subprocess.run(["docker"] + args, capture_output=True, text=True, timeout=30)
        except Exception as e:
            self.logger.error(f"Docker command failed: {e}")
            return None

    def _container_running(self) -> bool:
        result = self._run_docker(["ps", "-q", "-f", f"name={self.container_name}"])
        return bool(result and result.stdout.strip())

    def _detect_audio_device(self) -> str:
        if self.audio_device:
            return self.audio_device

        result = self._run_docker(
            [
                "run",
                "--rm",
                "--device",
                "/dev/snd:/dev/snd",
                self.image_name,
                "audio-devices",
                "list",
            ]
        )
        if not result or result.returncode != 0:
            self.logger.warning("Could not query container audio devices; defaulting to device 0.")
            return "0"

        devices: list[tuple[int, str]] = []
        pattern = re.compile(r"^\s*\[(\d+)\]\s+(.+?)\s*$")
        for line in result.stdout.splitlines():
            match = pattern.match(line)
            if match:
                devices.append((int(match.group(1)), match.group(2).strip()))

        if not devices:
            self.logger.warning("No audio devices were detected from sendspin; defaulting to device 0.")
            return "0"

        def find_first(match_fn):
            for index, name in devices:
                if match_fn(name.lower()):
                    return str(index)
            return None

        # Prefer a Panasonic HDMI device when available.
        selected = find_first(lambda n: "panasonic" in n and "hdmi" in n)
        if selected:
            self.logger.info(f"Auto-selected sendspin audio device by name: {selected}")
            return selected

        selected = find_first(lambda n: "panasonic" in n)
        if selected:
            self.logger.info(f"Auto-selected sendspin audio device by name: {selected}")
            return selected

        selected = find_first(lambda n: "hdmi" in n and "hw:1" in n)
        if selected:
            self.logger.info(f"Auto-selected sendspin HDMI audio device: {selected}")
            return selected

        selected = find_first(lambda n: n == "dmix")
        if selected:
            self.logger.info(f"Auto-selected sendspin shared ALSA device: {selected}")
            return selected

        selected = find_first(lambda n: n == "default")
        if selected:
            self.logger.info(f"Auto-selected sendspin default ALSA device: {selected}")
            return selected

        first_index = str(devices[0][0])
        self.logger.info(f"Falling back to first available sendspin audio device: {first_index}")
        return first_index

    def start(self) -> None:
        if not self._docker_available():
            return
        if self._container_running():
            self.logger.info(f"Docker playback container '{self.container_name}' already running.")
            return

        self.logger.info(f"Starting Docker playback container '{self.container_name}'.")
        self._run_docker(["stop", self.container_name])
        self._run_docker(["rm", "-f", self.container_name])

        device = self._detect_audio_device()
        cmd = [
            "run",
            "-d",
            "--name",
            self.container_name,
            "--restart",
            "unless-stopped",
            "--net",
            "host",
            "--privileged",
            "--device",
            "/dev/snd:/dev/snd",
            "-v",
            f"{self.config_dir}:/root/.config/sendspin",
            self.image_name,
            "daemon",
            "--audio-device",
            device,
        ]

        result = self._run_docker(cmd)
        if result and result.returncode == 0:
            self.logger.info(f"Docker playback container started: {result.stdout.strip()}")
        else:
            self.logger.error(
                f"Failed to start Docker playback container: {result.stderr.strip() if result else 'no result'}"
            )

    def stop(self) -> None:
        if not self._docker_available():
            return
        if not self._container_running():
            self.logger.info(f"Docker playback container '{self.container_name}' is not running.")
            return

        self.logger.info(f"Stopping Docker playback container '{self.container_name}'.")
        self._run_docker(["stop", self.container_name])
        self._run_docker(["rm", "-f", self.container_name])

    def set_volume(self, volume_int: int) -> None:
        self.logger.debug(
            "Docker backend ignores local volume changes; adjust volume inside the container or on the ALSA host device."
        )

    def set_mute(self, is_muted: bool) -> None:
        self.logger.debug(
            "Docker backend ignores local mute changes; adjust mute inside the container or on the ALSA host device."
        )
