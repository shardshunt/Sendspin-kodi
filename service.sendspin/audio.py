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
        self.preferred_device_name: str | None = None
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

    def _list_audio_devices(self) -> list[tuple[int, str]]:
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
            self.logger.warning("Could not query container audio devices.")
            return []

        devices: list[tuple[int, str]] = []
        pattern = re.compile(r"^\s*\[(\d+)\]\s+(.+?)\s*$")
        for line in result.stdout.splitlines():
            match = pattern.match(line)
            if match:
                devices.append((int(match.group(1)), match.group(2).strip()))
        return devices

    def _find_device_by_name(self, device_name: str, devices: list[tuple[int, str]]) -> str | None:
        if not device_name:
            return None

        mapped = self._find_device_by_kodi_alsa_name(device_name, devices)
        if mapped:
            return mapped

        normalized = device_name.lower()
        exact = next((str(index) for index, name in devices if name.lower() == normalized), None)
        if exact:
            return exact

        contains = next((str(index) for index, name in devices if normalized in name.lower()), None)
        if contains:
            return contains

        starts = next((str(index) for index, name in devices if name.lower().startswith(normalized)), None)
        if starts:
            return starts

        return None

    def _find_device_by_kodi_alsa_name(self, device_name: str, devices: list[tuple[int, str]]) -> str | None:
        name_lower = device_name.lower()
        match = re.search(r"card=([^,|]+),dev=(\d+)", name_lower)
        if not match:
            return None

        card_alias = match.group(1).strip()
        dev_index = int(match.group(2).strip())
        card_number = self._resolve_alsa_card_number(card_alias)
        if card_number is None:
            return None

        hw_device = self._resolve_alsa_hw_device(card_number, dev_index)
        if hw_device is None:
            return None

        target_pattern = f"hw:{card_number},{hw_device}"
        return next((str(index) for index, name in devices if target_pattern in name.lower()), None)

    def _resolve_alsa_card_number(self, card_alias: str) -> int | None:
        result = self._run_docker(
            [
                "run",
                "--rm",
                "--device",
                "/dev/snd:/dev/snd",
                "--entrypoint",
                "sh",
                self.image_name,
                "-c",
                "cat /proc/asound/cards",
            ]
        )
        if not result or result.returncode != 0:
            return None

        for line in result.stdout.splitlines():
            match = re.match(r"^\s*(\d+)\s*\[(.+?)\]:", line)
            if not match:
                continue
            index = int(match.group(1))
            alias = match.group(2).strip().lower()
            if alias == card_alias.lower() or card_alias.lower() in alias:
                return index
        return None

    def _resolve_alsa_hw_device(self, card_number: int, dev_index: int) -> int | None:
        result = self._run_docker(
            [
                "run",
                "--rm",
                "--device",
                "/dev/snd:/dev/snd",
                "--entrypoint",
                "sh",
                self.image_name,
                "-c",
                "cat /proc/asound/devices",
            ]
        )
        if not result or result.returncode != 0:
            return None

        device_numbers: list[int] = []
        for line in result.stdout.splitlines():
            if "digital audio playback" not in line.lower():
                continue
            match = re.match(r"^\s*\d+:\s*\[\s*" + re.escape(str(card_number)) + r"-(\d+)\]", line)
            if not match:
                continue
            device_numbers.append(int(match.group(1)))

        if not device_numbers:
            return None
        device_numbers.sort()
        if len(device_numbers) <= dev_index:
            return None
        return device_numbers[dev_index]

    def _find_alternate_device(self, exclude_device_name: str, devices: list[tuple[int, str]]) -> str | None:
        exclude_norm = exclude_device_name.lower()
        for _index, name in devices:
            lower_name = name.lower()
            if lower_name == exclude_norm or exclude_norm in lower_name:
                continue
            if "dmix" in lower_name or "default" in lower_name:
                continue
            return name
        return next((name for _, name in devices if "default" in name.lower()), None)

    def _detect_audio_device(self) -> str:
        if self.audio_device:
            return self.audio_device

        devices = self._list_audio_devices()
        if not devices:
            self.logger.warning("No audio devices were detected from sendspin; defaulting to device 0.")
            return "0"

        if self.preferred_device_name:
            selected = self._find_device_by_name(self.preferred_device_name, devices)
            if selected:
                self.logger.info(f"Matched Kodi audio output to sendspin audio device: {selected}")
                return selected

        def find_first(match_fn):
            for index, name in devices:
                if match_fn(name.lower()):
                    return str(index)
            return None

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
            self.logger.info(f"Auto-selected sendpin HDMI audio device: {selected}")
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
