import logging
import os
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
    ):
        self.logger = logging.getLogger("sendspin")
        self.image_name = image_name
        self.container_name = container_name
        self.config_dir = config_dir
        self.audio_device = audio_device
        self.log_process = None
        self.log_thread = None
        # Path to the directory containing the Dockerfile
        self.addon_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _ensure_image_exists(self):
        """Checks if the docker image exists; if not, attempts to build it."""
        check_cmd = ["docker", "images", "-q", self.image_name]
        result = subprocess.run(check_cmd, capture_output=True, text=True)

        if not result.stdout.strip():
            self.logger.info(f"Image {self.image_name} not found. Attempting to build...")
            dockerfile_path = os.path.join(self.addon_dir, "Dockerfile")

            if not os.path.exists(dockerfile_path):
                self.logger.error(f"Cannot build image: Dockerfile not found at {dockerfile_path}")
                return False

            build_cmd = ["docker", "build", "-t", self.image_name, self.addon_dir]
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
        cmd = ["docker", "logs", "-f", "--tail", "0", self.container_name]
        self.log_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        for line in iter(self.log_process.stdout.readline, ""):
            if line:
                self.logger.info(f"DOCKER: {line.strip()}")

        if self.log_process:
            self.log_process.stdout.close()

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
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            self.logger.info(f"Docker container started. ID: {result.stdout.strip()}")
            self.log_thread = threading.Thread(target=self._stream_logs, daemon=True)
            self.log_thread.start()
        else:
            self.logger.error(f"Docker failed to start! Error: {result.stderr.strip()}")

    def stop(self):
        if self.log_process:
            self.log_process.terminate()
            self.log_process = None

        subprocess.run(["docker", "stop", self.container_name], capture_output=True)
        subprocess.run(["docker", "rm", self.container_name], capture_output=True)
        self.logger.info("Docker container stopped.")
