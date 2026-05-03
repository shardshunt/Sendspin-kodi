import logging
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
        pulse_socket_dir: str = "/run/pulse",
        pulse_server: str = "unix:/run/pulse/native",
        config_dir: str = "/storage/.config/sendspin",
    ) -> None:
        self.logger = logging.getLogger("sendspin")
        self.image_name = image_name
        self.container_name = container_name
        self.pulse_socket_dir = pulse_socket_dir
        self.pulse_server = pulse_server
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

    def start(self) -> None:
        if not self._docker_available():
            return
        if self._container_running():
            self.logger.info(f"Docker playback container '{self.container_name}' already running.")
            return

        self.logger.info(f"Starting Docker playback container '{self.container_name}'.")
        self._run_docker(["stop", self.container_name])
        self._run_docker(["rm", "-f", self.container_name])

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
            "-v",
            f"{self.pulse_socket_dir}:{self.pulse_socket_dir}",
            "-v",
            f"{self.config_dir}:/root/.config/sendspin",
            "-e",
            f"PULSE_SERVER={self.pulse_server}",
            "-e",
            "PULSE_ALLOW_ROOT=1",
            self.image_name,
            "daemon",
            "--audio-device",
            "pulse",
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
            "Docker backend ignores local volume changes; adjust volume inside the container or at PulseAudio."
        )

    def set_mute(self, is_muted: bool) -> None:
        self.logger.debug(
            "Docker backend ignores local mute changes; adjust mute inside the container or at PulseAudio."
        )
