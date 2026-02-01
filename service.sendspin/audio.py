import asyncio
import logging
import subprocess

import numpy as np
from aiosendspin.client import SendspinClient


class AudioRouter:
    """
    Manages PulseAudio virtual sinks and routing.
    """

    def __init__(self):
        self.sink_name = "Sendspin_Sink"
        self.loopback_id = None
        self.null_sink_id = None
        self.logger = logging.getLogger("sendspin")

    def _run_pactl(self, args: list[str]) -> str:
        try:
            result = subprocess.run(["pactl"] + args, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as e:
            self.logger.info(f"pactl error: {e}")
        return ""

    def setup_routing(self) -> str:
        """
        Sets up the virtual sink and loopback to hardware sink.
        """
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
                "latency_msec=200",
            ]
            self.loopback_id = self._run_pactl(loop_cmd)
        return self.sink_name

    def get_hardware_sink(self) -> str:
        out = self._run_pactl(["list", "sinks", "short"])
        if out:
            for line in out.split("\n"):
                if any(x in line for x in ["alsa_output", "intel", "hdmi"]):
                    parts = line.split("\t")
                    if len(parts) > 1:
                        return parts[1]
        return "@DEFAULT_SINK@"

    def cleanup(self) -> None:
        if self.loopback_id:
            self._run_pactl(["unload-module", self.loopback_id])
            self.loopback_id = None
        if self.null_sink_id:
            self._run_pactl(["unload-module", self.null_sink_id])
            self.null_sink_id = None


class SyncPlaybackEngine:
    """
    Audio engine using pacat.
    """

    def __init__(self):
        self.process = None
        self.logger = logging.getLogger("sendspin")
        self._queue = asyncio.PriorityQueue()
        self._worker_task = None
        self._stats_task = None
        self._running = False

        # Configuration
        self.drop_threshold = 0.02
        self._volume = 50
        self._muted = False
        self.get_play_time = None
        self.is_synchronized = lambda: False
        self._PA_latency = 0.02
        self._alpha = 0.1  # For PA latency smoothing
        self._expected_next_timestamp = None
        self._correction_accumulator = 0.0
        self.loopback_latency = 200  # ms

        # Default Audio format parameters
        self._sample_rate = 44100
        self._channels = 2
        self._bytes_per_frame = 4
        self._last_sync_log_time = 0

        # for logging
        self._stat_chunks_received = 0
        self._stat_chunks_processed = 0
        self._stat_dropped_late = 0
        self._stat_silence_inserted = 0
        self._stat_net_gap_drops = 0

    # --- Volume and Mute Controls ---
    def set_volume(self, volume_int: int) -> None:
        self._volume = max(0, min(100, int(volume_int)))

    def set_mute(self, is_muted: bool) -> None:
        self._muted = bool(is_muted)

    def _apply_software_volume(self, data: bytes) -> bytes:
        if self._muted or self._volume == 0:
            return b"\x00" * len(data)
        if self._volume == 100:
            return data
        samples = np.frombuffer(data, dtype=np.int16).copy()
        amplitude = (self._volume / 100.0) ** 1.5
        return (samples * amplitude).astype(np.int16).tobytes()

    # --- Time Synchronization and Playback ---
    def set_time_provider(self, aiosendspin_client: SendspinClient) -> None:
        """pass aiosendspin_client"""
        self.aiosendspin_client = aiosendspin_client
        self.get_play_time = aiosendspin_client.compute_play_time
        self.get_server_time = aiosendspin_client.compute_server_time
        self.now_us = aiosendspin_client._now_us
        self.is_synchronized = aiosendspin_client.is_time_synchronized

    def start(self, rate: int, channels: int, bit_depth: int, target_sink: str) -> None:
        self._running = True

        # stop existing tasks if any
        if self._worker_task:
            self._worker_task.cancel()
        if self._stats_task:
            self._stats_task.cancel()

        # Reset logging counters
        self._stat_chunks_received = 0
        self._stat_chunks_processed = 0
        self._stat_dropped_late = 0
        self._stat_silence_inserted = 0
        self._stat_net_gap_drops = 0

        # set audio parameters
        self._sample_rate = rate
        self._channels = channels
        self._bytes_per_frame = (bit_depth // 8) * channels

        asyncio.create_task(self._async_init_process(rate, channels, bit_depth, target_sink))

    async def _async_init_process(self, rate: int, channels: int, bit_depth: int, target_sink: str) -> None:
        """Launches the pacat subprocess and starts the scheduler loop."""
        # Launch pacat subprocess
        cmd = [
            "pacat",
            "--playback",
            "--device",
            target_sink,
            "--prop",
            "application.name=Sendspin_Player",
            "--format",
            f"s{bit_depth}le",
            "--rate",
            str(rate),
            "--channels",
            str(channels),
            f"--latency-msec={self.loopback_latency}",
        ]
        self.process = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.PIPE)

        # Start scheduler and stats tasks
        self._worker_task = asyncio.create_task(self._scheduler_loop())
        self._stats_task = asyncio.create_task(self._debug_stats_loop())
        self.logger.info("Sync Engine: Subprocess launched.")
        asyncio.create_task(self._latency_monitor_loop())

        # --- Debug Logging Loop ---

    async def _debug_stats_loop(self):
        """Logs detailed audio and sync statistics every 5 seconds."""
        while self._running:
            await asyncio.sleep(20)

            # Gather metrics
            q_size = self._queue.qsize()
            lat_ms = self._PA_latency * 1000
            drift = self.aiosendspin_client._time_filter._drift
            is_sync = self.is_synchronized()
            corr_acc = self._correction_accumulator

            # Format message
            msg = (
                f"[Audio Stats] Sync: {'YES' if is_sync else 'NO'} | "
                f"Queue: {q_size} | "
                f"PA Latency: {lat_ms:.1f}ms | "
                f"Drift: {drift:.8f} | "
                f"Correction Acc: {corr_acc:.2f} | "
                f"Chunks: {self._stat_chunks_processed}/{self._stat_chunks_received} | "
                f"Drops (Late): {self._stat_dropped_late} | "
                f"Gaps (Silence): {self._stat_silence_inserted}"
            )
            self.logger.info(msg)

    # --- PA Latency Monitoring ---
    async def _query_pactl(self, args: list[str]) -> str:
        """Async helper to run pactl"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "pactl", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                return stdout.decode()
        except Exception as e:
            self.logger.error(f"pactl query error: {e}")
        return ""

    async def get_total_latency(self) -> float:
        """Queries PulseAudio for the combined latency of the player and loopback."""
        output = await self._query_pactl(["list", "sink-inputs"])
        if not output:
            return 0.02

        total_us = 0
        blocks = output.split("\n\n")

        for block in blocks:
            if "Sendspin_Player" in block or "module-loopback.c" in block:
                buffer_lat = 0
                sink_lat = 0

                for line in block.splitlines():
                    if "Buffer Latency:" in line:
                        parts = line.split()
                        if len(parts) >= 3:
                            buffer_lat = int(parts[2])
                    elif "Sink Latency:" in line:
                        parts = line.split()
                        if len(parts) >= 3:
                            sink_lat = int(parts[2])

                total_us += buffer_lat + sink_lat

        return total_us / 1_000_000.0

    async def _latency_monitor_loop(self) -> None:
        """Refreshes the pulseaudio latency value once per second uses exponential smoothing."""
        while self._running:
            new_raw_latency = await self.get_total_latency()
            if new_raw_latency:
                self._PA_latency = (self._alpha * new_raw_latency) + ((1.0 - self._alpha) * self._PA_latency)
                await asyncio.sleep(5.0)

    # --- Audio Scheduling ---
    def _apply_drift_correction(self, data: bytes) -> bytes:
        """Drops or inserts frames to match the server's clock speed."""
        audio_array = np.frombuffer(data, dtype=np.int16).reshape(-1, self._channels)
        num_frames = audio_array.shape[0]

        drift = self.aiosendspin_client._time_filter._drift
        self._correction_accumulator += num_frames * drift

        if self._correction_accumulator >= 1.0:
            audio_array = np.delete(audio_array, num_frames // 2, axis=0)
            self._correction_accumulator -= 1.0

        elif self._correction_accumulator <= -1.0:
            mid_idx = num_frames // 2
            duplicate_frame = audio_array[mid_idx : mid_idx + 1]
            audio_array = np.insert(audio_array, mid_idx, duplicate_frame, axis=0)
            self._correction_accumulator += 1.0

        return audio_array.tobytes()

    def play_chunk(self, server_timestamp_us: int, data: bytes) -> None:
        if not self._running:
            return

        self._stat_chunks_received += 1  # for logging

        if self._expected_next_timestamp is None:
            self._expected_next_timestamp = server_timestamp_us

        diff_us = self._expected_next_timestamp - server_timestamp_us

        def us_to_bytes(us: int) -> int:
            frames = (abs(us) * self._sample_rate) // 1_000_000
            return int(frames * self._bytes_per_frame)

        if diff_us > 0:
            trim_bytes = us_to_bytes(diff_us)
            if trim_bytes >= len(data):
                self.logger.warning("Dropping redundant packet (complete overlap)")
                return
            data = data[trim_bytes:]
            actual_start_us = self._expected_next_timestamp

        elif diff_us < -500:
            gap_bytes = us_to_bytes(diff_us)
            if gap_bytes < 1_000_000:
                silence = b"\x00" * gap_bytes
                self._queue.put_nowait((self._expected_next_timestamp, silence))
                self._stat_silence_inserted += 1  # for logging
            actual_start_us = server_timestamp_us
        else:
            actual_start_us = server_timestamp_us

        chunk_frames = len(data) // self._bytes_per_frame
        duration_us = (chunk_frames * 1_000_000) // self._sample_rate
        self._expected_next_timestamp = actual_start_us + duration_us

        self._queue.put_nowait((actual_start_us, data))

    async def _scheduler_loop(self) -> None:
        while self._running:
            try:
                scheduled_time_us, data = await self._queue.get()
                client_play_time_s = self.get_play_time(scheduled_time_us) / 1_000_000.0
                now = asyncio.get_event_loop().time()

                processed_data = self._apply_drift_correction(data)
                processed_data = self._apply_software_volume(processed_data)

                lead_time_s = self._PA_latency + (self.loopback_latency / 1000.0)
                wait_time = (client_play_time_s - now) - lead_time_s

                if wait_time > 0.001:
                    await asyncio.sleep(wait_time)

                if (asyncio.get_event_loop().time() - client_play_time_s) > self.drop_threshold:
                    self._stat_dropped_late += 1
                    self._queue.task_done()
                    continue

                if self.process and self.process.stdin:
                    try:
                        self.process.stdin.write(processed_data)
                        self._stat_chunks_processed += 1
                        await self.process.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        self.logger.error("Audio process died.")
                        break

                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Scheduler error: {e}")

    def stop(self) -> None:
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
        if self.process:
            self.process.terminate()

            self.process = None
