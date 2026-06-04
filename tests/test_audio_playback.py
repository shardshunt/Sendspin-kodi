#!/usr/bin/env python3
# ruff: noqa: E402, N802, I001
import os
import sys
import unittest
import logging
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
mock_audio = MagicMock()

# Inject mocks into sys.modules so imports in plugin code use them.
sys.modules["xbmc"] = mock_xbmc
sys.modules["xbmcaddon"] = mock_xbmcaddon
sys.modules["xbmcgui"] = mock_xbmcgui
sys.modules["xbmcvfs"] = mock_xbmcvfs
sys.modules["audio"] = mock_audio

# Configure xbmcvfs.translatePath to act as a no-op path translator
mock_xbmcvfs.translatePath.side_effect = lambda x: x


# Configure the Mock Addon class
class MockAddon:
    def __init__(self, *args, **kwargs):
        self._settings = {
            "control_url": "http://127.0.0.1:59999",
            "activate_visualisation_enabled": "true",
            "require_dummy_playback": "true",
            "docker_start_enabled": "false",
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


# Configure the Mock Playback Engine
class MockDockerPlaybackEngine:
    def __init__(self, *args, **kwargs):
        self.volume_scale = 0.3
        self.audio_device = "0"

    def configure_volume_sync(self, volume, muted, delay_ms):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def kodi_to_sendspin_volume(self, volume):
        return int(volume * 0.3)

    def sendspin_to_kodi_volume(self, volume):
        return int(volume / 0.3)


mock_audio.DockerPlaybackEngine.side_effect = MockDockerPlaybackEngine


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

            def abortRequested(self):
                # We have 17 states (indices 0 to 16).
                print(f"[TEST RUN] Monitor abortRequested checked. count={self.count}")
                if self.count >= 17:
                    return True
                self.count += 1
                return False

        # Apply Monitor mock
        mock_monitor_instance = MockMonitor()
        mock_xbmc.Monitor.side_effect = lambda: mock_monitor_instance

        # Mock the KodiManager to prevent real JSONRPC queries in tests
        controller = SendspinServiceController()
        controller.kodi = MagicMock()
        from unittest.mock import AsyncMock

        controller.kodi.cleanup = AsyncMock()
        controller.kodi.get_audio_output_device.return_value = "ALSA:default"
        controller.kodi.get_volume_state.return_value = {"volume": 100, "muted": False}
        controller.kodi.set_audio_output_device.return_value = True

        # Run the session loop!
        print("\n[TEST HARNESS] Starting run_session lifecycle test...")
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

        # 2. Release audio: should be called at pause (Iteration 9), stop (Iteration 15), and cleanup (finally block)
        self.assertEqual(
            mock_client.release_audio.call_count, 3, "Expected release_audio to be called exactly three times"
        )

        # 3. Verify Player operations:
        self.assertEqual(len(player.play_calls), 1, "Expected player.play to be called once")
        self.assertEqual(len(player.update_info_tag_calls), 1, "Expected player.updateInfoTag to be called once")
        self.assertEqual(
            len(player.stop_calls), 1, "Expected player.stop to be called once (only on full stop, not pause)"
        )

        # 4. Verify pause/play reset behavior (required on local audio acquisition to fetch FLAC headers):
        self.assertEqual(mock_client.pause.call_count, 2, "Expected pause to be called exactly twice")
        self.assertEqual(mock_client.play.call_count, 2, "Expected play to be called exactly twice")

        print("[TEST HARNESS] All assertions passed successfully!")


if __name__ == "__main__":
    unittest.main()
