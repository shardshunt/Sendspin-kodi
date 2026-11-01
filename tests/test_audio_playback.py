#!/usr/bin/env python3
# ruff: noqa: E402, N802, I001
import asyncio
import os
import sys
import unittest
import logging
import time
from unittest.mock import MagicMock, patch

# Configure standard logging to show detailed output and catch hidden errors
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Ensure the plugin directory is in the path so python can import its modules.
PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "service.sendspin"))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

# --- Define Mocks for Kodi Modules ---
mock_xbmc = MagicMock()
mock_xbmcaddon = MagicMock()
mock_xbmcgui = MagicMock()
mock_xbmcvfs = MagicMock()

# Inject mocks into sys.modules so imports in plugin code use them.
sys.modules["xbmc"] = mock_xbmc
sys.modules["xbmcaddon"] = mock_xbmcaddon
sys.modules["xbmcgui"] = mock_xbmcgui
sys.modules["xbmcvfs"] = mock_xbmcvfs

# Configure xbmcvfs.translatePath to act as a no-op path translator
mock_xbmcvfs.translatePath.side_effect = lambda x: x


# Configure the Mock Addon class
class MockAddon:
    def __init__(self, *args, **kwargs):
        self._settings = {
            "control_url": "http://127.0.0.1:59999",
            "activate_visualisation_enabled": "true",
            "require_dummy_playback": "true",
        }

    def getSetting(self, name):
        return self._settings.get(name, "")

    def getAddonInfo(self, name):
        if name == "path":
            return "/dummy/addon/path"
        return ""


mock_xbmcaddon.Addon.side_effect = MockAddon


# Configure the Mock ListItem and Tag classes
class MockMusicInfoTag:
    def __init__(self):
        self._title = ""
        self._artist = ""
        self._album = ""
        self._duration = 0
        self._media_type = ""

    def setTitle(self, title):
        self._title = title

    def setArtist(self, artist):
        self._artist = artist

    def setAlbum(self, album):
        self._album = album

    def setDuration(self, duration):
        self._duration = duration

    def setMediaType(self, media_type):
        self._media_type = media_type

    def getTitle(self):
        return self._title

    def getArtist(self):
        return self._artist

    def getAlbum(self):
        return self._album


class MockListItem:
    def __init__(self, label=""):
        self.label = label
        self.tag = MockMusicInfoTag()
        self.info = {}
        self.art = {}

    def getMusicInfoTag(self):
        return self.tag

    def setLabel(self, label):
        self.label = label

    def setInfo(self, media_type, info):
        self.info = info

    def setArt(self, art):
        self.art = art


mock_xbmcgui.ListItem.side_effect = MockListItem


# Configure the Mock Player
class MockKodiPlayer:
    last_instance = None

    def __init__(self, *args, **kwargs):
        MockKodiPlayer.last_instance = self
        self._is_playing = False
        self._time = 0.0
        self._paused = False
        self.play_calls = []
        self.stop_calls = []
        self.update_info_tag_calls = []
        self.tag = MockMusicInfoTag()

    def getMusicInfoTag(self):
        return self.tag

    def play(self, item, listitem=None, windowed=False, startpos=-1):
        self._is_playing = True
        self.play_calls.append((item, listitem))

    def updateInfoTag(self, listitem):
        self.update_info_tag_calls.append(listitem)

    def stop(self):
        self._is_playing = False
        self.stop_calls.append(True)

    def isPlaying(self):
        return self._is_playing

    def getPlayingFile(self):
        if self.play_calls:
            return self.play_calls[-1][0]
        return ""

    def isPlayingVideo(self):
        return False

    def getTime(self):
        return self._time

    def seekTime(self, seconds):
        self._time = seconds

    def getTotalTime(self):
        return 200.0

    def pause(self):
        self._paused = not self._paused


mock_xbmc.Player = MockKodiPlayer


# Mock condition visibility based on mock player pause state
def get_cond_visibility(cond):
    if cond == "Player.Paused" and MockKodiPlayer.last_instance:
        return MockKodiPlayer.last_instance._paused
    return False


mock_xbmc.getCondVisibility.side_effect = get_cond_visibility
mock_xbmc.LOGINFO = 1
mock_xbmc.log = MagicMock()


# --- Now Import the actual modules we want to test ---
import session
from service import SendspinServiceController


class TestAudioReleaseAcquireLifecycle(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Reset the mock player state before each test
        MockKodiPlayer.last_instance = None
        mock_xbmc.log.reset_mock()

    @patch("service.SendspinControlClient")
    @patch("asyncio.sleep")
    async def test_audio_acquire_release_lifecycle(self, mock_sleep, mock_client_class):
        # Prevent actual sleeping in unit test to run instantly by using an async no-op function
        async def async_noop(*args, **kwargs):
            pass

        mock_sleep.side_effect = async_noop

        # Setup the mock Sendspin client
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.acquire_audio.return_value = True
        mock_client.release_audio.return_value = True
        mock_client.set_delay.return_value = True
        mock_client.audio_status.return_value = {"released": True}

        # Sequence of states to simulate throughout the loop iterations
        states = [
            # Iteration 0: Initial state - Idle (No active playback, speed=0, no track)
            {
                "track": {},
                "playback": {"speed": 0, "position": 0, "duration": 0},
                "volume": {"volume": 30, "muted": False},
                "audio": {"released": True},
            },
            # Iteration 1: Playback starts but track info is not yet present (track: {}, speed=1)
            {
                "track": {},
                "playback": {"speed": 1, "position": 0, "duration": 0},
                "volume": {"volume": 30, "muted": False},
                "audio": {"released": False},
            },
            # Iteration 2: Playback continues and track info arrives (active playback, speed=1)
            {
                "track": {"title": "Test Audio"},
                "playback": {"speed": 1, "position": 2.0, "duration": 180},
                "volume": {"volume": 30, "muted": False},
                "audio": {"released": False},
            },
            # Iteration 3: Artist and album arrive after the title, without a title change.
            {
                "track": {"title": "Test Audio", "artist": "Mock Artist", "album": "Mock Album"},
                "playback": {"speed": 1, "position": 2.0, "duration": 180},
                "volume": {"volume": 30, "muted": False},
                "audio": {"released": False},
            },
            # Iteration 3: Brief pause tick 1 (speed=0) -> should NOT trigger release
            {
                "track": {"title": "Test Audio", "artist": "Mock Artist", "album": "Mock Album"},
                "playback": {"speed": 0, "position": 2.0, "duration": 180},
                "volume": {"volume": 30, "muted": False},
                "audio": {"released": True},
            },
            # Iteration 4: Playback Resumed (speed=1) -> should NOT trigger acquire (already acquired)
            {
                "track": {"title": "Test Audio", "artist": "Mock Artist", "album": "Mock Album"},
                "playback": {"speed": 1, "position": 3.0, "duration": 180},
                "volume": {"volume": 30, "muted": False},
                "audio": {"released": False},
            },
            # Iteration 5: Long pause tick 1 (speed=0)
            {
                "track": {"title": "Test Audio", "artist": "Mock Artist", "album": "Mock Album"},
                "playback": {"speed": 0, "position": 3.0, "duration": 180},
                "volume": {"volume": 30, "muted": False},
                "audio": {"released": True},
            },
            # Iteration 6: Long pause tick 2
            {
                "track": {"title": "Test Audio", "artist": "Mock Artist", "album": "Mock Album"},
                "playback": {"speed": 0, "position": 3.0, "duration": 180},
                "volume": {"volume": 30, "muted": False},
                "audio": {"released": True},
            },
            # Iteration 7: Long pause tick 3
            {
                "track": {"title": "Test Audio", "artist": "Mock Artist", "album": "Mock Album"},
                "playback": {"speed": 0, "position": 3.0, "duration": 180},
                "volume": {"volume": 30, "muted": False},
                "audio": {"released": True},
            },
            # Iteration 8: Long pause tick 4
            {
                "track": {"title": "Test Audio", "artist": "Mock Artist", "album": "Mock Album"},
                "playback": {"speed": 0, "position": 3.0, "duration": 180},
                "volume": {"volume": 30, "muted": False},
                "audio": {"released": True},
            },
            # Iteration 9: Long pause tick 5 -> triggers release
            {
                "track": {"title": "Test Audio", "artist": "Mock Artist", "album": "Mock Album"},
                "playback": {"speed": 0, "position": 3.0, "duration": 180},
                "volume": {"volume": 30, "muted": False},
                "audio": {"released": True},
            },
            # Iteration 10: Playback Resumed -> triggers acquire
            {
                "track": {"title": "Test Audio", "artist": "Mock Artist", "album": "Mock Album"},
                "playback": {"speed": 1, "position": 5.0, "duration": 180},
                "volume": {"volume": 30, "muted": False},
                "audio": {"released": False},
            },
            # Iteration 11: Playback Stopped / Finished - Tick 1 of grace period
            {
                "track": {},
                "playback": {"speed": 0, "position": 0, "duration": 0},
                "volume": {"volume": 30, "muted": False},
                "audio": {"released": True},
            },
            # Tick 2 of grace period (Iteration 12)
            {
                "track": {},
                "playback": {"speed": 0, "position": 0, "duration": 0},
                "volume": {"volume": 30, "muted": False},
                "audio": {"released": True},
            },
            # Tick 3 of grace period (Iteration 13)
            {
                "track": {},
                "playback": {"speed": 0, "position": 0, "duration": 0},
                "volume": {"volume": 30, "muted": False},
                "audio": {"released": True},
            },
            # Tick 4 of grace period (Iteration 14)
            {
                "track": {},
                "playback": {"speed": 0, "position": 0, "duration": 0},
                "volume": {"volume": 30, "muted": False},
                "audio": {"released": True},
            },
            # Tick 5 of grace period - triggers cleanup and stop (Iteration 15)
            {
                "track": {},
                "playback": {"speed": 0, "position": 0, "duration": 0},
                "volume": {"volume": 30, "muted": False},
                "audio": {"released": True},
            },
            # Iteration 16: Idle (with audio_claimed=False) -> triggers player.stop()
            {
                "track": {},
                "playback": {"speed": 0, "position": 0, "duration": 0},
                "volume": {"volume": 30, "muted": False},
                "audio": {"released": True},
            },
        ]

        state_iter = iter(states)

        # Mock the controller's get_sendspin_state to feed the planned states sequentially
        def get_state_side_effect():
            try:
                s = next(state_iter)
                print(
                    f"[TEST RUN] get_sendspin_state returning: track={s.get('track')}, speed={s.get('playback', {}).get('speed')}, released={s.get('audio', {}).get('released')}"
                )
                return s
            except StopIteration:
                # Return empty state if we run past the defined states
                return {}

        mock_client.get_state.side_effect = get_state_side_effect

        # Mock Monitor abortRequested to terminate the loop after we run all states
        class MockMonitor:
            def __init__(self):
                self.count = 0
                self.wake_event_triggered = False
                self.audio_device_setting_changed = False

            def abortRequested(self):
                # We have 18 states (indices 0 to 17).
                print(f"[TEST RUN] Monitor abortRequested checked. count={self.count}")
                # Trigger a wake event while the Kodi-side session is active.
                if self.count == 6:
                    self.wake_event_triggered = True
                if self.count >= 18:
                    return True
                self.count += 1
                return False

        # Apply Monitor mock
        mock_monitor_instance = MockMonitor()
        mock_xbmc.Monitor.side_effect = lambda: mock_monitor_instance

        # Patch SendspinMonitor in session to return our mock monitor instance
        monitor_patcher = patch("session.SendspinMonitor", return_value=mock_monitor_instance)
        monitor_patcher.start()
        self.addCleanup(monitor_patcher.stop)

        # Mock the KodiManager to prevent real JSONRPC queries in tests
        controller = SendspinServiceController()
        controller.kodi = MagicMock()
        from unittest.mock import AsyncMock

        controller.kodi.cleanup = AsyncMock()
        controller.kodi.get_audio_output_device.return_value = "ALSA:default"
        controller.kodi.get_volume_state.return_value = {"volume": 100, "muted": False}
        controller.kodi.set_audio_output_device.return_value = True

        # Keep this unit test independent of the host's PulseAudio/PipeWire
        # session. The lifecycle behavior is covered here; the helpers that
        # call pactl are exercised by Kodi/device integration tests instead.
        controller.route_kodi_to_null_sink = MagicMock()
        controller.restore_kodi_stream_routing = MagicMock()
        controller.set_default_sink_to_null = MagicMock()
        controller.restore_default_sink = MagicMock()
        controller.suspend_physical_sinks = MagicMock()
        controller.is_kodi_holding_pcm = MagicMock(return_value=False)

        # Run the session loop!
        print("\n[TEST HARNESS] Starting run_session lifecycle test...")

        async def run_in_executor_immediately(_loop, _executor, function, *args):
            return function(*args)

        with patch.object(asyncio.BaseEventLoop, "run_in_executor", run_in_executor_immediately):
            await session.run_session(controller)
        print("[TEST HARNESS] run_session lifecycle finished successfully.")

        player = MockKodiPlayer.last_instance
        self.assertIsNotNone(player, "Player should have been instantiated")

        print(f"[TEST DIAGNOSTIC] mock_client.acquire_audio calls: {mock_client.acquire_audio.call_args_list}")
        print(f"[TEST DIAGNOSTIC] mock_client.release_audio calls: {mock_client.release_audio.call_args_list}")
        print(f"[TEST DIAGNOSTIC] player play calls: {player.play_calls}")
        print(f"[TEST DIAGNOSTIC] player stop calls: {player.stop_calls}")

        # --- Assertions ---

        # 1. Acquire audio: should be called twice (Iteration 1: start, Iteration 10: resume after long pause)
        self.assertEqual(mock_client.acquire_audio.call_count, 2, "Expected acquire_audio to be called exactly twice")

        # 2. Release audio only on the pause/stop handoffs; cleanup has no claimed audio left.
        self.assertEqual(mock_client.release_audio.call_count, 2, "Expected release_audio to be called exactly twice")

        # 3. Verify Player operations:
        self.assertEqual(len(player.play_calls), 1, "Expected player.play to be called once")
        self.assertEqual(len(player.update_info_tag_calls), 4, "Expected startup, enrichment, and reset updates")
        self.assertEqual(player.update_info_tag_calls[0].tag.getArtist(), "Mock Artist")
        self.assertEqual(
            len(player.stop_calls), 1, "Expected player.stop to be called once (only on full stop, not pause)"
        )

        # 4. Verify pause/play reset behavior (required on local audio acquisition to fetch FLAC headers):
        self.assertEqual(mock_client.pause.call_count, 2, "Expected pause to be called exactly twice")
        self.assertEqual(mock_client.play.call_count, 2, "Expected play to be called exactly twice")

        print("[TEST HARNESS] All assertions passed successfully!")


class TestNativeBackendSupervisor(unittest.TestCase):
    def test_kodi_pause_waits_for_sendspin_state_and_reports_rejection(self):
        controller = SendspinServiceController()
        controller.control = MagicMock()
        controller.control.pause.return_value = True

        self.assertTrue(controller.handle_kodi_pause())
        self.assertTrue(controller.kodi_pause_is_pending())
        self.assertTrue(controller.handle_kodi_pause())
        controller.control.pause.assert_called_once_with()

        controller._kodi_pause_pending_until = 0.0
        controller.control.pause.return_value = False
        self.assertFalse(controller.handle_kodi_pause())
        self.assertFalse(controller.kodi_pause_is_pending())

    def test_kodi_resume_clears_pending_pause(self):
        controller = SendspinServiceController()
        controller.control = MagicMock()
        controller._kodi_pause_pending_until = time.monotonic() + 5.0
        controller.control.play.return_value = True

        self.assertTrue(controller.handle_kodi_resume())
        self.assertFalse(controller.kodi_pause_is_pending())

    def test_kodi_pause_state_transition_forwards_once_and_resumes(self):
        controller = MagicMock()

        paused = session.forward_kodi_pause_state_change(controller, True, True, False)
        self.assertTrue(paused)
        controller.handle_kodi_pause.assert_called_once_with()

        paused = session.forward_kodi_pause_state_change(controller, True, True, paused)
        self.assertTrue(paused)
        controller.handle_kodi_pause.assert_called_once_with()

        paused = session.forward_kodi_pause_state_change(controller, True, False, paused)
        self.assertFalse(paused)
        controller.handle_kodi_resume.assert_called_once_with()

        self.assertFalse(session.forward_kodi_pause_state_change(controller, False, False, paused))

    def test_architecture_aliases(self):
        self.assertEqual(SendspinServiceController._architecture_name("x86_64"), "x86_64")
        self.assertEqual(SendspinServiceController._architecture_name("amd64"), "x86_64")
        self.assertEqual(SendspinServiceController._architecture_name("aarch64"), "aarch64")
        self.assertEqual(SendspinServiceController._architecture_name("arm64"), "aarch64")
        self.assertEqual(SendspinServiceController._architecture_name("armv7l"), "armv7")

    @patch("service.os.path.isfile")
    @patch("service.platform.machine", return_value="aarch64")
    def test_architecture_backend_precedes_legacy_backend(self, _machine, mock_isfile):
        mock_isfile.side_effect = lambda path: path.endswith("aarch64/sendspin-kodi-backend")

        path = SendspinServiceController._default_backend_path("/dummy/addon")

        self.assertEqual(path, "/dummy/addon/resources/bin/aarch64/sendspin-kodi-backend")

    @patch("service.os.path.isfile", return_value=False)
    @patch("service.platform.machine", return_value="armv7l")
    def test_architecture_backend_falls_back_to_legacy_path(self, _machine, _isfile):
        path = SendspinServiceController._default_backend_path("/dummy/addon")

        self.assertEqual(path, "/dummy/addon/resources/bin/sendspin-kodi-backend")

    @patch("service.threading.Thread")
    @patch("service.subprocess.Popen")
    @patch("service.SendspinControlClient")
    def test_start_passes_kodi_configuration_and_waits_for_socket(self, mock_client_class, mock_popen, mock_thread):
        controller = SendspinServiceController()
        controller.kodi = MagicMock()
        controller.backend_path = "/native/sendspin-kodi-backend"
        controller.native_backend_available = True
        controller.server_url = "ws://sendspin.example:8927/sendspin"
        controller.player_name = "Kitchen"
        controller.audio_device_id = "hw:1,10"
        controller.state_file = "/profile/sendspin/state.json"
        controller.control_socket_path = "/profile/sendspin/control.sock"
        controller.kodi.get_volume_state.return_value = {"volume": 80, "muted": True}
        controller._get_delay_ms = MagicMock(return_value=250)

        process = MagicMock()
        process.poll.return_value = None
        mock_popen.return_value = process
        client = mock_client_class.return_value
        client.get_state.return_value = {"track": {}}

        self.assertTrue(controller._start_native_backend())

        command = mock_popen.call_args.args[0]
        self.assertEqual(command[0], controller.backend_path)
        self.assertEqual(mock_popen.call_args.kwargs["env"]["SENDSPIN_AUDIO_DEVICE"], "hw:1,10")
        self.assertIn("--server", command)
        self.assertIn("ws://sendspin.example:8927/sendspin", command)
        client.set_volume.assert_called_once_with(24, True)
        client.set_delay.assert_called_once_with(250)
        mock_thread.return_value.start.assert_called_once_with()

        controller._stop_native_backend()
        process.terminate.assert_called_once_with()

    @patch("service.threading.Thread")
    @patch("service.subprocess.Popen")
    @patch("service.SendspinControlClient")
    def test_start_reports_child_exit_before_socket_readiness(self, mock_client_class, mock_popen, mock_thread):
        controller = SendspinServiceController()
        controller.kodi = MagicMock()
        controller.backend_path = "/native/sendspin-kodi-backend"
        controller.native_backend_available = True

        process = MagicMock()
        process.poll.return_value = 2
        process.returncode = 2
        mock_popen.return_value = process

        self.assertFalse(controller._start_native_backend())
        self.assertGreater(controller._native_backend_retry_at, time.monotonic())
        mock_client_class.return_value.get_state.assert_not_called()


class TestAudioDeviceMapping(unittest.TestCase):
    @patch("subprocess.run")
    def test_get_audio_device_id(self, mock_run):
        # Mock aplay -l output
        mock_aplay_output = (
            "**** List of PLAYBACK Hardware Devices ****\n"
            "card 0: PCH [HDA Intel PCH], device 0: ALC3246 Analog [ALC3246 Analog]\n"
            "  Subdevices: 1/1\n"
            "  Subdevice #0: subdevice #0\n"
            "card 0: PCH [HDA Intel PCH], device 3: HDMI 0 [HDMI 0]\n"
            "  Subdevices: 1/1\n"
            "  Subdevice #0: subdevice #0\n"
            "card 0: PCH [HDA Intel PCH], device 7: HDMI 1 [HDMI 1]\n"
            "  Subdevices: 1/1\n"
            "  Subdevice #0: subdevice #0\n"
            "card 0: PCH [HDA Intel PCH], device 8: HDMI 2 [HDMI 2]\n"
            "  Subdevices: 1/1\n"
            "  Subdevice #0: subdevice #0\n"
        )
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = mock_aplay_output
        mock_run.return_value = mock_res

        with patch("xbmcaddon.Addon") as mock_addon_cls:
            mock_addon = MagicMock()
            mock_addon.getSetting.side_effect = lambda name: "8" if name == "fallback_audio_device" else ""
            mock_addon_cls.return_value = mock_addon

            from service import SendspinServiceController

            controller = SendspinServiceController()

            # Test various device strings. An out-of-range fallback remains
            # unchanged so existing installations retain their configured
            # value until the hardware list is available.
            self.assertEqual(controller._get_audio_device_id("Default"), "8")
            self.assertEqual(controller._get_audio_device_id("default"), "8")
            self.assertEqual(controller._get_audio_device_id("PIPEWIRE:Default|Default Output Device (PIPEWIRE)"), "8")
            self.assertEqual(controller._get_audio_device_id("ALSA:default"), "8")
            self.assertEqual(controller._get_audio_device_id("ALSA:sysdefault"), "8")

            # A numeric fallback is resolved to a stable raw ALSA identity
            # when that position exists in the current hardware list.
            mock_addon.getSetting.side_effect = lambda name: "1" if name == "fallback_audio_device" else ""
            self.assertEqual(controller._get_audio_device_id("ALSA:default"), "hw:0,3")
            mock_addon.getSetting.side_effect = lambda name: "8" if name == "fallback_audio_device" else ""

            # 2. Specific device matching by card and device returns a stable
            # raw ALSA identity rather than a reorderable global ordinal.
            self.assertEqual(controller._get_audio_device_id("ALSA:CARD=PCH,DEV=0"), "hw:0,0")
            self.assertEqual(controller._get_audio_device_id("ALSA:CARD=PCH,DEV=3"), "hw:0,3")
            self.assertEqual(controller._get_audio_device_id("ALSA:CARD=PCH,DEV=7"), "hw:0,7")
            self.assertEqual(controller._get_audio_device_id("ALSA:CARD=PCH,DEV=8"), "hw:0,8")

            # The raw identity remains the same regardless of where the
            # device appears in the global enumeration.
            reordered = (
                mock_aplay_output.replace(
                    "card 0: PCH [HDA Intel PCH], device 0: ALC3246 Analog [ALC3246 Analog]\\n",
                    "",
                )
                + "card 0: PCH [HDA Intel PCH], device 0: ALC3246 Analog [ALC3246 Analog]\\n"
            )
            mock_run.return_value.stdout = reordered
            self.assertEqual(controller._get_audio_device_id("ALSA:CARD=PCH,DEV=3"), "hw:0,3")

            # 3. Fallback cases
            # Invalid ALSA string without card/dev should return fallback (default fallback is "8")
            self.assertEqual(controller._get_audio_device_id("ALSA:invalid"), "8")
            # Non-default, non-ALSA string should return fallback
            self.assertEqual(controller._get_audio_device_id("other_device"), "8")


if __name__ == "__main__":
    unittest.main()
