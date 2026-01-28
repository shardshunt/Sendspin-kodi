import asyncio
import logging
import subprocess

import numpy as np


class AudioRouter:
    """
    Manages PulseAudio virtual sinks and routing for the Sendspin service.

    Creates a null sink and links it to the hardware sink via a loopback module.
    """

    def __init__(self):
        """Initializes the router state and logging."""
        self.sink_name = "Sendspin_Sink"
        self.loopback_id = None
        self.null_sink_id = None
        self.logger = logging.getLogger("sendspin")

    def _run_pactl(self, args):
        """
        Executes a PulseAudio control (pactl) command.
        """
        try:
            result = subprocess.run(["pactl"] + args, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as e:
            self.logger.info(f"pactl error: {e}")
        return None

    def setup_routing(self):
        """
        Sets up the virtual audio routing path in PulseAudio.
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
                "latency_msec=10",
            ]
            self.loopback_id = self._run_pactl(loop_cmd)
            self.logger.info(f"Routing {self.sink_name} -> {hw_sink}")
        return self.sink_name

    def get_hardware_sink(self):
        """
        Identifies the primary physical audio output device.
        """
        out = self._run_pactl(["list", "sinks", "short"])
        if out:
            for line in out.split("\n"):
                if "alsa_output" in line:
                    return line.split("\t")[1]
        return "@DEFAULT_SINK@"

    def cleanup(self):
        """
        Removes the virtual modules from PulseAudio to restore system state.
        """
        if self.loopback_id:
            self._run_pactl(["unload-module", self.loopback_id])
            self.loopback_id = None
        if self.null_sink_id:
            self._run_pactl(["unload-module", self.null_sink_id])
            self.null_sink_id = None


class SyncPlaybackEngine:
    """
    Handles time-synchronized PCM playback with software volume scaling.
    """

    def __init__(self):
        """Initializes the engine, audio queue, and software volume state."""
        self.process = None
        self.logger = logging.getLogger("sendspin")
        self._queue = asyncio.PriorityQueue()
        self._worker_task = None
        self._running = False
        self.target_latency = 0.02

        # Software Volume State
        self._volume = 100
        self._muted = False

    def set_volume(self, volume_int):
        """
        Updates the internal software volume level.
        """
        self._volume = max(0, min(100, int(volume_int)))

    def set_mute(self, is_muted):
        """
        Updates the software mute state.
        """
        self._muted = bool(is_muted)

    def get_volume(self):
        """
        Returns the current software volume level.
        """
        return self._volume

    def _apply_software_volume(self, data: bytes) -> bytes:
        """
        Scales PCM samples using a power curve for natural volume control.

        Uses a 1.5 power exponent to better match human hearing perception
        of loudness.
        """
        if self._muted or self._volume == 0:
            return b"\x00" * len(data)

        if self._volume == 100:
            return data

        # Convert bytes to 16-bit signed integers for scaling
        samples = np.frombuffer(data, dtype=np.int16).copy()

        # Calculate amplitude based on the Sendspin volume curve
        amplitude = (self._volume / 100.0) ** 1.5

        # Multiply samples by amplitude and cast back to int16
        samples = (samples * amplitude).astype(np.int16)
        return samples.tobytes()

    def set_time_provider(self, time_provider_func, sync_check_func):
        """
        Links the engine to the client's time synchronization filter.

        Args:
            time_provider_func (callable): Pass AIOSENDSPIN compute_play_time function here.
            sync_check_func (callable): Pass AIOSENDSPIN is_time_synchronized function here.
        """
        self.get_play_time = time_provider_func
        self.is_synchronized = sync_check_func

    def start(self, rate, channels, bit_depth, target_sink):
        """
        Launches the 'pacat' process and starts the scheduler loop.

        Args:
            rate (int): Sample rate in Hz.
            channels (int): Number of audio channels.
            bit_depth (int): Bits per sample.
            target_sink (str): The PulseAudio sink name to play through.
        """
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
            "--latency-msec=10",
        ]
        self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self._running = True
        self._worker_task = asyncio.create_task(self._scheduler_loop())

    def play_chunk(self, server_timestamp_us, data):
        """
        Calculates the local playback time and queues an audio chunk.

        Args:
            server_timestamp_us (int): Server-side timestamp for this chunk.
            data (bytes): PCM audio data.
        """
        if not self.is_synchronized:
            return
        local_target_us = self.get_play_time(server_timestamp_us)
        scheduled_time = (local_target_us / 1_000_000.0) + self.target_latency
        self._queue.put_nowait((scheduled_time, data))

    async def _scheduler_loop(self):
        """
        Asynchronous loop that releases audio chunks at their scheduled times.

        Applies software volume scaling immediately before writing to the
        process pipe.
        """
        while self._running:
            try:
                scheduled_time, data = await self._queue.get()
                wait_time = scheduled_time - asyncio.get_event_loop().time()
                if wait_time > 0:
                    await asyncio.sleep(wait_time)

                if self.process and self.process.stdin:
                    # Scaling is applied here to ensure volume changes take
                    # effect as soon as possible on the next chunk.
                    processed_data = self._apply_software_volume(data)
                    self.process.stdin.write(processed_data)
                    self.process.stdin.flush()
                self._queue.task_done()
            except Exception as e:
                self.logger.error(f"Scheduler error: {e}")

    def stop(self):
        """
        Terminates the playback process and cancels the scheduler task.
        """
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
        if self.process:
            self.process.terminate()
            self.process = None
