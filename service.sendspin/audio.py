import asyncio
import logging
import subprocess

import numpy as np


class AudioRouter:
    """
    Manages PulseAudio virtual sinks and routing.
    """

    def __init__(self):
        self.sink_name = "Sendspin_Sink"
        self.loopback_id = None
        self.null_sink_id = None
        self.logger = logging.getLogger("sendspin")

    def _run_pactl(self, args):
        try:
            result = subprocess.run(["pactl"] + args, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as e:
            self.logger.info(f"pactl error: {e}")
        return None

    def setup_routing(self):
        if not self.null_sink_id:
            cmd = [
                "load-module",
                "module-null-sink",
                f"sink_name={self.sink_name}",
                "sink_properties=device.description=Sendspin_Virtual_Cable",
            ]
            self.null_sink_id = self._run_pactl(cmd)

        hw_sink = self.get_hardware_sink()
        if hw_sink and not self.loopback_id:
            loop_cmd = [
                "load-module",
                "module-loopback",
                f"source={self.sink_name}.monitor",
                f"sink={hw_sink}",
                "latency_msec=5",
            ]
            self.loopback_id = self._run_pactl(loop_cmd)
            self.logger.info(f"Routing {self.sink_name} -> {hw_sink}")
        return self.sink_name

    def get_hardware_sink(self):
        out = self._run_pactl(["list", "sinks", "short"])
        if out:
            for line in out.split("\n"):
                if "alsa_output" in line or "intel" in line:
                    parts = line.split("\t")
                    if len(parts) > 1:
                        return parts[1]
        return "@DEFAULT_SINK@"

    def cleanup(self):
        if self.loopback_id:
            self._run_pactl(["unload-module", self.loopback_id])
            self.loopback_id = None
        if self.null_sink_id:
            self._run_pactl(["unload-module", self.null_sink_id])
            self.null_sink_id = None


class SyncPlaybackEngine:
    """
    Handles time-synchronized PCM playback with non-blocking I/O.
    Prioritizes real-time playback by dropping late chunks.
    """

    def __init__(self):
        self.process = None
        self.logger = logging.getLogger("sendspin")
        self._queue = asyncio.PriorityQueue()
        self._worker_task = None
        self._running = False

        # Timing constants
        self.target_latency = 0.005  # Target latency of 5ms for stability
        self.drop_threshold = 0.02  # Drop chunks more than 20ms late

        # Software Volume State
        self._volume = 100
        self._muted = False
        self.get_play_time = None
        self.is_synchronized = lambda: False

    def set_volume(self, volume_int):
        self._volume = max(0, min(100, int(volume_int)))

    def set_mute(self, is_muted):
        self._muted = bool(is_muted)

    def _apply_software_volume(self, data: bytes) -> bytes:
        """
        Applies volume using the power curve:
        $$amplitude = (\\frac{volume}{100.0})^{1.5}$$
        """
        if self._muted or self._volume == 0:
            return b"\x00" * len(data)
        if self._volume == 100:
            return data

        samples = np.frombuffer(data, dtype=np.int16).copy()
        amplitude = (self._volume / 100.0) ** 1.5
        samples = (samples * amplitude).astype(np.int16)
        return samples.tobytes()

    def set_time_provider(self, time_provider_func, sync_check_func):
        self.get_play_time = time_provider_func
        self.is_synchronized = sync_check_func

    def start(self, rate, channels, bit_depth, target_sink):
        self._running = True
        # Clean up any existing tasks
        if self._worker_task:
            self._worker_task.cancel()

        # Use create_task to start the async process helper
        asyncio.create_task(self._async_init_process(rate, channels, bit_depth, target_sink))

    async def _async_init_process(self, rate, channels, bit_depth, target_sink):
        fmt = f"s{bit_depth}le"
        cmd = [
            "pacat",
            "--playback",
            "--device",
            target_sink,
            "--format",
            fmt,
            "--rate",
            str(rate),
            "--channels",
            str(channels),
            "--latency-msec=5",
        ]
        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            self._worker_task = asyncio.create_task(self._scheduler_loop())
            self.logger.info("Async playback engine started.")
        except Exception as e:
            self.logger.error(f"Failed to launch pacat: {e}")

    def play_chunk(self, server_timestamp_us, data):
        if not self.is_synchronized():
            return

        local_target_us = self.get_play_time(server_timestamp_us)
        scheduled_time = (local_target_us / 1_000_000.0) + self.target_latency

        # Don't even queue if it's already too late
        if (asyncio.get_event_loop().time() - scheduled_time) > self.drop_threshold:
            return

        try:
            self._queue.put_nowait((scheduled_time, data))
        except asyncio.QueueFull:
            pass

    async def _scheduler_loop(self):
        while self._running:
            try:
                scheduled_time, data = await self._queue.get()
                now = asyncio.get_event_loop().time()

                # Logic: Drop chunk if it is significantly late
                if (now - scheduled_time) > self.drop_threshold:
                    self._queue.task_done()
                    continue

                # Wait until it's time to play
                wait_time = scheduled_time - now
                if wait_time > 0:
                    await asyncio.sleep(wait_time)

                if self.process and self.process.stdin:
                    processed_data = self._apply_software_volume(data)
                    self.process.stdin.write(processed_data)
                    await self.process.stdin.drain()

                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Scheduler error: {e}")

    def stop(self):
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
        if self.process:
            self.process.terminate()
            self.process = None
